"""codecheck 的工具适配层：源文件发现、clang-format/clang-tidy 调用与诊断解析。

把"外部工具的输出"和"skill 的 JSON 契约"分开，便于单测：
- 入口脚本只负责参数解析/状态写入/结果包络；
- 本模块的函数都可注入 `run_process`，测试不需要安装 clang 工具链。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any


SOURCE_SUFFIXES = {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh"}
DEFAULT_EXCLUDES = {
    ".git",
    ".svn",
    ".hg",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "DebugConfig",
    "Listings",
    "Objects",
    "Output",
}
SEVERITY_RANK = {"info": 0, "warning": 1, "error": 2}
TIDY_LINE = re.compile(
    r"^(?P<file>(?:[A-Za-z]:)?[^:]+):(?P<line>\d+):(?P<col>\d+): "
    r"(?P<severity>error|warning|note|remark): (?P<message>.*?)(?: \[(?P<rule>[^\]]+)\])?$"
)


class ToolRunError(RuntimeError):
    """外部分析器无法启动/超时/异常退出：必须按失败处理，不能当作空结果放行。"""

    def __init__(self, tool: str, message: str):
        super().__init__(message)
        self.tool = tool


def run_process(command: list[str], timeout: int) -> subprocess.CompletedProcess:
    """执行外部工具；超时抛 subprocess.TimeoutExpired，由调用方转成 ToolRunError。"""
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def workspace_relative(path: Path, workspace: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(workspace).as_posix()
    except ValueError:
        return resolved.as_posix()


def resolve_workspace_path(value: str | None, workspace: Path) -> str:
    """把工程配置里的相对路径按 workspace 解析（而不是按当前工作目录）。"""
    if value in (None, ""):
        return ""
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = workspace / path
    return str(path.resolve())


def discover_sources(
    workspace: Path,
    *,
    source_roots: list[str] | None = None,
    exclude_names: list[str] | None = None,
    exclude_paths: list[str] | None = None,
    max_files: int = 0,
) -> tuple[list[Path], list[str]]:
    """按源根/排除规则发现 C/C++ 文件，返回 (files, notes)。"""
    roots = [root for root in (source_roots or []) if root not in (None, "")] or ["."]
    excluded_names = {str(name).casefold() for name in (exclude_names or [])} | {
        name.casefold() for name in DEFAULT_EXCLUDES
    }
    excluded_prefixes = [
        resolve_workspace_path(str(path), workspace).replace("\\", "/").casefold()
        for path in (exclude_paths or [])
        if path not in (None, "")
    ]
    notes: list[str] = []
    files: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        base = Path(root).expanduser()
        if not base.is_absolute():
            base = workspace / base
        base = base.resolve()
        if base.is_file():
            candidates = [base]
        elif base.is_dir():
            candidates = []
            for current, dirnames, filenames in os.walk(base):
                current_path = Path(current)
                kept_dirs: list[str] = []
                for dirname in dirnames:
                    child = (current_path / dirname).resolve()
                    child_posix = child.as_posix().casefold()
                    if dirname.casefold() in excluded_names:
                        continue
                    if any(
                        child_posix == prefix or child_posix.startswith(prefix + "/")
                        for prefix in excluded_prefixes
                    ):
                        continue
                    kept_dirs.append(dirname)
                dirnames[:] = kept_dirs
                for filename in filenames:
                    path = current_path / filename
                    if path.suffix.lower() in SOURCE_SUFFIXES:
                        candidates.append(path)
        else:
            notes.append(f"源根不存在，已跳过：{root}")
            continue
        for path in candidates:
            resolved = path.resolve()
            posix = resolved.as_posix().casefold()
            parts = resolved.relative_to(workspace).parts if resolved.is_relative_to(workspace) else ()
            if any(part.casefold() in excluded_names for part in parts):
                continue
            if any(posix == prefix or posix.startswith(prefix + "/") for prefix in excluded_prefixes):
                continue
            if posix in seen:
                continue
            seen.add(posix)
            files.append(resolved)
    files.sort(key=lambda item: item.as_posix())
    if max_files and len(files) > max_files:
        notes.append(f"源文件数 {len(files)} 超过 max_files={max_files}，仅检查前 {max_files} 个")
        files = files[:max_files]
    return files, notes


def format_findings(
    files: list[Path],
    clang_format: str,
    style_file: str,
    workspace: Path,
    timeout: int,
) -> tuple[list[dict], list[str]]:
    """逐个文件执行 clang-format --dry-run --Werror，不符合即为一条 finding。"""
    findings: list[dict] = []
    notes: list[str] = []
    style_arg = f"--style=file:{style_file}" if style_file else "--style=file"
    for path in files:
        command = [clang_format, "--dry-run", "--Werror", style_arg, str(path)]
        try:
            process = run_process(command, timeout)
        except subprocess.TimeoutExpired as exc:
            raise ToolRunError("clang-format", f"clang-format 超时（{timeout}s）：{path.name}") from exc
        except OSError as exc:
            raise ToolRunError("clang-format", f"clang-format 无法执行：{exc}") from exc
        if process.returncode == 0:
            continue
        output = (process.stderr or process.stdout or "").strip().splitlines()
        detail = output[0] if output else "文件不符合格式配置"
        findings.append(
            {
                "file": workspace_relative(path, workspace),
                "line": 1,
                "column": 1,
                "severity": "warning",
                "category": "format",
                "rule": "clang-format",
                "message": detail,
                "tool": "clang-format",
            }
        )
    return findings, notes


def parse_tidy_output(text: str, workspace: Path) -> list[dict]:
    """解析 clang-tidy 文本诊断行，归一化为 findings。"""
    findings: list[dict] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("Found compiler error", "Error while processing")):
            continue
        match = TIDY_LINE.match(line)
        if not match:
            continue
        severity = match.group("severity")
        if severity in {"note", "remark"}:
            continue
        file_value = match.group("file")
        path = Path(file_value)
        resolved = path if path.is_absolute() else (workspace / path)
        findings.append(
            {
                "file": workspace_relative(resolved, workspace),
                "line": int(match.group("line")),
                "column": int(match.group("col")),
                "severity": "error" if severity == "error" else "warning",
                "category": "tidy",
                "rule": (match.group("rule") or "clang-tidy").strip(),
                "message": match.group("message").strip(),
                "tool": "clang-tidy",
            }
        )
    return findings


def dedupe_findings(findings: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    unique: list[dict] = []
    for finding in findings:
        key = (
            finding["file"],
            finding["line"],
            finding["column"],
            finding["rule"],
            finding["message"],
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(finding)
    unique.sort(key=lambda item: (item["file"], item["line"], item["column"], item["rule"]))
    return unique


def tidy_findings(
    files: list[Path],
    clang_tidy: str,
    *,
    tidy_config_file: str,
    compile_db: str,
    include_dirs: list[str],
    defines: list[str],
    workspace: Path,
    timeout: int,
) -> tuple[list[dict], list[str]]:
    """批量执行 clang-tidy；优先 compile_commands.json，否则退化为 -I/-D。"""
    notes: list[str] = []
    command = [clang_tidy, *[str(path) for path in files], "--quiet"]
    if tidy_config_file:
        command.extend(["--config-file", tidy_config_file])
    if compile_db:
        command.extend(["-p", compile_db])
    else:
        compiler_args: list[str] = []
        for directory in include_dirs:
            resolved_directory = resolve_workspace_path(str(directory), workspace)
            if resolved_directory:
                compiler_args.append(f"-I{resolved_directory}")
        for define in defines:
            compiler_args.append(f"-D{define}")
        if not compiler_args:
            notes.append("未提供 compile_db / include / define，clang-tidy 可能因头文件缺失产生大量解析错误")
        command.extend(["--", *compiler_args])
    try:
        process = run_process(command, timeout)
    except subprocess.TimeoutExpired as exc:
        raise ToolRunError("clang-tidy", f"clang-tidy 超时（{timeout}s）") from exc
    except OSError as exc:
        raise ToolRunError("clang-tidy", f"clang-tidy 无法执行：{exc}") from exc
    findings = parse_tidy_output(process.stdout or "", workspace)
    findings.extend(parse_tidy_output(process.stderr or "", workspace))
    if process.returncode != 0 and not findings:
        tail = ((process.stderr or process.stdout or "").strip().splitlines() or ["clang-tidy 执行失败"])[-1]
        raise ToolRunError("clang-tidy", f"clang-tidy 异常退出（exit {process.returncode}）：{tail}")
    return dedupe_findings(findings), notes


def evaluate_gate(findings: list[dict], fail_on: str) -> dict:
    counts = {"error": 0, "warning": 0, "info": 0}
    for finding in findings:
        severity = finding.get("severity", "info")
        counts[severity if severity in counts else "info"] += 1
    if fail_on == "none":
        passed = True
    else:
        threshold = SEVERITY_RANK[fail_on]
        passed = all(SEVERITY_RANK.get(finding.get("severity", "info"), 0) < threshold for finding in findings)
    return {"fail_on": fail_on, "passed": passed, "counts": counts}


def load_rules(skill_directory: Path) -> list[dict]:
    rules_file = skill_directory / "references" / "rules.json"
    try:
        data = json.loads(rules_file.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"规则库不存在：{rules_file}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"规则库无法解析：{rules_file}（{exc}）") from exc
    rules = data.get("rules", []) if isinstance(data, dict) else []
    return [rule for rule in rules if isinstance(rule, dict)]


def findings_for_output(findings: list[dict], max_findings: int) -> tuple[list[dict], bool]:
    if max_findings <= 0:
        return findings, False
    return findings[:max_findings], len(findings) > max_findings