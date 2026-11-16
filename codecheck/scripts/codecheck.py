#!/usr/bin/env python3
"""codecheck 主入口：编码规范检查（格式 / 静态分析 / 规则库）。

设计约定：
- 只读检查，默认不修改源文件；
- 统一 JSON 输出，退出码：0 通过、1 失败（error.code 区分原因）、130 中断；
- 工具缺失/运行失败不静默成功；配置损坏直接报错，不返回空配置；
- 成功后把最近一次检查写入 .embeddedskills/state.json 的 last_check 段。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from codecheck_runtime import (
    ConfigError,
    default_config_path,
    is_missing,
    load_json_file,
    load_local_config,
    load_project_config,
    load_workspace_state,
    make_result,
    make_timing,
    normalize_path,
    now_iso,
    output_json,
    parameter_context,
    resolve_runtime_param,
    resolve_tool_param,
    skill_dir,
    update_state_entry,
    workspace_root,
)
from codecheck_tools import (
    ToolRunError,
    dedupe_findings,
    discover_sources,
    evaluate_gate,
    findings_for_output,
    format_findings,
    load_rules,
    resolve_workspace_path,
    tidy_findings,
    workspace_relative,
)


DEFAULT_TIMEOUT_SECONDS = 600
DEFAULT_MAX_FINDINGS = 200
DEFAULT_LIST_LIMIT = 200


def build_error_result(action: str, code: str, message: str, context: dict, timing: dict) -> dict:
    return make_result(
        status="error",
        action=action,
        summary=message,
        context=context,
        error={"code": code, "message": message},
        timing=timing,
    )


def load_effective_local_config(explicit_path: str | None) -> dict:
    """--config 显式指定的文件直接读取；未指定时才读取 skill 目录下的 config.json。"""
    if explicit_path:
        path = Path(str(explicit_path)).expanduser()
        if not path.is_file():
            raise ConfigError(f"配置文件不存在：{path}")
        return load_json_file(path)
    return load_local_config(__file__)


def print_findings(result: dict) -> None:
    details = result.get("details", {})
    for finding in details.get("findings", []):
        print(
            f"  {finding['file']}:{finding['line']}:{finding['column']} "
            f"{finding['severity']} [{finding['rule']}] {finding['message']}"
        )
    if details.get("truncated"):
        print(f"  ... 仅显示部分结果（共 {details.get('finding_count')} 条）")
    for note in details.get("notes", []):
        print(f"  note: {note}")


def collect_files(args: argparse.Namespace, workspace: Path, project_config: dict) -> tuple[list[Path], list[str]]:
    """CLI --files 优先；否则按源根/排除规则发现。"""
    if args.files:
        files: list[Path] = []
        for value in args.files:
            path = Path(value)
            files.append((path if path.is_absolute() else (workspace / path)).resolve())
        return files, []
    source_roots = args.source_root
    if source_roots is None:
        configured = project_config.get("source_roots")
        source_roots = configured if isinstance(configured, list) else None
    excludes = args.exclude
    if excludes is None:
        configured_excludes = project_config.get("exclude")
        excludes = configured_excludes if isinstance(configured_excludes, list) else None
    return discover_sources(
        workspace,
        source_roots=source_roots,
        exclude_names=[str(item) for item in (excludes or [])],
        exclude_paths=[str(item) for item in (excludes or [])],
        max_files=args.max_files,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="codecheck 编码规范检查（clang-format / clang-tidy）")
    parser.add_argument("action", choices=["scan", "format", "tidy", "check", "rules"])
    parser.add_argument("--workspace", default=None, help="workspace 根目录，默认当前目录")
    parser.add_argument("--config", default=None, help="skill 环境级配置路径")
    parser.add_argument("--files", action="append", default=None, help="只检查指定文件（可重复）")
    parser.add_argument("--source-root", action="append", default=None, help="源文件根目录（可重复）")
    parser.add_argument("--exclude", action="append", default=None, help="排除目录/名字（可重复）")
    parser.add_argument("--include-dir", action="append", default=None, help="clang-tidy -I 参数（可重复）")
    parser.add_argument("--define", action="append", default=None, help="clang-tidy -D 参数（可重复）")
    parser.add_argument("--compile-db", default=None, help="compile_commands.json 所在目录")
    parser.add_argument("--style-file", default=None, help=".clang-format 路径")
    parser.add_argument("--tidy-config-file", default=None, help=".clang-tidy 路径")
    parser.add_argument("--clang-format-exe", default=None)
    parser.add_argument("--clang-tidy-exe", default=None)
    parser.add_argument("--fail-on", choices=["none", "warning", "error"], default=None)
    parser.add_argument("--max-files", type=int, default=0)
    parser.add_argument("--max-findings", type=int, default=DEFAULT_MAX_FINDINGS)
    parser.add_argument("--timeout", type=int, default=None, help="单次工具调用超时（秒）")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    started_at = now_iso()
    started_ts = time.time()
    workspace = workspace_root(args.workspace)
    config_path = normalize_path(args.config or str(default_config_path(__file__)))

    def timing() -> dict:
        return make_timing(started_at, (time.time() - started_ts) * 1000)

    try:
        local_config = load_effective_local_config(args.config)
        project_config = load_project_config(str(workspace))
    except ConfigError as exc:
        result = build_error_result(
            args.action,
            "invalid_config",
            str(exc),
            parameter_context(provider="codecheck", workspace=str(workspace), config_path=config_path),
            timing(),
        )
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {exc}", file=sys.stderr)
        return 1

    parameter_sources: dict[str, str] = {}

    def fail_tool(message: str) -> int:
        result = build_error_result(
            args.action,
            "tool_failed",
            message,
            parameter_context(
                provider="codecheck",
                workspace=str(workspace),
                parameter_sources=parameter_sources,
                config_path=config_path,
            ),
            timing(),
        )
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {message}", file=sys.stderr)
        return 1

    if args.action == "rules":
        try:
            rules = load_rules(skill_dir(__file__))
        except ValueError as exc:
            result = build_error_result(
                args.action,
                "invalid_rules",
                str(exc),
                parameter_context(provider="codecheck", workspace=str(workspace), config_path=config_path),
                timing(),
            )
            if args.as_json:
                output_json(result)
            else:
                print(f"错误: {exc}", file=sys.stderr)
            return 1
        result = make_result(
            status="ok",
            action="rules",
            summary=f"规则库共 {len(rules)} 条",
            details={"rules": rules},
            context=parameter_context(provider="codecheck", workspace=str(workspace), config_path=config_path),
            metrics={"rules": len(rules)},
            timing=timing(),
        )
        if args.as_json:
            output_json(result)
        else:
            print(result["summary"])
            for rule in rules:
                print(f"  {rule.get('id')}: {rule.get('title')} [{rule.get('severity')}]")
        return 0

    try:
        timeout_value, parameter_sources["timeout_seconds"] = resolve_runtime_param(
            "timeout_seconds",
            args.timeout,
            project_config=project_config,
            project_keys=["timeout_seconds"],
            local_config=local_config,
            local_keys=["timeout_seconds"],
            default=DEFAULT_TIMEOUT_SECONDS,
        )
        fail_on, parameter_sources["fail_on"] = resolve_runtime_param(
            "fail_on",
            args.fail_on,
            project_config=project_config,
            project_keys=["fail_on"],
            local_config=local_config,
            local_keys=["fail_on"],
            default="warning",
        )
    except ConfigError as exc:
        result = build_error_result(
            args.action,
            "invalid_config",
            str(exc),
            parameter_context(provider="codecheck", workspace=str(workspace), config_path=config_path),
            timing(),
        )
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {exc}", file=sys.stderr)
        return 1
    timeout_seconds = int(timeout_value)

    files, notes = collect_files(args, workspace, project_config)
    if args.action == "scan":
        result = make_result(
            status="ok",
            action="scan",
            summary=f"发现 {len(files)} 个 C/C++ 文件",
            details={
                "files": [workspace_relative(path, workspace) for path in files[:DEFAULT_LIST_LIMIT]],
                "truncated": len(files) > DEFAULT_LIST_LIMIT,
                "notes": notes,
            },
            context=parameter_context(
                provider="codecheck",
                workspace=str(workspace),
                parameter_sources=parameter_sources,
                config_path=config_path,
            ),
            metrics={"files": len(files)},
            timing=timing(),
        )
        if args.as_json:
            output_json(result)
        else:
            print(result["summary"])
            for item in result["details"].get("files", []):
                print(f"  {item}")
        return 0

    if not files:
        result = build_error_result(
            args.action,
            "no_source_files",
            "未发现可检查的 C/C++ 文件",
            parameter_context(
                provider="codecheck",
                workspace=str(workspace),
                parameter_sources=parameter_sources,
                config_path=config_path,
            ),
            timing(),
        )
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {result['summary']}", file=sys.stderr)
        return 1

    findings: list[dict] = []
    backends: dict[str, str] = {}

    if args.action in {"format", "check"}:
        try:
            clang_format, parameter_sources["clang_format_exe"] = resolve_tool_param(
                "clang_format_exe",
                args.clang_format_exe,
                local_config=local_config,
                local_keys=["clang_format_exe"],
                path_candidates=["clang-format.exe", "clang-format"],
                required=True,
            )
        except ConfigError as exc:
            result = build_error_result(
                args.action,
                "tool_not_found",
                f"未找到 clang-format：{exc}",
                parameter_context(
                    provider="codecheck",
                    workspace=str(workspace),
                    parameter_sources=parameter_sources,
                    config_path=config_path,
                ),
                timing(),
            )
            if args.as_json:
                output_json(result)
            else:
                print(f"错误: {result['summary']}", file=sys.stderr)
            return 1
        backends["clang_format"] = clang_format

        style_value, parameter_sources["style_file"] = resolve_runtime_param(
            "style_file",
            args.style_file,
            project_config=project_config,
            project_keys=["style_file"],
            local_config=local_config,
            local_keys=["style_file"],
            default="",
        )
        style_file = resolve_workspace_path(style_value, workspace)
        if not style_file and (workspace / ".clang-format").is_file():
            style_file = str((workspace / ".clang-format").resolve())
        if style_file and not Path(style_file).is_file():
            notes.append(f"格式配置文件不存在，clang-format 将使用默认风格：{style_file}")
        try:
            format_results, format_notes = format_findings(files, clang_format, style_file, workspace, timeout_seconds)
        except ToolRunError as exc:
            return fail_tool(str(exc))
        findings.extend(format_results)
        notes.extend(format_notes)

    if args.action in {"tidy", "check"}:
        try:
            clang_tidy, parameter_sources["clang_tidy_exe"] = resolve_tool_param(
                "clang_tidy_exe",
                args.clang_tidy_exe,
                local_config=local_config,
                local_keys=["clang_tidy_exe"],
                path_candidates=["clang-tidy.exe", "clang-tidy"],
                required=True,
            )
        except ConfigError as exc:
            result = build_error_result(
                args.action,
                "tool_not_found",
                f"未找到 clang-tidy：{exc}",
                parameter_context(
                    provider="codecheck",
                    workspace=str(workspace),
                    parameter_sources=parameter_sources,
                    config_path=config_path,
                ),
                timing(),
            )
            if args.as_json:
                output_json(result)
            else:
                print(f"错误: {result['summary']}", file=sys.stderr)
            return 1
        backends["clang_tidy"] = clang_tidy

        tidy_config_value, parameter_sources["tidy_config_file"] = resolve_runtime_param(
            "tidy_config_file",
            args.tidy_config_file,
            project_config=project_config,
            project_keys=["tidy_config_file"],
            local_config=local_config,
            local_keys=["tidy_config_file"],
            default="",
        )
        tidy_config_file = resolve_workspace_path(tidy_config_value, workspace)
        if not tidy_config_file and (workspace / ".clang-tidy").is_file():
            tidy_config_file = str((workspace / ".clang-tidy").resolve())

        compile_db_value, parameter_sources["compile_db"] = resolve_runtime_param(
            "compile_db",
            args.compile_db,
            project_config=project_config,
            project_keys=["compile_db"],
            default="",
        )
        compile_db = resolve_workspace_path(compile_db_value, workspace)
        if compile_db and not Path(compile_db).exists():
            notes.append(f"compile_db 不存在，将退化为 include/define 参数：{compile_db}")
            compile_db = ""

        include_dirs = args.include_dir
        if include_dirs is None:
            configured_includes = project_config.get("include_dirs")
            include_dirs = configured_includes if isinstance(configured_includes, list) else []
        defines = args.define
        if defines is None:
            configured_defines = project_config.get("defines")
            defines = configured_defines if isinstance(configured_defines, list) else []

        try:
            tidy_results, tidy_notes = tidy_findings(
                files,
                clang_tidy,
                tidy_config_file=tidy_config_file,
                compile_db=compile_db,
                include_dirs=[str(item) for item in include_dirs],
                defines=[str(item) for item in defines],
                workspace=workspace,
                timeout=timeout_seconds,
            )
        except ToolRunError as exc:
            return fail_tool(str(exc))
        findings.extend(tidy_results)
        notes.extend(tidy_notes)

    findings = dedupe_findings(findings)
    gate = evaluate_gate(findings, fail_on)
    shown, truncated = findings_for_output(findings, args.max_findings)

    state_info: dict = {}
    try:
        state_info = update_state_entry(
            "last_check",
            {
                "provider": "codecheck",
                "action": args.action,
                "passed": gate["passed"],
                "counts": gate["counts"],
                "files": len(files),
                "fail_on": fail_on,
            },
            str(workspace),
        )
    except (ConfigError, OSError) as exc:
        notes.append(f"state.json 写入失败：{exc}")

    if gate["passed"]:
        status = "ok"
        error = None
        summary = f"规范检查通过：{len(files)} 个文件，0 个阻断问题"
        exit_code = 0
    else:
        status = "error"
        summary = (
            f"规范检查未通过：{gate['counts']['error']} errors, "
            f"{gate['counts']['warning']} warnings（fail_on={fail_on}）"
        )
        error = {
            "code": "standard_violations",
            "message": summary,
            "fail_on": fail_on,
            "counts": gate["counts"],
        }
        exit_code = 1

    result = make_result(
        status=status,
        action=args.action,
        summary=summary,
        details={
            "files_checked": len(files),
            "findings": shown,
            "finding_count": len(findings),
            "truncated": truncated,
            "gate": gate,
            "backends": backends,
            "notes": notes,
        },
        context=parameter_context(
            provider="codecheck",
            workspace=str(workspace),
            parameter_sources=parameter_sources,
            config_path=config_path,
        ),
        metrics={"files": len(files), "findings": len(findings)},
        state=state_info,
        error=error,
        timing=timing(),
    )

    if args.as_json:
        output_json(result)
    else:
        print(f"[codecheck {args.action}] {result['summary']}")
        print_findings(result)
    return exit_code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
        print(
            '{"status": "error", "action": "interrupted", "summary": "用户中断"}',
            flush=True,
        )
        raise SystemExit(130)