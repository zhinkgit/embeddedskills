---
name: terminal
description: Agent 终端工具，独立提供 SSH、串口和本地进程接入。用于远程命令、SSH 主机配置、探测、传输、隧道，以及通过任一后端开启持续会话并多轮收发。
argument-hint: "[exec|start|list|status|send|read|stop|attach|serial|ssh] ..."
---

# Terminal

统一入口为 `python <skill-dir>/scripts/terminal.py [--workspace DIR] [--json] ...`。支持 Windows 和 Linux，Python 3.10+。SSH 功能需要 OpenSSH，串口功能需要 pyserial，本地进程只用 Python 标准库。

| 任务 | 命令 |
| --- | --- |
| 单次命令 | `exec ssh ALIAS COMMAND`、`exec local -- PROGRAM ARG...` |
| 持续会话 | `start ssh ALIAS`、`start serial --port PORT`、`start local -- PROGRAM ARG...` |
| 管理会话 | `list`、`status NAME`、`send NAME DATA`、`read NAME`、`stop NAME`、`attach NAME` |
| 串口 | `serial scan`，串口会话支持参数、Hex 和日志 |
| SSH 配套 | `ssh hosts list/find/resolve/show/add`、`ssh probe`、`ssh upload/download`、`ssh tunnel start/status/stop` |

持续操作先用 `start --name` 创建会话。交替使用 `send` 和 `read --timeout 秒`，根据返回结果决定下一步。命令行按需选用 `--hex` 和 `--eol lf|cr|crlf|none`。用 `--log FILE` 显式记录收到的数据。完成后执行 `stop NAME`。串口会话独占物理端口。前台行模式使用 `attach`。

SSH 使用 OpenSSH Host 别名和密钥认证。遇到未知主机密钥时，默认保留验证失败，只有显式 `--accept-new-host-key` 才接受新密钥。写 SSH 配置使用明确的 `hosts add`，隧道使用 `ssh tunnel start`。探测只运行内置命令。

JSON 模式返回 `status`、`action`、`summary`、`details`，失败附带 `error.code` 和 `error.message`，同时返回非零退出码。会话状态保存在工作区 `.embeddedskills/state/terminal.json`。

更多参数与用法见 [README](README.md)，随包提供 [本机配置示例](config.example.json)。
