---
name: can
description: >-
  嵌入式 CAN / CAN-FD 调试工具，用于扫描接口、监控报文、发送测试帧、记录日志、数据库文件解码和总线统计。
  当用户提到 CAN、CAN-FD、DBC 解码、总线抓包、USB-CAN 联调、报文发送、总线统计、
  PCAN、Vector、slcan、CAN 接口扫描、CAN ID 过滤、ASC 日志、BLF 文件时自动触发，
  也兼容 /can 显式调用。即使用户只是说"看看 CAN 报文"、"发一帧试试"或"解码一下 DBC"，
  只要上下文涉及 CAN 总线通信就应触发此 skill。
argument-hint: "[scan|monitor|send|log|decode|stats] ..."
---

# CAN — 嵌入式 CAN / CAN-FD 调试工具

统一封装接口发现、实时监控、报文发送、日志记录、数据库文件解码和统计分析能力。

## 配置

### 环境级配置 (`skill/config.json`)

仅保留 slcan 相关的环境级配置：

```json
{
  "slcan_serial_port": "",
  "slcan_serial_baudrate": 115200
}
```

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `slcan_serial_port` | slcan 场景的串口 | `""` |
| `slcan_serial_baudrate` | slcan 场景的串口速率 | `115200` |

### 工程级配置 (`.embeddedskills/config.json`)

工作区下的 `.embeddedskills/config.json` 存放工程级 CAN 配置：

```json
{
  "can": {
    "interface": "",
    "channel": "",
    "bitrate": 500000,
    "data_bitrate": 2000000,
    "log_dir": ".embeddedskills/logs/can"
  }
}
```

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `interface` | CAN 后端，如 `pcan` / `vector` / `slcan` | `""` |
| `channel` | 通道名，如 `PCAN_USBBUS1` | `""` |
| `bitrate` | 仲裁域比特率 | `500000` |
| `data_bitrate` | CAN-FD 数据域比特率 | `2000000` |
| `log_dir` | 日志输出目录 | `.embeddedskills/logs/can` |

### 参数解析优先级

1. **CLI 参数** (`--interface`, `--channel`, `--bitrate` 等) - 最高优先级
2. **工程级配置** (`.embeddedskills/config.json` 中的 `can` 部分)
3. **状态文件** (`.embeddedskills/state.json` 中的历史记录)
4. **默认值** - 最低优先级

### 自动扫描行为

当未指定 `interface` 和 `channel` 时，脚本会自动扫描系统 CAN 接口：
- 若只找到一个接口，自动使用并写入工程配置
- 若找到多个接口，返回候选列表让用户选择
- 若未找到接口，提示错误

## 子命令

| 子命令 | 用途 | 风险 |
|--------|------|------|
| `scan` | 扫描可用 CAN 接口与 USB-CAN 设备 | 低 |
| `monitor` | 实时监控总线报文 | 低 |
| `send` | 发送标准帧 / 扩展帧 / 远程帧 / CAN-FD 帧 | 高 |
| `log` | 记录总线报文到 ASC / BLF / CSV 文件 | 低 |
| `decode` | 用 DBC 等数据库文件解码报文或日志 | 低 |
| `stats` | 统计总线负载、ID 分布和帧率 | 低 |

## 执行流程

1. 检查 `python-can` 是否可用，未安装时提示 `pip install python-can`
2. 按优先级解析参数：CLI > 工程级配置 > 状态文件 > 默认值
3. 无子命令时默认执行 `scan`
4. `monitor / send / log / stats` 使用解析后的连接参数
5. `decode` 先确认数据库文件和输入源存在
6. 若未指定 `interface`/`channel`，自动扫描系统 CAN 接口：
   - 唯一候选：自动使用并写入工程配置
   - 多候选：返回列表让用户选择
7. 成功执行后，将确认的参数写回工程配置
8. `send` 只要配置可连接就直接执行，不二次确认
9. 运行对应脚本并输出结构化结果
10. 失败时优先反馈接口、驱动、比特率和过滤条件问题

## 脚本调用

所有脚本位于 skill 目录的 `scripts/` 下，通过 `python` 直接调用。
脚本会按优先级从 CLI 参数、工程级配置、状态文件中读取参数。

```bash
# 扫描接口
python scripts/can_scan.py [--json]

# 实时监控
python scripts/can_monitor.py [--interface <接口>] [--channel <通道>] [--bitrate <速率>] [--fd] [--filter-id <ID列表>] [--exclude-id <ID列表>] [--dbc <DBC文件>] [--timeout <秒>] [--json]

# 发送报文
python scripts/can_send.py [--interface <接口>] [--channel <通道>] [--bitrate <速率>] <id> <data> [--extended] [--remote] [--fd] [--repeat <次>] [--interval <秒>] [--periodic <毫秒>] [--listen] [--json]

# 日志记录
python scripts/can_log.py [--interface <接口>] [--channel <通道>] [--bitrate <速率>] [--output <文件>] [--duration <秒>] [--max-count <数量>] [--filter-id <ID列表>] [--console] [--json]

# 数据库解码
python scripts/can_decode.py <db_file> [--db-format <auto|dbc|arxml|kcd|sym|cdd>] [--id <CAN_ID>] [--data <HEX数据>] [--log <日志文件>] [--signal <信号名>] [--list] [--json]

# 总线统计
python scripts/can_stats.py [--interface <接口>] [--channel <通道>] [--bitrate <速率>] [--duration <秒>] [--top <数量>] [--watch <ID列表>] [--json]
```

## 输出格式

单次命令返回标准 JSON：
```json
{
  "status": "ok",
  "action": "scan",
  "summary": "发现 2 个 CAN 接口",
  "details": { ... }
}
```

持续命令（monitor --json、send --listen --json）输出 JSON Lines，结束摘要写入 stderr。

错误输出：
```json
{
  "status": "error",
  "action": "send",
  "error": { "code": "interface_open_failed", "message": "无法打开指定 CAN 接口" }
}
```

## 核心规则

- 不自动猜测 interface、channel、bitrate，多接口时不自动选择
- 参数解析优先级：CLI > 工程级配置 > 状态文件 > 默认值
- 未指定 `interface`/`channel` 时自动扫描，唯一候选自动写入配置，多候选需用户选择
- 成功执行后，确认的参数自动写回 `.embeddedskills/config.json`
- 未明确说明用途时不主动发送任何报文
- `--json` 输出的持续流使用 JSON Lines，摘要写 stderr 不污染数据流
- DBC 解码失败不应导致监控中断
- 找不到帧定义时返回明确错误，不静默吞掉

## 参考

- `references/common_interfaces.json`：常见 USB-CAN 设备信息
