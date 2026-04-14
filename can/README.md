# can

Claude Code skill，用于嵌入式 CAN / CAN-FD 总线调试：接口扫描、实时监控、报文发送、日志记录、DBC 解码和总线统计。

## 功能

- 扫描系统可用 CAN 接口与 USB-CAN 设备
- 实时监控总线报文（支持 ID 过滤、DBC 解码、CAN-FD）
- 发送标准帧 / 扩展帧 / 远程帧 / CAN-FD 帧（支持周期发送和回听）
- 记录总线报文到 ASC / BLF / CSV 文件
- 用 DBC / ARXML / KCD 等数据库文件解码报文或日志
- 统计总线负载、ID 分布和帧率

## 环境要求

- Python 3.x
- [python-can](https://python-can.readthedocs.io/) — `pip install python-can`
- [cantools](https://cantools.readthedocs.io/) — `pip install cantools`
- [pyserial](https://pypi.org/project/pyserial/) — `pip install pyserial`（仅 slcan 场景需要）
- USB-CAN 设备驱动（PEAK、Vector、Kvaser 等，按硬件安装对应驱动）

## 配置

### 环境级配置 (`config.json`)

仅保留 slcan 相关的环境级配置：

```json
{
  "slcan_serial_port": "",
  "slcan_serial_baudrate": 115200
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `slcan_serial_port` | 否 | slcan 场景的串口号 |
| `slcan_serial_baudrate` | 否 | slcan 场景的串口速率，默认 115200 |

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

| 字段 | 必填 | 说明 |
|------|------|------|
| `interface` | 否 | CAN 后端，如 `pcan` / `vector` / `slcan`，为空时自动扫描 |
| `channel` | 否 | 通道名，如 `PCAN_USBBUS1` |
| `bitrate` | 否 | 仲裁域比特率，默认 500000 |
| `data_bitrate` | 否 | CAN-FD 数据域比特率，默认 2000000 |
| `log_dir` | 否 | 日志输出目录，默认 `.embeddedskills/logs/can` |

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

> `decode` 子命令的数据库文件通过位置参数显式传入，不从配置读取。

## 子命令

| 子命令 | 用途 | 示例 |
|--------|------|------|
| `scan` | 扫描可用 CAN 接口（默认子命令） | `/can scan` |
| `monitor` | 实时监控总线报文 | `/can monitor --timeout 10` |
| `send` | 发送测试帧 | `/can send 0x123 "DE AD BE EF"` |
| `log` | 记录总线日志 | `/can log --output trace.asc` |
| `decode` | 用数据库文件解码报文或日志 | `/can decode vehicle.dbc --log trace.asc` |
| `stats` | 统计总线负载与 ID 分布 | `/can stats --duration 10` |

## 目录结构

```
can/
├── README.md
├── SKILL.md
├── config.json
├── config.example.json
├── scripts/
│   ├── can_scan.py
│   ├── can_monitor.py
│   ├── can_send.py
│   ├── can_log.py
│   ├── can_decode.py
│   └── can_stats.py
└── references/
    └── common_interfaces.json
```

## 支持的接口

| 接口 | 平台 | 备注 |
|------|------|------|
| `pcan` | Windows | 需安装 PEAK 驱动 |
| `vector` | Windows | 需安装 Vector XL Driver Library |
| `ixxat` | Windows | 需安装 IXXAT VCI 驱动 |
| `kvaser` | Windows / Linux | 需安装 Kvaser CANlib |
| `slcan` | Windows / Linux | 串口转 CAN，需 pyserial |
| `socketcan` | Linux | 内核原生支持 |
| `gs_usb` | Linux | candleLight / CANable 等 gs_usb 固件设备 |
| `virtual` | 全平台 | 虚拟总线，用于测试 |
