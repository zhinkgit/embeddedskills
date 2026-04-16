---
name: probe-rs
description: >-
  probe-rs 下载与调试工具，用于探针发现、固件烧录、复位、内存读写、GDB Server 调试和 RTT 日志读取。
  当用户提到 probe-rs、cargo-embed、DAP、RTT、CMSIS-DAP、ST-Link、J-Link、烧录、芯片信息、
  连接 under reset、probe 选择器、probe-rs gdb、probe-rs attach 时自动触发，也兼容 /probe-rs 显式调用。
  即使用户只是说"用 probe-rs 烧进去"、"看看 RTT"或"拉个 backtrace"，只要上下文涉及 probe-rs 就应触发此 skill。
argument-hint: "[list|info|flash|erase|reset|read-mem|write-mem|attach|run|gdb|rtt] ..."
---

# probe-rs 下载与调试

本 skill 提供 `probe-rs` CLI 的结构化包装，覆盖探针发现、目标信息、烧录、复位、内存读写、one-shot GDB 调试和 RTT 日志读取。

## 配置

### 环境级配置（skill/config.json）

首次使用前建议在 skill 目录下创建 `config.json`：

```json
{
  "exe": "probe-rs",
  "gdb_exe": "C:\\Program Files\\Arm\\GNU Toolchain mingw-w64-x86_64-arm-none-eabi\\bin\\arm-none-eabi-gdb.exe",
  "gdb_port": 3333,
  "dap_port": 50000,
  "operation_mode": 1
}
```

- `exe`：`probe-rs` 可执行文件路径或命令名
- `gdb_exe`：`arm-none-eabi-gdb` 路径，`gdb` 子命令需要
- `gdb_port`：默认 GDB 端口
- `dap_port`：预留给交互式 DAP 会话
- `operation_mode`：`1` 直接执行 / `2` 输出风险摘要但不阻塞 / `3` 执行前确认

### 工程级配置（.embeddedskills/config.json）

```json
{
  "probe-rs": {
    "chip": "STM32F407VGTx",
    "protocol": "swd",
    "probe": "",
    "speed": 4000,
    "connect_under_reset": false
  }
}
```

- `chip`：芯片型号，`probe-rs` 主后端必填
- `protocol`：`swd` 或 `jtag`
- `probe`：探针选择器，格式 `VID:PID[:Serial]`
- `speed`：调试速率 kHz
- `connect_under_reset`：连接时是否保持 reset

参数优先级：**CLI 参数 > 工程配置 > state.json > 默认值**

## 子命令

| 子命令 | 用途 | 风险 |
|---|---|---|
| `list` | 枚举可用探针 | 低 |
| `info` | 查看探针与目标信息 | 低 |
| `flash` | 烧录固件（elf/hex/bin/uf2） | 高 |
| `erase` | 擦除芯片非易失存储 | 高 |
| `reset` | 复位目标芯片 | 高 |
| `read-mem` | 读取内存 | 低 |
| `write-mem` | 写内存 | 高 |
| `attach` / `run` | 包装 probe-rs attach/run | 低 |
| `gdb` | 启动 GDB Server 并执行 one-shot 调试 | 低 |
| `rtt` | 读取 RTT 日志 | 低 |

## 典型调用

```bash
# 列出探针
python <skill-dir>/scripts/probe_rs_exec.py list --json

# 烧录 ELF
python <skill-dir>/scripts/probe_rs_exec.py flash --chip STM32F407VGTx --file build/app.elf --json

# 烧录 BIN（必须提供地址）
python <skill-dir>/scripts/probe_rs_exec.py flash --chip STM32F407VGTx --file build/app.bin --address 0x08000000 --json

# 读取内存
python <skill-dir>/scripts/probe_rs_exec.py read-mem --chip STM32F407VGTx --address 0x20000000 --length 16 --width b32 --json

# one-shot backtrace
python <skill-dir>/scripts/probe_rs_gdb.py backtrace --chip STM32F407VGTx --elf build/app.elf --json

# RTT
python <skill-dir>/scripts/probe_rs_rtt.py --chip STM32F407VGTx --json
```

## 核心规则

- 不自动猜测 `chip`，缺失时直接报错
- 多探针场景建议显式提供 `--probe`
- `.bin` 烧录必须显式提供地址
- `workflow build-debug` 只走 one-shot 诊断包装，不启动需要人工接管的长期 DAP 会话
- Windows 下若要用 `probe-rs` 驱动 `J-Link`，通常需要切换到 `WinUSB`，这会影响 SEGGER 官方工具继续使用；若仍依赖 J-Link 官方工具链，优先继续用现有 `jlink` skill
