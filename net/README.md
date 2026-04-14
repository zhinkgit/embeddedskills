# net

Claude Code skill，用于嵌入式网络通信调试：接口发现、抓包、pcap 分析、连通性测试、端口扫描和流量统计。

## 功能

- 列出网络接口及 tshark 接口映射
- 实时抓包，支持 pcapng/pcap 格式输出
- 离线分析 pcap 文件（协议分布、会话、端点、IO、异常检测）
- ping / TCP 连通性测试 / 路由追踪
- 端口扫描（含 Banner 抓取）
- 实时流量统计

## 环境要求

- [Wireshark](https://www.wireshark.org/) — 提供 tshark、dumpcap、capinfos（安装时勾选命令行工具并加入 PATH）
- [Npcap](https://npcap.com/) — Windows 抓包驱动（Wireshark 安装时可一并安装）
- Python 3.x（仅标准库，无额外依赖）
- 抓包可能需要管理员权限

## 配置

### 环境级配置 (`config.json`)

仅保留工具路径相关的环境级配置：

```json
{
  "tshark_exe": "tshark",
  "capinfos_exe": "capinfos"
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `tshark_exe` | 是 | tshark 路径或命令名 |
| `capinfos_exe` | 否 | capinfos 路径或命令名 |

### 工程级配置 (`.embeddedskills/config.json`)

工作区下的 `.embeddedskills/config.json` 存放工程级网络配置：

```json
{
  "net": {
    "interface": "",
    "target": "",
    "capture_filter": "",
    "display_filter": "",
    "duration": 30,
    "timeout_ms": 1000,
    "scan_ports": "",
    "capture_format": "pcapng",
    "log_dir": ".embeddedskills/logs/net"
  }
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `interface` | 否 | 默认抓包接口（用 `iface` 子命令查看可用接口） |
| `target` | 否 | 默认目标 IP，多个用逗号分隔 |
| `capture_filter` | 否 | 默认抓包过滤器（BPF 语法） |
| `display_filter` | 否 | 默认显示过滤器（Wireshark 语法） |
| `duration` | 否 | 默认抓包/统计时长（秒），默认 30 |
| `timeout_ms` | 否 | ping/scan 超时毫秒数，默认 1000 |
| `scan_ports` | 否 | 默认扫描端口范围，为空时使用嵌入式常用端口集 |
| `capture_format` | 否 | 抓包格式：`pcapng` 或 `pcap`，默认 `pcapng` |
| `log_dir` | 否 | 日志输出目录，默认 `.embeddedskills/logs/net` |

### 参数解析优先级

1. **CLI 参数** (`--interface`, `--target` 等) - 最高优先级
2. **工程级配置** (`.embeddedskills/config.json` 中的 `net` 部分)
3. **状态文件** (`.embeddedskills/state.json` 中的历史记录)
4. **默认值** - 最低优先级

### 配置写回

成功执行后，确认的参数会自动写回 `.embeddedskills/config.json`，方便下次使用。
