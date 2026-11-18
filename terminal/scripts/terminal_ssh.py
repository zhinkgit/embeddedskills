from __future__ import annotations

import subprocess
from pathlib import Path

from terminal_hosts import (
    add_host, has_control_characters, parse_hosts, read_lines, resolve_hosts,
    search_hosts, ssh_config_path, validate_host_alias,
)
from terminal_runtime import TerminalError, executable, result


REMOTE_PROBE = r'''set -u
value() { printf '%s=%s\n' "$1" "$2"; }
value os "$(uname -s 2>/dev/null || true)"
value arch "$(uname -m 2>/dev/null || true)"
value kernel "$(uname -r 2>/dev/null || true)"
value hostname "$(hostname 2>/dev/null || true)"
value cpu_count "$(getconf _NPROCESSORS_ONLN 2>/dev/null || true)"
value memory_kib "$(awk '/^MemTotal:/ {print $2; exit}' /proc/meminfo 2>/dev/null || true)"
value python "$(python3 --version 2>&1 || true)"
value node "$(node --version 2>/dev/null || true)"
value gpu "$(nvidia-smi -L 2>/dev/null | paste -sd ';' - || true)"
value container "$(test -f /.dockerenv && printf docker || true)"
'''


def parse_probe_output(output: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator and key:
            result[key] = value
    return result



def common_argv(args, program: str, options=()) -> list[str]:
    local = args.local
    path = getattr(args, "ssh_config", None)
    if path and not path.is_file():
        raise TerminalError("config_not_found", f"SSH 配置不存在：{path}")
    override = getattr(args, f"{program}_exe", None) or local.get(f"{program}_exe")
    argv = [executable(program, override)]
    if path:
        argv.extend(["-F", str(path)])
    argv.extend(options)
    known_hosts = getattr(args, "known_hosts_file", None)
    if known_hosts and (has_control_characters(known_hosts) or '"' in known_hosts):
        raise TerminalError("invalid_arguments", "known-hosts 路径包含非法字符")
    if known_hosts:
        known_hosts = Path(known_hosts).expanduser().resolve().as_posix()
        argv.extend(["-o", f'UserKnownHostsFile="{known_hosts}"'])
    policy = "accept-new" if getattr(args, "accept_new_host_key", False) else "yes"
    argv.extend(["-o", f"StrictHostKeyChecking={policy}", "-o", "BatchMode=yes"])
    return argv


def ssh_argv(args, *options: str) -> list[str]:
    validate_host_alias(args.alias)
    return [*common_argv(args, "ssh", options), args.alias]


def run(argv: list[str], timeout: float, cwd=None) -> dict:
    try:
        proc = subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        def partial(value):
            return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else (value or "")
        raise TerminalError("timeout", f"命令超过 {timeout:g} 秒", exit_code=None,
                            stdout=partial(exc.stdout), stderr=partial(exc.stderr)) from exc
    return {"exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


def finished(action: str, details: dict) -> dict:
    if details["exit_code"]:
        raise TerminalError("command_failed", f"命令退出码：{details['exit_code']}", **details)
    return result(action, "命令执行完成", **details)


def hosts(args) -> dict:
    path = args.ssh_config or ssh_config_path()
    action = f"ssh hosts {args.host_action}"
    if args.host_action == "add":
        return result(action, "已添加 SSH 主机", **add_host(args, path))
    if args.host_action == "show":
        details = run(ssh_argv(args, "-G"), args.timeout)
        finished(action, details)
        config = {}
        for line in details["stdout"].splitlines():
            key, _, value = line.partition(" ")
            if key in {"hostname", "user", "port", "identityfile", "proxyjump"}:
                config.setdefault(key, []).append(value)
        return result(action, "已解析 OpenSSH 配置", alias=args.alias, config=config)

    inventory = parse_hosts(read_lines(path))
    if args.host_action == "list":
        return result(action, "已读取 SSH 主机清单", hosts=inventory)
    if args.host_action == "find":
        return result(action, "SSH 主机查找完成", hosts=search_hosts(inventory, args.query))
    matches = resolve_hosts(inventory, args.query)
    if len(matches) != 1:
        code = "ambiguous_host" if matches else "host_not_found"
        raise TerminalError(code, "未能确定唯一 SSH 主机", candidates=matches)
    return result(action, "已确定唯一 SSH 主机", host=matches[0])


def probe(args) -> dict:
    argv = ssh_argv(args, "-T", "-o", f"ConnectTimeout={args.connect_timeout}", "-o", "ConnectionAttempts=1",
                    "-o", "ClearAllForwardings=yes", "-o", "ForwardAgent=no", "-o", "ForwardX11=no",
                    "-o", "PermitLocalCommand=no", "-o", "ProxyCommand=none")
    output = run([*argv, REMOTE_PROBE], args.timeout)
    return finished("ssh probe", {**output, "environment": parse_probe_output(output["stdout"])})


def transfer(args) -> dict:
    validate_host_alias(args.alias)
    if not args.remote or has_control_characters(args.remote):
        raise TerminalError("invalid_path", "远程路径不能为空或含控制字符")
    remote = f"{args.alias}:{args.remote}"
    local = str(args.local_path.resolve())
    targets = [local, remote] if args.ssh_action == "upload" else [remote, local]
    ssh_client = executable("ssh", args.ssh_exe or args.local.get("ssh_exe"))
    options = ["-S", ssh_client]
    if args.recursive:
        options.append("-r")
    argv = common_argv(args, "scp", options)
    return finished(f"ssh {args.ssh_action}", run([*argv, *targets], args.timeout))


def tunnel_argv(args) -> list[str]:
    host = args.remote_host
    if not host or any(c.isspace() for c in host) or has_control_characters(host) or any(c in "[]" for c in host):
        raise TerminalError("invalid_host", "无效的转发目标主机")
    if ":" in host:
        host = f"[{host}]"
    target = f"127.0.0.1:{args.local_port}:{host}:{args.remote_port}"
    return ssh_argv(args, "-N", "-o", "ExitOnForwardFailure=yes",
                    "-o", f"ConnectTimeout={args.connect_timeout}", "-L", target)
