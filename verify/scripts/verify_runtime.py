"""verify skill 的运行时公共层：配置、状态、报告、进程与结果输出。

与仓库既有 `*_runtime.py` 保持同一套接口：
- 三层配置：CLI > 测试计划 > `.embeddedskills/config.json` > `verify/config.json` > 默认值
- 统一结果包络：status/action/summary/details/context/artifacts/metrics/state/timing/error
- 状态回写：`.embeddedskills/state/verify.json` 中的 `last_verify`
- 报告落盘：`.embeddedskills/reports/verify/`
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


STATE_DIR_NAME = ".embeddedskills"
STATE_FILE_NAME = Path("state") / "verify.json"
PROJECT_CONFIG_FILE_NAME = "config.json"
SKILL_NAME = "verify"
REPORT_DIR_NAME = "reports"
REPORT_SUBDIR_NAME = "verify"
DEFAULT_PLAN_CANDIDATES = (
    ".embeddedskills/verify/plan.json",
    ".embeddedskills/verify.json",
    "verify.plan.json",
)


class ConfigError(ValueError):
    """配置文件不存在、结构错误或 JSON 损坏。"""


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def is_missing(value: Any) -> bool:
    return value is None or value == ""


def skill_dir(script_file: str | Path) -> Path:
    return Path(script_file).resolve().parents[1]


def default_config_path(script_file: str | Path) -> Path:
    return skill_dir(script_file) / "config.json"


def load_json_file(path: str | Path, *, required: bool = False) -> dict:
    """读取 JSON 对象；不存在返回 {}，损坏抛 ConfigError。"""
    file_path = Path(path)
    if not file_path.is_file():
        if required:
            raise ConfigError(f"文件不存在：{file_path}")
        return {}
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        raise ConfigError(f"JSON 无法解析：{file_path}（{exc}）") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"JSON 顶层必须是对象：{file_path}")
    return data


def save_json_file(path: str | Path, data: dict) -> None:
    """原子写入 JSON（同目录临时文件 + os.replace）。"""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(dir=str(file_path.parent), prefix=f".{file_path.name}.")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(data, ensure_ascii=False, indent=2))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, file_path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def workspace_root(workspace: str | None = None) -> Path:
    if not is_missing(workspace):
        return Path(str(workspace)).expanduser().resolve()
    return Path.cwd().resolve()


def normalize_path(value: str | None) -> str:
    if is_missing(value):
        return ""
    return str(Path(str(value)).expanduser().resolve())


def load_local_config(script_file: str | Path | None = None) -> dict:
    if not script_file:
        return {}
    return load_json_file(default_config_path(script_file))


def load_project_config(workspace: str | None = None) -> dict:
    config_file = workspace_root(workspace) / STATE_DIR_NAME / PROJECT_CONFIG_FILE_NAME
    data = load_json_file(config_file)
    section = data.get(SKILL_NAME, {})
    return section if isinstance(section, dict) else {}


def load_full_project_config(workspace: str | None = None) -> dict:
    config_file = workspace_root(workspace) / STATE_DIR_NAME / PROJECT_CONFIG_FILE_NAME
    return load_json_file(config_file)


def save_project_config(workspace: str | None = None, values: dict | None = None) -> None:
    config_file = workspace_root(workspace) / STATE_DIR_NAME / PROJECT_CONFIG_FILE_NAME
    data = load_json_file(config_file)
    section = data.get(SKILL_NAME, {})
    data[SKILL_NAME] = {**(section if isinstance(section, dict) else {}), **(values or {})}
    save_json_file(config_file, data)


def load_workspace_state(workspace: str | None = None) -> dict:
    return load_json_file(workspace_root(workspace) / STATE_DIR_NAME / STATE_FILE_NAME)


def save_workspace_state(state: dict, workspace: str | None = None) -> Path:
    file_path = workspace_root(workspace) / STATE_DIR_NAME / STATE_FILE_NAME
    save_json_file(file_path, state)
    return file_path


def get_state_entry(state: dict | None, key: str) -> dict:
    if not isinstance(state, dict):
        return {}
    value = state.get(key, {})
    return value if isinstance(value, dict) else {}


def update_state_entry(category: str, record: dict, workspace: str | None = None) -> dict:
    state = load_workspace_state(workspace)
    state[category] = {**record, "timestamp": record.get("timestamp") or now_iso()}
    file_path = save_workspace_state(state, workspace)
    return {
        "workspace": str(workspace_root(workspace)),
        "file": str(file_path),
        "updated_keys": [category],
        category: state[category],
    }


def resolve_plan_path(workspace: str | None, explicit: str | None = None) -> Path | None:
    """按 CLI > 工程配置 > 环境配置 > 约定位置解析测试计划路径。"""
    if not is_missing(explicit):
        candidate = Path(str(explicit)).expanduser()
        if not candidate.is_absolute():
            candidate = workspace_root(workspace) / candidate
        return candidate.resolve()

    project_config = load_project_config(workspace)
    local_config = load_local_config(__file__)
    configured = project_config.get("plan") or local_config.get("plan")
    if not is_missing(configured):
        candidate = Path(str(configured)).expanduser()
        if not candidate.is_absolute():
            candidate = workspace_root(workspace) / candidate
        return candidate.resolve()

    for relative in DEFAULT_PLAN_CANDIDATES:
        candidate = workspace_root(workspace) / relative
        if candidate.is_file():
            return candidate.resolve()
    return None


def parameter_context(
    *,
    provider: str,
    workspace: str | None = None,
    parameter_sources: dict | None = None,
    plan_path: str | None = None,
) -> dict:
    context = {"provider": provider, "workspace": str(workspace_root(workspace))}
    if parameter_sources:
        context["parameter_sources"] = {key: value for key, value in parameter_sources.items() if value not in (None, "", [], {})}
    if not is_missing(plan_path):
        context["plan_path"] = normalize_path(str(plan_path))
    return context


def compact_dict(data: dict | None) -> dict:
    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in data.items() if value not in (None, "", [], {})}


def make_result(
    *,
    status: str,
    action: str,
    summary: str,
    details: dict | None = None,
    context: dict | None = None,
    artifacts: dict | None = None,
    metrics: dict | None = None,
    state: dict | None = None,
    timing: dict | None = None,
    error: dict | None = None,
) -> dict:
    result = {
        "status": status,
        "action": action,
        "summary": summary,
        "details": compact_dict(details),
    }
    if context:
        result["context"] = compact_dict(context)
    if artifacts:
        result["artifacts"] = compact_dict(artifacts)
    if metrics:
        result["metrics"] = compact_dict(metrics)
    if state:
        result["state"] = compact_dict(state)
    if timing:
        result["timing"] = compact_dict(timing)
    if error:
        result["error"] = compact_dict(error)
    return result


def make_timing(started_at: str, elapsed_ms: int | float) -> dict:
    return {"started_at": started_at, "finished_at": now_iso(), "elapsed_ms": int(elapsed_ms)}


def output_json(data: dict, *, indent: int = 2) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(json.dumps(data, ensure_ascii=False, indent=indent), flush=True)


def hidden_subprocess_kwargs() -> dict:
    if sys.platform != "win32":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        "startupinfo": startupinfo,
    }


def run_process(
    command: list[str],
    *,
    cwd: Path,
    timeout_sec: float | None = None,
    env: dict | None = None,
) -> dict:
    """执行外部命令，返回结构化结果；超时不会抛出。"""
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            env={**os.environ, **(env or {})},
            **hidden_subprocess_kwargs(),
        )
    except FileNotFoundError as exc:
        return {
            "exit_code": 127,
            "timed_out": False,
            "stdout": "",
            "stderr": str(exc),
            "error": {"code": "command_not_found", "message": f"命令不存在: {command[0]}"},
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "exit_code": 124,
            "timed_out": True,
            "stdout": _decode_output(exc.stdout),
            "stderr": _decode_output(exc.stderr),
            "error": {"code": "timeout", "message": f"命令超时（{timeout_sec}s）: {' '.join(command)}"},
        }
    return {
        "exit_code": proc.returncode,
        "timed_out": False,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
        "error": None,
    }


def _decode_output(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def tail(text: str, limit: int = 2000) -> str:
    if not text:
        return ""
    return text[-limit:]


def report_dir(workspace: str | None = None) -> Path:
    return workspace_root(workspace) / STATE_DIR_NAME / REPORT_DIR_NAME / REPORT_SUBDIR_NAME


def write_report(workspace: str | None, report: dict, *, prefix: str = "run") -> dict:
    """写入带时间戳的报告和 latest.json，返回产物路径。"""
    directory = report_dir(workspace)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    timestamped = directory / f"{prefix}-{stamp}.json"
    counter = 1
    while timestamped.exists():
        timestamped = directory / f"{prefix}-{stamp}-{counter}.json"
        counter += 1
    latest = directory / "latest.json"
    save_json_file(timestamped, report)
    save_json_file(latest, report)
    return {"report": str(timestamped), "latest_report": str(latest)}