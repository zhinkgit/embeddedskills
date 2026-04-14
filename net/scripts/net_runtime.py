"""net skill 私有运行时工具。"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


SKILL_DIR = Path(__file__).resolve().parent.parent
SKILL_NAME = "net"
STATE_DIR_NAME = ".embeddedskills"
STATE_FILE_NAME = "state.json"
PROJECT_CONFIG_FILE = "config.json"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def is_missing(value: Any) -> bool:
    return value is None or value == ""


def load_json_file(path: str | Path) -> dict:
    """加载 JSON 文件，不存在返回空字典"""
    file_path = Path(path)
    if not file_path.exists():
        return {}
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_json_file(path: str | Path, data: dict) -> None:
    """保存 JSON 文件，自动创建目录"""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_local_config() -> dict:
    """加载 skill/config.json（环境级配置）"""
    return load_json_file(SKILL_DIR / "config.json")


def save_local_config(data: dict) -> None:
    """保存环境级配置到 skill/config.json"""
    save_json_file(SKILL_DIR / "config.json", data)


def workspace_root(workspace: str | None = None) -> Path:
    if not is_missing(workspace):
        return Path(str(workspace)).expanduser().resolve()
    return Path.cwd().resolve()


def load_project_config(workspace: str | None = None) -> dict:
    """从 workspace/.embeddedskills/config.json 读取本 skill 的工程级配置"""
    proj_config = load_json_file(workspace_root(workspace) / STATE_DIR_NAME / PROJECT_CONFIG_FILE)
    return proj_config.get(SKILL_NAME, {})


def save_project_config(workspace: str | None = None, values: dict | None = None) -> None:
    """写回工程级配置，只更新本 skill 的部分"""
    if values is None:
        return
    proj_path = workspace_root(workspace) / STATE_DIR_NAME / PROJECT_CONFIG_FILE
    proj_config = load_json_file(proj_path)
    proj_config[SKILL_NAME] = {**proj_config.get(SKILL_NAME, {}), **values}
    save_json_file(proj_path, proj_config)


def load_workspace_state(workspace: str | None = None) -> dict:
    """从 workspace/.embeddedskills/state.json 读取状态"""
    return load_json_file(workspace_root(workspace) / STATE_DIR_NAME / STATE_FILE_NAME)


def save_workspace_state(state: dict, workspace: str | None = None) -> Path:
    """保存状态"""
    file_path = workspace_root(workspace) / STATE_DIR_NAME / STATE_FILE_NAME
    save_json_file(file_path, state)
    return file_path


def update_state_entry(category: str, record: dict, workspace: str | None = None) -> dict:
    """更新状态条目"""
    state = load_workspace_state(workspace)
    state[category] = {**record, "timestamp": record.get("timestamp") or now_iso()}
    file_path = save_workspace_state(state, workspace)
    return {
        "workspace": str(workspace_root(workspace)),
        "file": str(file_path),
        "updated_keys": [category],
        category: state[category],
    }


def normalize_path(value: str | None, base: str | Path | None = None) -> str:
    """路径规范化"""
    if is_missing(value):
        return ""
    path = Path(str(value)).expanduser()
    if base and not path.is_absolute():
        path = Path(base) / path
    return str(path.resolve()) if path.is_absolute() else str(path)


def _first_resolved(mapping: dict, keys: list[str]) -> tuple[Any, str | None]:
    for key in keys:
        value = mapping.get(key)
        if not is_missing(value):
            return value, key
    return None, None


def resolve_param(
    name: str,
    cli_value: Any = None,
    local_config: dict | None = None,
    local_keys: list[str] | None = None,
    project_config: dict | None = None,
    project_keys: list[str] | None = None,
    state: dict | None = None,
    state_keys: list[str] | None = None,
    default: Any = None,
) -> tuple[Any, str]:
    """统一参数解析，优先级: CLI > 环境级 > 工程级 > state > default"""
    if not is_missing(cli_value):
        return cli_value, "cli"

    if local_config and local_keys:
        value, key = _first_resolved(local_config, local_keys)
        if not is_missing(value):
            return value, f"local:{key}"

    if project_config and project_keys:
        value, key = _first_resolved(project_config, project_keys)
        if not is_missing(value):
            return value, f"project:{key}"

    if state and state_keys:
        value, key = _first_resolved(state, state_keys)
        if not is_missing(value):
            return value, f"state:{key}"

    if not is_missing(default):
        return default, "default"

    return None, ""


def parameter_context(name: str, value: Any, source: str) -> dict:
    """记录参数来源"""
    return {"name": name, "value": value, "source": source}


def make_result(
    success: bool = True,
    action: str = "",
    summary: str = "",
    details: dict | None = None,
    error: dict | None = None,
) -> dict:
    """统一结果格式"""
    result = {
        "status": "ok" if success else "error",
        "action": action,
        "summary": summary,
    }
    if details:
        result["details"] = details
    if error:
        result["error"] = error
    return result


def make_timing(start_time: float) -> dict:
    """执行时间记录"""
    elapsed = datetime.now().timestamp() - start_time
    return {
        "started_at": datetime.fromtimestamp(start_time).astimezone().isoformat(timespec="seconds"),
        "finished_at": now_iso(),
        "elapsed_ms": int(elapsed * 1000),
    }


def check_tshark(exe: str = "tshark") -> bool:
    """检查 tshark 是否可用"""
    try:
        result = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=5)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def parse_tshark_interfaces(tshark_exe: str = "tshark") -> list[dict] | None:
    """解析 tshark -D 获取抓包接口列表"""
    try:
        result = subprocess.run(
            [tshark_exe, "-D"], capture_output=True, text=True, timeout=10
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0:
        return None

    interfaces = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # 格式: 1. \Device\NPF_{...} (描述)
        m = re.match(r"(\d+)\.\s+(.+?)(?:\s+\((.+?)\))?\s*$", line)
        if m:
            interfaces.append({
                "index": int(m.group(1)),
                "device": m.group(2).strip(),
                "description": m.group(3).strip() if m.group(3) else "",
            })
    return interfaces


def parse_ipconfig() -> list[dict]:
    """解析 ipconfig /all 获取网络接口信息"""
    try:
        result = subprocess.run(
            ["ipconfig", "/all"], capture_output=True, text=True, encoding="gbk", errors="replace"
        )
    except FileNotFoundError:
        return []

    interfaces = []
    current = None

    for line in result.stdout.splitlines():
        # 适配器标题行
        adapter_match = re.match(r"^(\S.*?)\s*适配器\s+(.+?)\s*[:：]", line)
        if not adapter_match:
            adapter_match = re.match(r"^(\S.*?)\s+adapter\s+(.+?)\s*[:：]", line, re.IGNORECASE)
        if adapter_match:
            if current:
                interfaces.append(current)
            current = {
                "type": adapter_match.group(1).strip(),
                "name": adapter_match.group(2).strip(),
                "description": "",
                "mac": "",
                "ipv4": "",
                "subnet": "",
                "gateway": "",
                "dhcp": "",
                "status": "up",
            }
            continue

        if current is None:
            continue

        line_stripped = line.strip()

        if re.match(r"(媒体状态|Media State)", line_stripped, re.IGNORECASE):
            if "断开" in line_stripped or "disconnected" in line_stripped.lower():
                current["status"] = "down"
        elif re.match(r"(描述|Description)", line_stripped, re.IGNORECASE):
            current["description"] = line_stripped.split(":", 1)[-1].strip() if ":" in line_stripped else ""
        elif re.match(r"(物理地址|Physical Address)", line_stripped, re.IGNORECASE):
            current["mac"] = line_stripped.split(":", 1)[-1].strip() if ":" in line_stripped else ""
        elif re.match(r"(IPv4 地址|IPv4 Address)", line_stripped, re.IGNORECASE):
            val = line_stripped.split(":", 1)[-1].strip() if ":" in line_stripped else ""
            current["ipv4"] = re.sub(r"\(.*?\)", "", val).strip()
        elif re.match(r"(子网掩码|Subnet Mask)", line_stripped, re.IGNORECASE):
            current["subnet"] = line_stripped.split(":", 1)[-1].strip() if ":" in line_stripped else ""
        elif re.match(r"(默认网关|Default Gateway)", line_stripped, re.IGNORECASE):
            current["gateway"] = line_stripped.split(":", 1)[-1].strip() if ":" in line_stripped else ""
        elif re.match(r"DHCP", line_stripped, re.IGNORECASE) and "已启用" in line_stripped or "Yes" in line_stripped:
            current["dhcp"] = "enabled"

    if current:
        interfaces.append(current)

    return interfaces


def get_net_config(
    cli_interface: str | None = None,
    cli_target: str | None = None,
    cli_capture_filter: str | None = None,
    cli_display_filter: str | None = None,
    cli_duration: int | None = None,
    cli_timeout_ms: int | None = None,
    cli_scan_ports: str | None = None,
    cli_capture_format: str | None = None,
    workspace: str | None = None,
) -> tuple[dict, dict]:
    """
    获取网络配置，按优先级解析参数。
    返回 (config_dict, sources_dict)
    """
    local_cfg = load_local_config()
    proj_cfg = load_project_config(workspace)
    state = load_workspace_state(workspace)

    sources = {}

    # 解析各个参数
    interface, src = resolve_param(
        "interface", cli_interface,
        project_config=proj_cfg, project_keys=["interface"],
        state=state, state_keys=["last_net_interface"],
    )
    sources["interface"] = src or "unknown"

    target, src = resolve_param(
        "target", cli_target,
        project_config=proj_cfg, project_keys=["target"],
        state=state, state_keys=["last_net_target"],
    )
    sources["target"] = src or "unknown"

    capture_filter, src = resolve_param(
        "capture_filter", cli_capture_filter,
        project_config=proj_cfg, project_keys=["capture_filter"],
        state=state, state_keys=["last_capture_filter"],
        default="",
    )
    sources["capture_filter"] = src or "default"

    display_filter, src = resolve_param(
        "display_filter", cli_display_filter,
        project_config=proj_cfg, project_keys=["display_filter"],
        state=state, state_keys=["last_display_filter"],
        default="",
    )
    sources["display_filter"] = src or "default"

    duration, src = resolve_param(
        "duration", cli_duration,
        project_config=proj_cfg, project_keys=["duration"],
        state=state, state_keys=["last_duration"],
        default=30,
    )
    sources["duration"] = src or "default"

    timeout_ms, src = resolve_param(
        "timeout_ms", cli_timeout_ms,
        project_config=proj_cfg, project_keys=["timeout_ms"],
        state=state, state_keys=["last_timeout_ms"],
        default=1000,
    )
    sources["timeout_ms"] = src or "default"

    scan_ports, src = resolve_param(
        "scan_ports", cli_scan_ports,
        project_config=proj_cfg, project_keys=["scan_ports"],
        state=state, state_keys=["last_scan_ports"],
        default="",
    )
    sources["scan_ports"] = src or "default"

    capture_format, src = resolve_param(
        "capture_format", cli_capture_format,
        project_config=proj_cfg, project_keys=["capture_format"],
        state=state, state_keys=["last_capture_format"],
        default="pcapng",
    )
    sources["capture_format"] = src or "default"

    log_dir, src = resolve_param(
        "log_dir", None,
        project_config=proj_cfg, project_keys=["log_dir"],
        default=".embeddedskills/logs/net",
    )
    sources["log_dir"] = src or "default"

    # 获取工具路径（环境级配置）
    tshark_exe = local_cfg.get("tshark_exe", "tshark")
    capinfos_exe = local_cfg.get("capinfos_exe", "capinfos")

    config = {
        "interface": interface,
        "target": target,
        "capture_filter": capture_filter,
        "display_filter": display_filter,
        "duration": duration,
        "timeout_ms": timeout_ms,
        "scan_ports": scan_ports,
        "capture_format": capture_format,
        "log_dir": log_dir,
        "tshark_exe": tshark_exe,
        "capinfos_exe": capinfos_exe,
    }

    return config, sources


def output_json(data: dict, *, indent: int = 2) -> None:
    """输出 JSON 到 stdout"""
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=indent), flush=True)
