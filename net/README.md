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

复制 `config.example.json` 为 `config.json`，根据实际环境修改：

```json
{
  "tshark_exe": "tshark",
  "capinfos_exe": "capinfos",
  "default_interface": "",
  "default_target": "",
  "default_capture_filter": "",
  "default_display_filter": "",
  "default_duration": 30,
  "default_timeout_ms": 1000,
  "default_scan_ports": "",
  "default_capture_format": "pcapng"
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `tshark_exe` | 是 | tshark 路径或命令名 |
| `capinfos_exe` | 否 | capinfos 路径或命令名 |
| `default_interface` | 否 | 默认抓包接口（用 `iface` 子命令查看可用接口） |
| `default_target` | 否 | 默认目标 IP，多个用逗号分隔 |
| `default_capture_filter` | 否 | 默认抓包过滤器（BPF 语法） |
| `default_display_filter` | 否 | 默认显示过滤器（Wireshark 语法） |
| `default_duration` | 否 | 默认抓包/统计时长（秒），默认 30 |
| `default_timeout_ms` | 否 | ping/scan 超时毫秒数，默认 1000 |
| `default_scan_ports` | 否 | 默认扫描端口范围，为空时使用嵌入式常用端口集 |
| `default_capture_format` | 否 | 抓包格式：`pcapng` 或 `pcap`，默认 `pcapng` |
