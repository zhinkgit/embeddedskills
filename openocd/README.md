# openocd

Claude Code skill，通过 OpenOCD 进行探针探测、固件烧录、Flash 擦除、GDB Server 启动和目标复位。支持 ST-Link、CMSIS-DAP、DAPLink、FTDI 等开源调试器。

## 功能

- 探针与目标连通性探测
- 固件烧录（.elf / .hex / .bin）
- Flash 擦除
- GDB Server 启动（供 GDB 连接进行源码级调试）
- 目标复位（支持 halt/run 模式）

## 环境要求

- [OpenOCD](https://openocd.org/) — 安装后确保 `openocd` 可执行或填写完整路径
- Python 3.x（仅标准库，无额外依赖）
- 调试器驱动（ST-Link 需要 ST 官方驱动或 WinUSB/libusb，CMSIS-DAP 免驱）

## 配置

复制 `config.example.json` 为 `config.json`，根据实际环境修改：

```json
{
  "exe": "openocd",
  "scripts_dir": "",
  "default_board": "",
  "default_interface": "interface/stlink.cfg",
  "default_target": "target/stm32f4x.cfg",
  "gdb_port": 3333,
  "telnet_port": 4444,
  "operation_mode": 1
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `exe` | 是 | openocd 路径或命令名 |
| `scripts_dir` | 否 | OpenOCD 配置脚本目录，为空时使用内置路径 |
| `default_board` | 否 | 默认 board 配置（如 `board/stm32f4discovery.cfg`），优先级高于 interface+target |
| `default_interface` | 否 | 默认 interface 配置（如 `interface/stlink.cfg`） |
| `default_target` | 否 | 默认 target 配置（如 `target/stm32f4x.cfg`） |
| `gdb_port` | 否 | GDB Server 端口，默认 3333 |
| `telnet_port` | 否 | Telnet 端口，默认 4444 |
| `operation_mode` | 否 | `1` 直接执行 / `2` 输出风险摘要 / `3` 执行前确认 |

> 提示：设置了 `default_board` 时，`default_interface` 和 `default_target` 可以不填。
