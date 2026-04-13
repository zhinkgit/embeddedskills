"""OpenOCD Semihosting 输出捕获。"""

from __future__ import annotations

import argparse
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from openocd_runtime import (  # noqa: E402
    emit_stream_record,
    default_config_path,
    get_state_entry,
    load_json_file,
    load_workspace_state,
    make_result,
    make_timing,
    normalize_path,
    now_iso,
    output_json,
    parameter_context,
    resolve_param,
    update_state_entry,
    workspace_root,
)


LOG_PREFIXES = re.compile(r"^(Info|Warn|Error|Debug)\s*:", re.IGNORECASE)
STATUS_PATTERNS = [
    "Listening on port",
    "halted due to",
    "target state:",
    "shutdown command invoked",
    "GDB",
    "accepting",
    "dropped",
]


def build_openocd_cmd(
    exe: str,
    board: str = "",
    interface: str = "",
    target: str = "",
    search: str = "",
    adapter_speed: str = "",
    transport: str = "",
    gdb_port: int = 3333,
    telnet_port: int = 4444,
) -> list[str]:
    cmd = [exe]
    if search:
        cmd.extend(["-s", search])
    if board:
        cmd.extend(["-f", board])
    else:
        if interface:
            cmd.extend(["-f", interface])
        if target:
            cmd.extend(["-f", target])
    if adapter_speed:
        cmd.extend(["-c", f"adapter speed {adapter_speed}"])
    if transport:
        cmd.extend(["-c", f"transport select {transport}"])
    cmd.extend(["-c", f"gdb_port {gdb_port}"])
    cmd.extend(["-c", f"telnet_port {telnet_port}"])
    return cmd


def start_openocd_server(cmd: list[str]) -> subprocess.Popen:
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )


def wait_server_ready(proc: subprocess.Popen, telnet_port: int, timeout: int = 15) -> tuple[bool, list[str]]:
    started = time.time()
    errors: list[str] = []
    ready = False
    while time.time() - started < timeout:
        if proc.poll() is not None:
            remaining = proc.stderr.read()
            errors.extend([line.strip() for line in remaining.splitlines() if "Error:" in line])
            return False, errors
        line = proc.stderr.readline()
        if not line:
            time.sleep(0.1)
            continue
        stripped = line.strip()
        if "Error:" in stripped:
            errors.append(stripped)
        if f"Listening on port {telnet_port}" in stripped or "listening on" in stripped.lower():
            ready = True
            break
    if not ready:
        return False, errors
    critical = ["open failed", "init mode failed", "no device found", "cannot connect", "error connecting dp", "examination failed"]
    critical_errors = [item for item in errors if any(keyword in item.lower() for keyword in critical)]
    if critical_errors:
        return False, critical_errors
    return True, errors


def cleanup_proc(proc: subprocess.Popen | None) -> None:
    if proc and proc.poll() is None:
        try:
            if sys.platform == "win32":
                proc.terminate()
            else:
                proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            proc.kill()


def _read_until_prompt(sock: socket.socket) -> str:
    buf = b""
    while True:
        decoded = buf.decode("utf-8", errors="replace")
        if decoded.endswith("> ") or "\n> " in decoded or "\r> " in decoded:
            prompt_pos = decoded.rfind("\n> ")
            if prompt_pos == -1:
                prompt_pos = decoded.rfind("\r> ")
            if prompt_pos == -1 and decoded.endswith("> "):
                prompt_pos = len(decoded) - 2
            return decoded[:prompt_pos].strip() if prompt_pos >= 0 else decoded.strip()
        try:
            chunk = sock.recv(4096)
            if not chunk:
                return buf.decode("utf-8", errors="replace").strip()
            buf += chunk
        except socket.timeout:
            return buf.decode("utf-8", errors="replace").strip()


def telnet_send_multi(host: str, port: int, commands: list[str], timeout: float = 5.0) -> list[str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    responses: list[str] = []
    try:
        sock.connect((host, port))
        _read_until_prompt(sock)
        for command in commands:
            sock.sendall((command + "\n").encode("utf-8"))
            responses.append(_read_until_prompt(sock))
        return responses
    finally:
        sock.close()


def is_semihosting_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if LOG_PREFIXES.match(stripped):
        return False
    return not any(item in stripped for item in STATUS_PATTERNS)


def _state_lookup(state: dict) -> dict:
    last_debug = get_state_entry(state, "last_debug")
    last_flash = get_state_entry(state, "last_flash")
    return {
        "board": last_debug.get("board") or last_flash.get("board"),
        "interface": last_debug.get("interface") or last_flash.get("interface"),
        "target": last_debug.get("target") or last_flash.get("target"),
        "search": last_debug.get("search"),
        "adapter_speed": last_debug.get("adapter_speed") or last_flash.get("adapter_speed"),
        "transport": last_debug.get("transport") or last_flash.get("transport"),
        "gdb_port": last_debug.get("gdb_port"),
        "telnet_port": last_debug.get("telnet_port"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenOCD Semihosting 输出捕获")
    parser.add_argument("--exe", default=None, help="openocd 路径")
    parser.add_argument("--board", default=None, help="board 配置文件")
    parser.add_argument("--interface", default=None, help="interface 配置文件")
    parser.add_argument("--target", default=None, help="target 配置文件")
    parser.add_argument("--search", default=None, help="额外配置脚本搜索目录")
    parser.add_argument("--adapter-speed", default=None, help="调试速率 kHz")
    parser.add_argument("--transport", default=None, choices=["", "swd", "jtag"], help="传输协议")
    parser.add_argument("--gdb-port", type=int, default=None, help="GDB 端口")
    parser.add_argument("--telnet-port", type=int, default=None, help="Telnet 端口")
    parser.add_argument("--timeout", type=int, default=0, help="捕获时长秒数，0=持续到 Ctrl+C")
    parser.add_argument("--config", default=None, help="skill config.json 路径")
    parser.add_argument("--workspace", default=None, help="workspace 根目录，默认当前目录")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    started_at = now_iso()
    started_ts = time.time()
    workspace = workspace_root(args.workspace)
    config_path = normalize_path(args.config or str(default_config_path(__file__)))
    config = load_json_file(config_path)
    state = load_workspace_state(str(workspace))
    state_lookup = _state_lookup(state)

    parameter_sources: dict[str, str] = {}
    try:
        exe, parameter_sources["exe"] = resolve_param("exe", args.exe, config=config, config_keys=["exe"], required=True)
        board, parameter_sources["board"] = resolve_param(
            "board",
            args.board,
            config=config,
            config_keys=["default_board"],
            state_record=state_lookup,
            state_keys=["board"],
        )
        interface, parameter_sources["interface"] = resolve_param(
            "interface",
            args.interface,
            config=config,
            config_keys=["default_interface"],
            state_record=state_lookup,
            state_keys=["interface"],
        )
        target, parameter_sources["target"] = resolve_param(
            "target",
            args.target,
            config=config,
            config_keys=["default_target"],
            state_record=state_lookup,
            state_keys=["target"],
        )
        search, parameter_sources["search"] = resolve_param(
            "search",
            args.search,
            config=config,
            config_keys=["scripts_dir"],
            state_record=state_lookup,
            state_keys=["search"],
        )
        adapter_speed, parameter_sources["adapter_speed"] = resolve_param(
            "adapter_speed",
            args.adapter_speed,
            config=config,
            config_keys=["adapter_speed"],
            state_record=state_lookup,
            state_keys=["adapter_speed"],
        )
        transport, parameter_sources["transport"] = resolve_param(
            "transport",
            args.transport,
            config=config,
            config_keys=["transport"],
            state_record=state_lookup,
            state_keys=["transport"],
        )
        gdb_port, parameter_sources["gdb_port"] = resolve_param(
            "gdb_port",
            args.gdb_port,
            config=config,
            config_keys=["gdb_port"],
            state_record=state_lookup,
            state_keys=["gdb_port"],
        )
        telnet_port, parameter_sources["telnet_port"] = resolve_param(
            "telnet_port",
            args.telnet_port,
            config=config,
            config_keys=["telnet_port"],
            state_record=state_lookup,
            state_keys=["telnet_port"],
        )
    except ValueError as exc:
        result = make_result(
            status="error",
            action="semihosting",
            summary=str(exc),
            details={},
            context=parameter_context(provider="openocd", workspace=str(workspace), parameter_sources=parameter_sources, config_path=config_path),
            error={"code": "missing_param", "message": str(exc)},
            timing=make_timing(started_at, (time.time() - started_ts) * 1000),
        )
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {exc}", file=sys.stderr)
        sys.exit(1)

    if not board and not interface and not target:
        message = "必须提供 --board 或 --interface + --target"
        result = make_result(
            status="error",
            action="semihosting",
            summary=message,
            details={},
            context=parameter_context(provider="openocd", workspace=str(workspace), parameter_sources=parameter_sources, config_path=config_path),
            error={"code": "missing_config", "message": message},
            timing=make_timing(started_at, (time.time() - started_ts) * 1000),
        )
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {message}", file=sys.stderr)
        sys.exit(1)

    cmd = build_openocd_cmd(
        exe=exe,
        board=board or "",
        interface=interface or "",
        target=target or "",
        search=search or "",
        adapter_speed=str(adapter_speed or ""),
        transport=transport or "",
        gdb_port=int(gdb_port or 3333),
        telnet_port=int(telnet_port or 4444),
    )

    proc: subprocess.Popen | None = None
    try:
        proc = start_openocd_server(cmd)
        ready, errors = wait_server_ready(proc, int(telnet_port or 4444))
        if not ready:
            message = "; ".join(errors) if errors else "OpenOCD 启动失败或超时"
            result = make_result(
                status="error",
                action="semihosting",
                summary="Semihosting 服务启动失败",
                details={"errors": errors},
                context=parameter_context(provider="openocd", workspace=str(workspace), parameter_sources=parameter_sources, config_path=config_path),
                error={"code": "server_failed", "message": message},
                timing=make_timing(started_at, (time.time() - started_ts) * 1000),
            )
            if args.as_json:
                output_json(result)
            else:
                print(f"错误: {message}", file=sys.stderr)
            sys.exit(1)

        try:
            telnet_send_multi("localhost", int(telnet_port or 4444), ["halt", "arm semihosting enable", "resume"])
        except (ConnectionError, OSError) as exc:
            result = make_result(
                status="error",
                action="semihosting",
                summary="Telnet 连接失败",
                details={},
                context=parameter_context(provider="openocd", workspace=str(workspace), parameter_sources=parameter_sources, config_path=config_path),
                error={"code": "telnet_failed", "message": f"Telnet 连接失败: {exc}"},
                timing=make_timing(started_at, (time.time() - started_ts) * 1000),
            )
            if args.as_json:
                output_json(result)
            else:
                print(f"错误: {result['error']['message']}", file=sys.stderr)
            sys.exit(1)

        update_state_entry(
            "last_observe",
            {
                "provider": "openocd",
                "action": "semihosting",
                "board": board or "",
                "interface": interface or "",
                "target": target or "",
                "search": search or "",
                "adapter_speed": adapter_speed or "",
                "transport": transport or "",
                "channel_type": "semihosting",
                "stream_type": "text",
                "source": "openocd",
            },
            str(workspace),
        )

        if not args.as_json:
            print("Semihosting 已启用，等待输出（Ctrl+C 退出）:", file=sys.stderr, flush=True)
            print("-" * 40, file=sys.stderr, flush=True)

        monitor_started = time.time()
        while True:
            if args.timeout > 0 and (time.time() - monitor_started) >= args.timeout:
                break

            if proc.poll() is not None:
                remaining = proc.stderr.read()
                for line in remaining.splitlines(keepends=True):
                    if is_semihosting_line(line):
                        emit_stream_record(
                            source="openocd",
                            channel_type="semihosting",
                            text=line,
                            as_json=args.as_json,
                            stream_type="text",
                        )
                break

            line = proc.stderr.readline()
            if not line:
                time.sleep(0.02)
                continue
            if is_semihosting_line(line):
                emit_stream_record(
                    source="openocd",
                    channel_type="semihosting",
                    text=line,
                    as_json=args.as_json,
                    stream_type="text",
                )

    except FileNotFoundError:
        message = f"openocd 不存在或不在 PATH 中: {exe}"
        result = make_result(
            status="error",
            action="semihosting",
            summary=message,
            details={},
            context=parameter_context(provider="openocd", workspace=str(workspace), parameter_sources=parameter_sources, config_path=config_path),
            error={"code": "exe_not_found", "message": message},
            timing=make_timing(started_at, (time.time() - started_ts) * 1000),
        )
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {message}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        if not args.as_json:
            print("\n已停止 semihosting 捕获", file=sys.stderr, flush=True)
    finally:
        if proc and proc.poll() is None:
            try:
                telnet_send_multi("localhost", int(telnet_port or 4444), ["halt", "arm semihosting disable", "shutdown"], timeout=2.0)
            except (ConnectionError, OSError):
                pass
        cleanup_proc(proc)


if __name__ == "__main__":
    main()
