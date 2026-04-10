"""OpenOCD GDB Server 启动与就绪检测"""

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time


def build_openocd_cmd(exe: str, board: str = "", interface: str = "", target: str = "",
                      search: str = "", adapter_speed: str = "", transport: str = "",
                      gdb_port: int = 3333, telnet_port: int = 4444) -> list:
    """构建 OpenOCD GDB Server 命令行"""
    cmd = [exe]

    if search:
        cmd.extend(["-s", search])

    # board 优先
    if board:
        cmd.extend(["-f", board])
    else:
        if interface:
            cmd.extend(["-f", interface])
        if target:
            cmd.extend(["-f", target])

    # adapter speed
    if adapter_speed:
        cmd.extend(["-c", f"adapter speed {adapter_speed}"])

    # transport
    if transport:
        cmd.extend(["-c", f"transport select {transport}"])

    # 端口配置
    cmd.extend(["-c", f"gdb_port {gdb_port}"])
    cmd.extend(["-c", f"telnet_port {telnet_port}"])

    return cmd


def start_openocd_server(cmd: list) -> subprocess.Popen:
    """启动 OpenOCD 进程"""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    return proc


def wait_server_ready(proc: subprocess.Popen, gdb_port: int, timeout: int = 15) -> tuple:
    """等待 GDB Server 就绪，返回 (ready: bool, errors: list)

    同时检查就绪标志和前序 Error 输出，
    避免"端口已开但目标未连通"的假成功。
    """
    start = time.time()
    errors = []
    ready = False

    while time.time() - start < timeout:
        if proc.poll() is not None:
            # 进程已退出，读取剩余输出
            remaining = proc.stderr.read()
            for line in remaining.splitlines():
                if "Error:" in line:
                    errors.append(line.strip())
            return False, errors

        line = proc.stderr.readline()
        if not line:
            time.sleep(0.1)
            continue

        line = line.strip()

        # 收集错误
        if "Error:" in line:
            errors.append(line)

        # 检测就绪标志
        if f"Listening on port {gdb_port}" in line or "listening on" in line.lower():
            ready = True

    # 超时但进程还活着
    if not ready:
        return False, errors

    # 就绪了，但如果有严重错误仍然报错
    critical_errors = [e for e in errors if any(k in e.lower() for k in
                       ["open failed", "init mode failed", "no device found", "cannot connect"])]
    if critical_errors:
        return False, critical_errors

    return True, errors


def cleanup(proc: subprocess.Popen):
    """清理 OpenOCD 进程"""
    if proc and proc.poll() is None:
        try:
            if sys.platform == "win32":
                proc.terminate()
            else:
                proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            proc.kill()


def output_json(data: dict):
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="OpenOCD GDB Server 启动")
    parser.add_argument("--exe", default="openocd", help="openocd 路径")
    parser.add_argument("--board", default="", help="board 配置文件")
    parser.add_argument("--interface", default="", help="interface 配置文件")
    parser.add_argument("--target", default="", help="target 配置文件")
    parser.add_argument("--search", default="", help="额外配置脚本搜索目录")
    parser.add_argument("--adapter-speed", default="", help="调试速率 kHz")
    parser.add_argument("--transport", default="", choices=["", "swd", "jtag"], help="传输协议")
    parser.add_argument("--gdb-port", type=int, default=3333, help="GDB 端口")
    parser.add_argument("--telnet-port", type=int, default=4444, help="Telnet 端口")
    parser.add_argument("--json", action="store_true", dest="as_json")

    args = parser.parse_args()

    # 参数校验
    if not args.board and not args.interface and not args.target:
        result = {
            "status": "error",
            "action": "gdb-server",
            "error": {"code": "missing_config", "message": "必须提供 --board 或 --interface + --target"},
        }
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {result['error']['message']}", file=sys.stderr)
        sys.exit(1)

    cmd = build_openocd_cmd(
        exe=args.exe, board=args.board, interface=args.interface, target=args.target,
        search=args.search, adapter_speed=args.adapter_speed, transport=args.transport,
        gdb_port=args.gdb_port, telnet_port=args.telnet_port,
    )

    proc = None
    try:
        proc = start_openocd_server(cmd)
        ready, errors = wait_server_ready(proc, args.gdb_port)

        if not ready:
            error_msg = "; ".join(errors) if errors else "GDB Server 启动失败或超时"
            result = {
                "status": "error",
                "action": "gdb-server",
                "error": {"code": "gdbserver_failed", "message": error_msg},
            }
            cleanup(proc)
        else:
            result = {
                "status": "ok",
                "action": "gdb-server",
                "summary": "GDB Server 已就绪",
                "details": {
                    "gdb_port": args.gdb_port,
                    "telnet_port": args.telnet_port,
                    "pid": proc.pid,
                    "connect_cmd": f"arm-none-eabi-gdb -ex 'target remote localhost:{args.gdb_port}'",
                },
            }
            if errors:
                result["details"]["warnings"] = errors

        if args.as_json:
            output_json(result)
        else:
            if result["status"] == "ok":
                print(f"[gdb-server] GDB Server 已就绪")
                print(f"  GDB 端口: {args.gdb_port}")
                print(f"  Telnet 端口: {args.telnet_port}")
                print(f"  PID: {proc.pid}")
                print(f"  连接: arm-none-eabi-gdb -ex 'target remote localhost:{args.gdb_port}'")
                if errors:
                    print(f"  警告: {'; '.join(errors)}")
            else:
                err = result.get("error", {})
                print(f"[gdb-server] 失败 — {err.get('message', '未知错误')}", file=sys.stderr)
                sys.exit(1)

        # GDB Server 模式：如果成功启动，保持运行直到用户终止
        if result["status"] == "ok":
            try:
                proc.wait()
            except KeyboardInterrupt:
                pass

    except FileNotFoundError:
        result = {
            "status": "error",
            "action": "gdb-server",
            "error": {"code": "exe_not_found", "message": f"openocd 不存在或不在 PATH 中: {args.exe}"},
        }
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {result['error']['message']}", file=sys.stderr)
        sys.exit(1)
    finally:
        if proc:
            cleanup(proc)


if __name__ == "__main__":
    main()
