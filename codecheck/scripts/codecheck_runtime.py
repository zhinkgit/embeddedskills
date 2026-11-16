"""codecheck skill 的运行时公共层：配置、状态、参数解析与结果输出。

与仓库既有 `*_runtime.py` 保持同一套接口，便于后续收敛到共享 core：
- 三层配置 + PATH：CLI > skill/config.json > .embeddedskills/config.json > state.json > 默认值
- 统一结果包络：status/action/summary/details/context/artifacts/metrics/state/timing/error
- 参数来源追踪：parameter_context() 回显每个参数的实际来源

与模版的两处有意差异（见 PR 说明）：
1. `load_json_file` 遇到损坏 JSON 抛 `ConfigError`，不再静默返回 `{}`，
   避免配置被读空后又被整文件重写；
2. `save_json_file` 使用临时文件 + `os.replace` 原子写，避免并发/中断写坏 state。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


STATE_DIR_NAME = ".embeddedskills"
STATE_FILE_NAME = "state.json"
PROJECT_CONFIG_FILE_NAME = "config.json"
SKILL_NAME = "codecheck"


class ConfigError(ValueError):
    """配置文件不存在结构或 JSON 损坏。"""


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def is_missing(value: Any) -> bool:
    return value is None or value == ""


def default_config_path(script_file: str) -> Path:
    return Path(script_file).resolve().parents[1] / "config.json"


def skill_dir(script_file: str) -> Path:
    return Path(script_file).resolve().parents[1]


def load_json_file(path: str | Path) -> dict:
    """读取 JSON 对象；文件不存在返回 {}，内容损坏抛 ConfigError。"""
    file_path = Path(path)
    if not file_path.is_file():
        return {}
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        raise ConfigError(f"配置文件无法解析：{file_path}（{exc}）") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"配置文件顶层必须是 JSON 对象：{file_path}")
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


def normalize_command_value(value: str | None) -> str:
    if is_missing(value):
        return ""
    candidate = str(value).strip()
    expanded = Path(candidate).expanduser()
    if expanded.exists():
        return str(expanded.resolve())
    resolved = shutil.which(candidate)
    if resolved:
        return normalize_path(resolved)
    return candidate


def resolve_path_candidate(candidates: list[str] | tuple[str, ...] | None) -> tuple[str, str]:
    for candidate in candidates or []:
        if is_missing(candidate):
            continue
        resolved = shutil.which(str(candidate))
        if resolved:
            return normalize_path(resolved), f"path:{candidate}"
    return "", ""


def load_local_config(script_file: str | None = None) -> dict:
    if not script_file:
        return {}
    return load_json_file(default_config_path(script_file))


def load_project_config(workspace: str | None = None) -> dict:
    config_file = workspace_root(workspace) / STATE_DIR_NAME / PROJECT_CONFIG_FILE_NAME
    data = load_json_file(config_file)
    section = data.get(SKILL_NAME, {})
    return section if isinstance(section, dict) else {}


def save_project_config(workspace: str | None = None, values: dict | None = None) -> None:
    if values is None:
        values = {}
    config_file = workspace_root(workspace) / STATE_DIR_NAME / PROJECT_CONFIG_FILE_NAME
    data = load_json_file(config_file)
    section = data.get(SKILL_NAME, {})
    merged = {**(section if isinstance(section, dict) else {}), **values}
    data[SKILL_NAME] = merged
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


def _first_resolved(mapping: dict, keys: list[str]) -> tuple[Any, str | None]:
    for key in keys:
        value = mapping.get(key)
        if not is_missing(value):
            return value, key
    return None, None


def resolve_tool_param(
    name: str,
    cli_value: Any,
    *,
    local_config: dict | None = None,
    local_keys: list[str] | None = None,
    path_candidates: list[str] | tuple[str, ...] | None = None,
    default: Any = None,
    required: bool = False,
) -> tuple[Any, str]:
    """工具路径：CLI > skill/config.json > PATH > 默认值。"""
    if not is_missing(cli_value):
        value = normalize_command_value(str(cli_value))
        source = "cli"
    else:
        value = None
        source = ""
        if local_config and local_keys:
            value, key = _first_resolved(local_config, local_keys)
            if not is_missing(value):
                value = normalize_command_value(str(value))
                source = f"config:{key}"
        if is_missing(value):
            value, source = resolve_path_candidate(path_candidates)
        if is_missing(value) and not is_missing(default):
            value = default
            source = f"default:{default}" if isinstance(default, str) else "default"
    if required and is_missing(value):
        raise ConfigError(f"缺少必要参数: {name}")
    return value, source


def resolve_project_param(
    name: str,
    cli_value: Any,
    *,
    project_config: dict | None = None,
    project_keys: list[str] | None = None,
    state_record: dict | None = None,
    state_keys: list[str] | None = None,
    default: Any = None,
    required: bool = False,
    normalize_as_path: bool = False,
) -> tuple[Any, str]:
    """工程参数：CLI > .embeddedskills/config.json > state.json > 默认值。"""
    if not is_missing(cli_value):
        value = cli_value
        source = "cli"
    else:
        value = None
        source = ""
        if project_config and project_keys:
            value, key = _first_resolved(project_config, project_keys)
            if not is_missing(value):
                source = f"project_config:{key}"
        if is_missing(value) and state_record and state_keys:
            value, key = _first_resolved(state_record, state_keys)
            if not is_missing(value):
                source = f"state:{key}"
        if is_missing(value) and not is_missing(default):
            value = default
            source = "default"
    if normalize_as_path and not is_missing(value):
        value = normalize_path(str(value))
    if required and is_missing(value):
        raise ConfigError(f"缺少必要参数: {name}")
    return value, source


def resolve_runtime_param(
    name: str,
    cli_value: Any,
    *,
    project_config: dict | None = None,
    project_keys: list[str] | None = None,
    local_config: dict | None = None,
    local_keys: list[str] | None = None,
    state_record: dict | None = None,
    state_keys: list[str] | None = None,
    default: Any = None,
    required: bool = False,
    normalize_as_path: bool = False,
) -> tuple[Any, str]:
    """运行参数：CLI > 工程配置 > 环境配置 > state > 默认值。"""
    if not is_missing(cli_value):
        value = cli_value
        source = "cli"
    else:
        value = None
        source = ""
        if project_config and project_keys:
            value, key = _first_resolved(project_config, project_keys)
            if not is_missing(value):
                source = f"project_config:{key}"
        if is_missing(value) and local_config and local_keys:
            value, key = _first_resolved(local_config, local_keys)
            if not is_missing(value):
                source = f"config:{key}"
        if is_missing(value) and state_record and state_keys:
            value, key = _first_resolved(state_record, state_keys)
            if not is_missing(value):
                source = f"state:{key}"
        if is_missing(value) and not is_missing(default):
            value = default
            source = "default"
    if normalize_as_path and not is_missing(value):
        value = normalize_path(str(value))
    if required and is_missing(value):
        raise ConfigError(f"缺少必要参数: {name}")
    return value, source


def resolve_artifact_param(name: str, cli_value: Any, **kwargs) -> tuple[Any, str]:
    return resolve_project_param(name, cli_value, **kwargs)


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


def parameter_context(
    *,
    provider: str,
    workspace: str | None = None,
    parameter_sources: dict | None = None,
    config_path: str | None = None,
) -> dict:
    context = {"provider": provider, "workspace": str(workspace_root(workspace))}
    if parameter_sources:
        context["parameter_sources"] = compact_dict(parameter_sources)
    if not is_missing(config_path):
        context["config_path"] = normalize_path(str(config_path))
    return context


def output_json(data: dict, *, indent: int = 2) -> None:
    """输出 JSON 到 stdout。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(json.dumps(data, ensure_ascii=False, indent=indent), flush=True)