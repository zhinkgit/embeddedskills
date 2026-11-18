from __future__ import annotations

import base64
import math
import os
import secrets
import sys
import threading
from pathlib import Path

import terminal_sessions as sessions
import terminal_ssh as ssh
from terminal_runtime import Parser, TerminalError, emit, executable, failure, result, settings
from terminal_transport import scan_serial


LINE_ENDINGS = {"none": b"", "lf": b"\n", "cr": b"\r", "crlf": b"\r\n"}


def nonnegative(value: str) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("timeout")
    return seconds


def positive(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise ValueError("positive integer")
    return number


def port_number(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 65535:
        raise ValueError("port")
    return number


def add_connection(parser, *, scp=False):
    parser.add_argument("alias")
    parser.add_argument("--ssh-exe")
    if scp:
        parser.add_argument("--scp-exe")
    parser.add_argument("--accept-new-host-key", action="store_true")
    parser.add_argument("--known-hosts-file")


def add_command(parser, backend, *, session=False):
    if backend == "ssh":
        add_connection(parser)
        if session:
            parser.add_argument("--connect-timeout", type=positive, default=10)
        if not session:
            parser.add_argument("remote_command")
    else:
        parser.add_argument("--cwd", type=Path)
        parser.add_argument("argv", nargs="*")
    if session:
        parser.add_argument("--name")
        parser.add_argument("--encoding", default="utf-8")
        parser.add_argument("--log", type=Path, metavar="FILE")
    else:
        parser.add_argument("--timeout", type=nonnegative, default=30)


def build_parser() -> Parser:
    parser = Parser(description="Agent 终端，支持 SSH、串口和本地进程")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--ssh-config", type=Path)
    parser.add_argument("--json", action="store_true")
    commands = parser.add_subparsers(dest="action", required=True)

    for action in ("exec", "start"):
        group = commands.add_parser(action).add_subparsers(dest="backend", required=True)
        for backend in ("ssh", "local"):
            add_command(group.add_parser(backend), backend, session=action == "start")
        if action == "start":
            serial_start = group.add_parser("serial")
            serial_start.add_argument("--name")
            serial_start.add_argument("--encoding", default="utf-8")
            serial_start.add_argument("--log", type=Path, metavar="FILE")
            serial_start.add_argument("--port")
            serial_start.add_argument("--baudrate", type=positive)
            serial_start.add_argument("--bytesize", type=int, choices=(5, 6, 7, 8))
            serial_start.add_argument("--parity", choices=("N", "E", "O", "M", "S"))
            serial_start.add_argument("--stopbits", type=float, choices=(1, 1.5, 2))

    commands.add_parser("list")
    for action in ("status", "read", "send", "stop", "attach"):
        command = commands.add_parser(action)
        command.add_argument("name")
        if action in ("read", "send", "attach"):
            command.add_argument("--encoding")
        if action in ("read", "send"):
            command.add_argument("--hex", action="store_true")
        if action in ("send", "attach"):
            command.add_argument("--eol", choices=LINE_ENDINGS, default="lf" if action == "attach" else "none")
        if action == "read":
            command.add_argument("--timeout", type=nonnegative, default=0)
        if action == "send":
            command.add_argument("data")

    commands.add_parser("serial").add_subparsers(dest="serial_action", required=True).add_parser("scan")
    ssh_group = commands.add_parser("ssh").add_subparsers(dest="ssh_action", required=True)
    host_group = ssh_group.add_parser("hosts").add_subparsers(dest="host_action", required=True)
    host_group.add_parser("list")
    for action in ("find", "resolve"):
        host_group.add_parser(action).add_argument("query", nargs="+")
    show = host_group.add_parser("show")
    add_connection(show)
    show.add_argument("--timeout", type=nonnegative, default=10)
    add = host_group.add_parser("add")
    add.add_argument("alias")
    add.add_argument("--host", required=True)
    add.add_argument("--user", required=True)
    add.add_argument("--port", type=port_number, default=22)
    for option in ("key", "proxy-jump", "description", "aliases", "groups", "tags", "location"):
        add.add_argument(f"--{option}")
    probe = ssh_group.add_parser("probe")
    add_connection(probe)
    probe.add_argument("--timeout", type=nonnegative, default=30)
    probe.add_argument("--connect-timeout", type=positive, default=10)
    for action in ("upload", "download"):
        transfer = ssh_group.add_parser(action)
        add_connection(transfer, scp=True)
        transfer.add_argument("local_path", type=Path)
        transfer.add_argument("remote")
        transfer.add_argument("--timeout", type=nonnegative, default=60)
        transfer.add_argument("--recursive", action="store_true")
    tunnels = ssh_group.add_parser("tunnel").add_subparsers(dest="tunnel_action", required=True)
    for action in ("start", "status", "stop"):
        tunnel = tunnels.add_parser(action)
        tunnel.add_argument("--name", required=True)
        if action == "start":
            add_connection(tunnel)
            tunnel.add_argument("--local-port", type=port_number, required=True)
            tunnel.add_argument("--remote-port", type=port_number, required=True)
            tunnel.add_argument("--remote-host", default="127.0.0.1")
            tunnel.add_argument("--connect-timeout", type=positive, default=10)
    return parser


def local_argv(args) -> list[str]:
    if args.argv:
        return [executable(args.argv[0]), *args.argv[1:]]
    if args.action == "exec":
        raise TerminalError("missing_command", "请在 -- 后提供本地程序及参数")
    if os.name == "nt":
        return [executable("powershell.exe"), "-NoLogo", "-NoProfile", "-Command", "-"]
    return [executable(os.environ.get("SHELL") or "/bin/sh")]


def make_spec(args) -> dict:
    backend = args.backend
    spec = {"backend": backend, "name": args.name or f"{backend}-{secrets.token_hex(4)}",
            "encoding": sessions.encoding_for(args.encoding)}
    if args.log:
        spec["log_file"] = str(args.log.resolve())
    if backend == "serial":
        config = args.project.get("serial", {})
        if not isinstance(config, dict):
            raise TerminalError("invalid_config", "terminal.serial 应为配置对象")
        for key, default in {"port": None, "baudrate": 115200, "bytesize": 8, "parity": "N", "stopbits": 1}.items():
            value = getattr(args, key)
            spec[key] = value if value is not None else config.get(key, default)
        if not spec["port"]:
            raise TerminalError("missing_port", "请指定 --port 或配置 terminal.serial.port")
    elif backend == "ssh":
        spec["argv"] = ssh.ssh_argv(args, "-tt", "-vv", "-o", f"ConnectTimeout={args.connect_timeout}")
        spec["startup_timeout"] = args.connect_timeout
    else:
        spec["argv"] = local_argv(args)
        spec["cwd"] = str((args.cwd or args.workspace).resolve())
    return spec


def transmit(record, text, encoding, eol, hex_mode=False) -> dict:
    data = bytes.fromhex(text) if hex_mode else text.encode(encoding)
    payload = data + LINE_ENDINGS[eol]
    if len(payload) > sessions.MAX_BUFFER:
        raise TerminalError("payload_too_large", "单次写入最多 1 MiB")
    return sessions.call(record, "send", data=base64.b64encode(payload).decode("ascii"))


def attach(record, encoding, eol) -> dict:
    finished = threading.Event()

    def receive():
        while not finished.is_set():
            response = sessions.call(record, "read", timeout=0.2, encoding=encoding)
            if response["status"] == "error":
                print(response["summary"], file=sys.stderr)
                return
            details = response["details"]
            if details["text"]:
                print(details["text"], end="", flush=True)
            if not details["alive"]:
                return

    thread = threading.Thread(target=receive, daemon=True)
    thread.start()
    try:
        for line in sys.stdin:
            response = transmit(record, line.rstrip("\r\n"), encoding, eol)
            if response["status"] == "error":
                return response
    except KeyboardInterrupt:
        pass
    finally:
        finished.set()
        thread.join(timeout=1)
    return result("attach", "已离开前台会话")


def dispatch_ssh(args) -> dict:
    if args.ssh_action == "hosts":
        return ssh.hosts(args)
    if args.ssh_action == "probe":
        return ssh.probe(args)
    if args.ssh_action in ("upload", "download"):
        return ssh.transfer(args)
    action = args.tunnel_action
    if action == "start":
        output = sessions.start(args.workspace, {
            "backend": "tunnel", "name": args.name, "encoding": "utf-8",
            "argv": ssh.tunnel_argv(args), "local_port": args.local_port,
            "tunnel_timeout": args.connect_timeout + 2,
        })
        output["details"]["local_port"] = args.local_port
    elif action == "stop":
        output = sessions.stop(args.workspace, args.name, "tunnel")
    else:
        output = sessions.call(sessions.resolve_record(args.workspace, args.name, "tunnel"), "status")
    output["action"] = f"ssh tunnel {action}"
    return output


def dispatch(args) -> dict:
    action = args.action
    if action == "exec":
        if args.backend == "ssh":
            argv = [*ssh.ssh_argv(args, "-T"), args.remote_command]
            output = ssh.run(argv, args.timeout)
        else:
            output = ssh.run(local_argv(args), args.timeout, args.cwd or args.workspace)
        return ssh.finished(action, output)
    if action == "start":
        return sessions.start(args.workspace, make_spec(args))
    if action == "serial":
        return result("serial scan", "串口扫描完成", ports=scan_serial())
    if action == "ssh":
        return dispatch_ssh(args)
    if action == "list":
        return sessions.session_list(args.workspace)
    if action == "stop":
        return sessions.stop(args.workspace, args.name)
    record = sessions.resolve_record(args.workspace, args.name)
    if action == "status":
        return sessions.call(record, action)
    if record["backend"] == "tunnel":
        raise TerminalError("invalid_backend", "转发只支持状态查询与停止")
    encoding = sessions.encoding_for(args.encoding or record["encoding"])
    if action == "send":
        return transmit(record, args.data, encoding, args.eol, args.hex)
    if action == "read":
        return sessions.call(record, action, timeout=args.timeout, encoding=encoding, hex=args.hex)
    if args.json:
        raise TerminalError("interactive_only", "attach 为前台行模式，请用 send/read 获取 JSON")
    return attach(record, encoding, args.eol)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Options after -- are arguments of the local program, including --json.
    boundary = argv.index("--") if "--" in argv else len(argv)
    as_json = "--json" in argv[:boundary]
    if as_json:
        argv = [word for i, word in enumerate(argv) if i >= boundary or word != "--json"]
        argv.insert(0, "--json")
    action = "terminal"
    try:
        args = build_parser().parse_args(argv)
        action = args.action
        if action == "ssh":
            action = " ".join(filter(None, ("ssh", args.ssh_action,
                               getattr(args, "host_action", None), getattr(args, "tunnel_action", None))))
        args.workspace = args.workspace.resolve()
        if args.ssh_config:
            args.ssh_config = args.ssh_config.expanduser().resolve()
        args.local, args.project = settings(args)
        payload = dispatch(args)
    except TerminalError as exc:
        payload = failure(action, exc.code, str(exc), **exc.details)
    except OSError as exc:
        payload = failure(action, "io_failed", str(exc))
    except (ValueError, LookupError) as exc:
        payload = failure(action, "invalid_arguments", str(exc))
    return emit(payload, as_json)


if __name__ == "__main__":
    raise SystemExit(main())
