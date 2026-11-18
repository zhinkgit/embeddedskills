"""eide skill runtime utilities."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from time import sleep
from shutil import which
from typing import Any


STATE_DIR_NAME = ".embeddedskills"
STATE_FILE_NAME = Path("state") / "eide.json"
PROJECT_CONFIG_FILE_NAME = "config.json"

SKILL_NAME = "eide"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def default_config_path(script_file: str) -> Path:
    return Path(script_file).resolve().parents[1] / "config.json"


def load_local_config(script_file: str | None = None) -> dict:
    if script_file is None:
        import inspect
        frame = inspect.currentframe()
        if frame and frame.f_back:
            script_file = frame.f_back.f_globals.get("__file__", "")
        if not script_file:
            return {}
    config_path = default_config_path(script_file)
    return load_json_file(config_path)


def save_local_config(data: dict, script_file: str | None = None) -> Path | None:
    if script_file is None:
        import inspect
        frame = inspect.currentframe()
        if frame and frame.f_back:
            script_file = frame.f_back.f_globals.get("__file__", "")
        if not script_file:
            return None
    config_path = default_config_path(script_file)
    existing = load_json_file(config_path)
    existing.update(data)
    save_json_file(config_path, existing)
    return config_path


def load_project_config(workspace: str | None = None) -> dict:
    ws = workspace_root(workspace)
    config_file = ws / STATE_DIR_NAME / PROJECT_CONFIG_FILE_NAME
    data = load_json_file(config_file)
    return data.get(SKILL_NAME, {})


def save_project_config(
    workspace: str | None = None, values: dict | None = None
) -> Path | None:
    if values is None:
        values = {}
    ws = workspace_root(workspace)
    config_file = ws / STATE_DIR_NAME / PROJECT_CONFIG_FILE_NAME
    with json_write_lock(config_file):
        data = load_json_file(config_file)
        data[SKILL_NAME] = {**(data.get(SKILL_NAME, {})), **values}
        save_json_file(config_file, data)
    return config_file


def output_json(data: dict, *, indent: int | None = 2, stream=None) -> None:
    if "status" in data:
        error = data.get("error") or {}
        if data["status"] == "error":
            message = error.get("message") or data.get("summary") or "Command failed"
            error = {**error, "code": error.get("code") or "command_failed", "message": message}
            data = {**data, "error": error}
        data = {**data, "summary": data.get("summary") or error.get("message") or str(data.get("action", SKILL_NAME)),
                "details": data.get("details") or {}}
    stream = sys.stdout if stream is None else stream
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=indent), file=stream, flush=True)


def is_missing(value: Any) -> bool:
    return value is None or value == ""


def normalize_path(value: str | None) -> str:
    if is_missing(value):
        return ""
    return str(Path(str(value)).expanduser().resolve())


def normalize_path_with_base(
    value: str | None, base: str | Path | None = None
) -> str:
    if is_missing(value):
        return ""
    path = Path(str(value)).expanduser()
    if base and not path.is_absolute():
        path = Path(base) / path
    return str(path.resolve())


def _serialize_state_value(value: Any, workspace: Path) -> Any:
    if isinstance(value, dict):
        return {
            key: _serialize_state_value(item, workspace)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_serialize_state_value(item, workspace) for item in value]
    if not isinstance(value, str) or "://" in value:
        return value

    path = Path(value).expanduser()
    if not path.is_absolute():
        return value
    try:
        return Path(os.path.relpath(path.resolve(), workspace)).as_posix()
    except ValueError:
        return value


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


def load_json_file(path: str | Path) -> dict:
    file_path = Path(path)
    if not file_path.exists():
        return {}
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


@contextmanager
def json_write_lock(path: Path):
    """Protect a read-modify-write update without locking readers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(path.suffix + ".lock").open("a+b") as lock:
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def save_json_file(path: str | Path, data: dict) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=file_path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(data, stream, ensure_ascii=False, indent=2)
        except BaseException:
            stream.close()
            temporary.unlink()
            raise
    try:
        for attempt in range(10):
            try:
                temporary.replace(file_path)
                break
            except PermissionError:
                if os.name != "nt" or attempt == 9:
                    raise
                sleep(0.025)
    finally:
        temporary.unlink(missing_ok=True)


def workspace_root(workspace: str | None = None) -> Path:
    if not is_missing(workspace):
        return Path(str(workspace)).expanduser().resolve()
    return Path.cwd().resolve()


def load_workspace_state(workspace: str | None = None) -> dict:
    return load_json_file(
        workspace_root(workspace) / STATE_DIR_NAME / STATE_FILE_NAME
    )


def save_workspace_state(state: dict, workspace: str | None = None) -> Path:
    ws = workspace_root(workspace)
    file_path = ws / STATE_DIR_NAME / STATE_FILE_NAME
    save_json_file(file_path, _serialize_state_value(state, ws))
    return file_path


def get_state_entry(state: dict | None, key: str) -> dict:
    if not isinstance(state, dict):
        return {}
    value = state.get(key, {})
    return value if isinstance(value, dict) else {}


def update_state_entry(
    category: str, record: dict, workspace: str | None = None
) -> dict:
    ws = workspace_root(workspace)
    with json_write_lock(workspace_root(workspace) / STATE_DIR_NAME / STATE_FILE_NAME):
        state = load_workspace_state(workspace)
        state[category] = _serialize_state_value(
            {**record, "timestamp": record.get("timestamp") or now_iso()}, ws
        )
        file_path = save_workspace_state(state, workspace)
    return {
        "workspace": str(ws),
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


def _auto_detect_builder_dir() -> str:
    vscode_ext = os.environ.get("USERPROFILE", os.path.expanduser("~"))
    base = Path(vscode_ext) / ".vscode" / "extensions"
    if base.exists():
        for d in sorted(base.iterdir(), reverse=True):
            if d.is_dir() and d.name.startswith("cl.eide-"):
                builder_dir = d / "res" / "tools" / "win32" / "unify_builder"
                if builder_dir.exists():
                    return str(builder_dir.resolve())
    return ""


def _auto_detect_code() -> str:
    code = which("code")
    if code:
        return str(Path(code).resolve())
    candidates = [
        r"C:\Program Files\Microsoft VS Code\bin\code.cmd",
        r"C:\Program Files (x86)\Microsoft VS Code\bin\code.cmd",
        os.path.join(
            os.environ.get("LOCALAPPDATA", ""),
            "Programs",
            "Microsoft VS Code",
            "bin",
            "code.cmd",
        ),
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return str(Path(c).resolve())
    return ""


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
    if not is_missing(cli_value):
        value = normalize_path(str(cli_value))
        source = "cli"
    else:
        value = None
        source = ""
        if local_config and local_keys:
            value, key = _first_resolved(local_config, local_keys)
            if not is_missing(value):
                value = normalize_path(str(value))
                source = f"config:{key}"
        if is_missing(value) and path_candidates:
            for candidate in path_candidates:
                if is_missing(candidate):
                    continue
                resolved = which(str(candidate))
                if resolved:
                    value = normalize_path(resolved)
                    source = f"path:{candidate}"
                    break
        if is_missing(value) and not is_missing(default):
            value = default
            source = f"default:{default}" if isinstance(default, str) else "default"
    if required and is_missing(value):
        raise ValueError(f"missing required param: {name}")
    return value, source


def resolve_param(
    name: str,
    cli_value: Any,
    *,
    config: dict | None = None,
    config_keys: list[str] | None = None,
    state_record: dict | None = None,
    state_keys: list[str] | None = None,
    required: bool = False,
    normalize_as_path: bool = False,
    workspace: str | None = None,
) -> tuple[Any, str]:
    if not is_missing(cli_value):
        value = cli_value
        source = "cli"
    else:
        value = None
        source = ""
        if config and config_keys:
            value, config_key = _first_resolved(config, config_keys)
            if not is_missing(value):
                source = f"config:{config_key}"
        if is_missing(value) and state_record and state_keys:
            value, state_key = _first_resolved(state_record, state_keys)
            if not is_missing(value):
                source = f"state:{state_key}"
    if normalize_as_path and not is_missing(value):
        value = normalize_path_with_base(
            str(value), workspace_root(workspace)
        )
    if required and is_missing(value):
        raise ValueError(f"missing required param: {name}")
    return value, source


def compact_dict(data: dict | None) -> dict:
    if not isinstance(data, dict):
        return {}
    return {
        key: value
        for key, value in data.items()
        if value not in (None, "", [], {})
    }


def build_artifacts(**paths: str) -> dict:
    return {
        key: normalize_path(str(value))
        for key, value in paths.items()
        if not is_missing(value)
    }


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
    next_actions: list[str] | None = None,
    timing: dict | None = None,
    error: dict | None = None,
) -> dict:
    result = {
        "status": status,
        "action": action,
        "summary": summary,
        "details": compact_dict(details),
    }
    optional = {
        "context": compact_dict(context),
        "artifacts": compact_dict(artifacts),
        "metrics": compact_dict(metrics),
        "state": compact_dict(state),
        "timing": compact_dict(timing),
    }
    for key, value in optional.items():
        if value:
            result[key] = value
    if next_actions:
        result["next_actions"] = [item for item in next_actions if item]
    if error:
        result["error"] = compact_dict(error)
    return result


def make_timing(started_at: str, elapsed_ms: int | float) -> dict:
    return {
        "started_at": started_at,
        "finished_at": now_iso(),
        "elapsed_ms": int(elapsed_ms),
    }


def parameter_context(
    *,
    provider: str,
    workspace: str | None = None,
    parameter_sources: dict | None = None,
    config_path: str | None = None,
) -> dict:
    context = {
        "provider": provider,
        "workspace": str(workspace_root(workspace)),
    }
    if parameter_sources:
        context["parameter_sources"] = compact_dict(parameter_sources)
    if not is_missing(config_path):
        context["config_path"] = normalize_path(str(config_path))
    return context


def _auto_detect_toolchain_prefix() -> str:
    for prefix in ("arm-none-eabi-", "arm-eabi-", "arm-elf-"):
        if which(prefix + "gcc") or which(prefix + "size"):
            return prefix
    return "arm-none-eabi-"


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        if "--json" in sys.argv[1:]:
            output_json(make_result(status="error", action=Path(sys.argv[0]).stem, summary=message,
                                    error={"code": "invalid_arguments", "message": message}))
            raise SystemExit(2)
        super().error(message)


def run_cli(main) -> None:
    try:
        main()
    except (OSError, ValueError) as exc:
        if "--json" not in sys.argv[1:]:
            raise
        output_json(make_result(status="error", action=Path(sys.argv[0]).stem, summary=str(exc),
                                error={"code": "command_failed", "message": str(exc)}))
        raise SystemExit(1) from exc
