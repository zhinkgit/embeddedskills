# jlink

Claude Code skill，通过 J-Link 探针进行嵌入式设备的固件烧录、内存读写、寄存器查看、RTT/SWO 日志读取和在线调试。

## 功能

- 探针与目标连通性探测
- 固件烧录（.hex / .bin / .elf）
- 内存读写、寄存器查看、目标复位
- RTT 日志实时读取
- SWO 事件流包装（通过外部 viewer）
- 在线调试：暂停/恢复/单步/断点运行
- GDB 源码级调试：调用栈、局部变量查看

## 环境要求

- [SEGGER J-Link Software](https://www.segger.com/downloads/jlink/) — 提供 JLink.exe、JLinkGDBServerCL.exe、JLinkRTTClient.exe
- Python 3.x（仅标准库，无额外依赖）
- GDB 调试需要 `arm-none-eabi-gdb`（随 [Arm GNU Toolchain](https://developer.arm.com/Tools%20and%20Software/GNU%20Toolchain) 安装）

## 配置

### 环境级配置（skill/config.json）

复制 `config.example.json` 为 `config.json`，根据实际安装路径修改：

```json
{
  "exe": "C:\\Program Files\\SEGGER\\JLink\\JLink.exe",
  "gdbserver_exe": "C:\\Program Files\\SEGGER\\JLink\\JLinkGDBServerCL.exe",
  "rtt_exe": "C:\\Program Files\\SEGGER\\JLink\\JLinkRTTClient.exe",
  "gdb_exe": "C:\\Program Files\\Arm\\GNU Toolchain mingw-w64-x86_64-arm-none-eabi\\bin\\arm-none-eabi-gdb.exe",
  "serial_no": "",
  "rtt_telnet_port": 0,
  "swo_command": [],
  "operation_mode": 1
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `exe` | 是 | JLink.exe 完整路径 |
| `gdbserver_exe` | 否 | JLinkGDBServerCL.exe 路径，RTT 和 GDB 调试需要 |
| `rtt_exe` | 否 | JLinkRTTClient.exe 路径，RTT 需要 |
| `gdb_exe` | 否 | arm-none-eabi-gdb 路径，GDB 源码级调试需要 |
| `serial_no` | 否 | 探针序列号，多探针场景使用 |
| `rtt_telnet_port` | 否 | RTT 端口，`0` 使用工具默认值 |
| `swo_command` | 否 | 外部 SWO viewer 的完整命令数组，供 `jlink_swo.py` 包装 |
| `operation_mode` | 否 | `1` 直接执行 / `2` 输出风险摘要 / `3` 执行前确认 |

### 工程级配置（.embeddedskills/config.json）

设备参数（device/interface/speed）统一在工作区的 `.embeddedskills/config.json` 中管理：

```json
{
  "jlink": {
    "device": "STM32F407VG",
    "interface": "SWD",
    "speed": "4000"
  }
}
```

参数解析优先级：**CLI 参数 > 工程配置 > state.json > 默认值**

成功执行后，确认过的 device/interface/speed 会自动写回工程配置。
