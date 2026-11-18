# Terminal

支持 Windows 和 Linux，需 Python 3.10+。SSH 接入需 OpenSSH 的 `ssh`，传输另需 `scp`。串口接入需 `python -m pip install pyserial`。本地接入需指定的程序，默认使用 PowerShell（Windows）或系统 Shell（Linux）。

## 本地命令和持续会话

以下示例在 Terminal Skill 目录执行。全局选项放在子命令前，`--` 后是本地程序的参数。

```console
python scripts/terminal.py --json exec local -- python -c "print('hello')"
python scripts/terminal.py --json start local --name repl -- python -u -i
python scripts/terminal.py --json send repl "print(42)" --eol lf
python scripts/terminal.py --json read repl --timeout 2
python scripts/terminal.py --json status repl
python scripts/terminal.py --json stop repl
```

## 串口

```console
python scripts/terminal.py --json serial scan
python scripts/terminal.py --json start serial --name board --port COM11 --baudrate 115200 --log board.log
python scripts/terminal.py --json send board "help" --eol crlf
python scripts/terminal.py --json read board --hex --timeout 2
python scripts/terminal.py --json stop board
```

串口会话参数优先读取 CLI，其次读取工作区 `.embeddedskills/config.json` 的 `terminal.serial` 段。可配置 `port`、`baudrate`、`bytesize`、`parity`、`stopbits`。缺少端口时先扫描并确定目标。日志保存原始接收字节。前台查看可用 `attach board --eol crlf`，退出前台后可继续用 Agent 命令操作会话。

## SSH

```console
python scripts/terminal.py --json ssh hosts resolve board
python scripts/terminal.py --json ssh probe board
python scripts/terminal.py --json exec ssh board "uname -a"
python scripts/terminal.py --json start ssh board --name remote
python scripts/terminal.py --json send remote "pwd" --eol lf
python scripts/terminal.py --json read remote --timeout 2
python scripts/terminal.py --json stop remote
python scripts/terminal.py --json ssh upload board ./local.bin /tmp/remote.bin
python scripts/terminal.py --json ssh tunnel start board --name debug --local-port 3333 --remote-port 3333
python scripts/terminal.py --json ssh tunnel status --name debug
python scripts/terminal.py --json ssh tunnel stop --name debug
```

SSH 主机清单使用 `~/.ssh/config`。全局 `--ssh-config FILE` 可选另一份配置。执行连接时使用 OpenSSH 对所选配置和跳板机的解析。主机清单查找读取该配置文件中直接声明的 Host。应用主机配置或开启隧道前应确定用户授权的目标。只读探测入口关闭任意 ProxyCommand。连接默认仅使用非交互认证和已信任的主机密钥。

`config.example.json` 可复制为 Skill 目录下的 `config.json`，用于指定本机 `ssh_exe` 和 `scp_exe`。CLI 可覆盖这两项。运行状态存放于指定工作区。
