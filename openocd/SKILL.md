---
name: openocd
description: >-
  OpenOCD 下载与调试工具，用于探针探测、固件烧录、Flash 擦除、GDB Server 启动和目标复位控制。
  当用户提到 OpenOCD、ST-Link、CMSIS-DAP、DAPLink、FTDI、烧录固件、擦除 Flash、GDB Server、
  reset、interface/target/board 配置、openocd.cfg 时自动触发，也兼容 /openocd 显式调用。
  即使用户只是说"烧录一下"、"启动 GDB Server"或"擦除芯片"，只要上下文涉及 OpenOCD 支持的
  开源调试器（ST-Link、CMSIS-DAP、DAPLink、FTDI 等）就应触发此 skill。
argument-hint: "[probe|flash|erase|gdb-server|reset] ..."
---

# OpenOCD 下载与调试

本 skill 提供 OpenOCD 的探针探测、固件烧录、Flash 擦除、GDB Server 启动和目标复位能力。

## 配置

skill 目录下的 `config.json` 包含运行时配置，首次使用前确认 `exe` 路径正确：

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

- `exe`：openocd.exe 路径或命令名（必填）
- `scripts_dir`：OpenOCD 配置脚本目录，为空时使用 OpenOCD 内置路径
- `default_board`：默认 board 配置（如 `board/stm32f4discovery.cfg`），优先级高于 interface+target
- `default_interface`：默认 interface 配置（如 `interface/stlink.cfg`）
- `default_target`：默认 target 配置（如 `target/stm32f4x.cfg`）
- `gdb_port`：GDB Server 端口，默认 3333
- `telnet_port`：Telnet 端口，默认 4444
- `operation_mode`：`1` 直接执行 / `2` 输出风险摘要但不阻塞 / `3` 执行前确认

## 子命令

| 子命令 | 用途 | 风险 |
|--------|------|------|
| `probe` | 验证 board 或 interface+target 组合，探测目标连通性 | 低 |
| `flash` | 烧录固件（.elf / .hex / .bin） | 高 |
| `erase` | 擦除目标 Flash | 高 |
| `gdb-server` | 启动 GDB Server，保持运行等待 GDB 连接 | 低 |
| `reset` | 复位目标芯片 | 高 |

## 执行流程

1. 读取 `config.json`，确认 `exe` 路径有效
2. 读取默认 `board / interface / target` 配置
3. 已知 `board` 时优先使用 `-f board/*.cfg`，否则组合 `-f interface/*.cfg -f target/*.cfg`
4. `board`、`interface`、`target` 同时缺失时，不自动拼接组合，直接要求用户补充
5. 按 `operation_mode` 决定是否需要确认后执行
6. 调用 `openocd_run.py` 或 `openocd_gdb.py`
7. 统一解析输出中的 `Info`、`Error` 和就绪日志，返回结构化结果

## 脚本调用

skill 目录下有两个 Python 脚本，使用标准库实现，无额外依赖。

### openocd_run.py — 探测 / 烧录 / 擦除 / 复位

```bash
# 探测连通性（使用 board）
python <skill-dir>/scripts/openocd_run.py probe --board board/stm32f4discovery.cfg --json

# 探测连通性（使用 interface + target）
python <skill-dir>/scripts/openocd_run.py probe --interface interface/stlink.cfg --target target/stm32f4x.cfg --json

# 烧录 ELF 固件
python <skill-dir>/scripts/openocd_run.py flash --file build/app.elf --interface interface/stlink.cfg --target target/stm32f4x.cfg --json

# 烧录 BIN 固件（必须提供地址）
python <skill-dir>/scripts/openocd_run.py flash --file build/app.bin --address 0x08000000 --interface interface/stlink.cfg --target target/stm32f4x.cfg --json

# 擦除 Flash（自动选择 mass/sector）
python <skill-dir>/scripts/openocd_run.py erase --mode auto --interface interface/stlink.cfg --target target/stm32f4x.cfg --json

# 强制整片擦除
python <skill-dir>/scripts/openocd_run.py erase --mode mass --interface interface/stlink.cfg --target target/stm32f4x.cfg --json

# 强制扇区擦除
python <skill-dir>/scripts/openocd_run.py erase --mode sector --interface interface/stlink.cfg --target target/stm32f4x.cfg --json

# 复位目标
python <skill-dir>/scripts/openocd_run.py reset --mode halt --interface interface/stlink.cfg --target target/stm32f4x.cfg --json
```

通用可选参数：`--board <cfg>`、`--search <目录>`、`--adapter-speed <kHz>`、`--transport <swd|jtag>`、`--exe <openocd路径>`

- `erase` 的 `--mode` 支持 `auto|mass|sector`
  - `auto`：优先使用 target 对应的 mass erase，未命中映射时回退 sector erase
  - `mass`：强制整片擦除；当前 target 未配置 mass erase 命令时直接报错
  - `sector`：强制 `flash erase_sector <bank> 0 last`
- `reset` 的 `--mode` 仍为 `halt|run|init`

### openocd_gdb.py — GDB Server 启动

```bash
# 启动 GDB Server
python <skill-dir>/scripts/openocd_gdb.py --interface interface/stlink.cfg --target target/stm32f4x.cfg --json

# 使用 board 启动
python <skill-dir>/scripts/openocd_gdb.py --board board/stm32f4discovery.cfg --gdb-port 3333 --json
```

可选参数：`--gdb-port`、`--telnet-port`、`--search`、`--adapter-speed`、`--transport`

GDB Server 启动后保持运行，脚本检测到就绪状态后返回端口信息。后续可通过 `arm-none-eabi-gdb` 连接 `localhost:<gdb-port>` 进行源码级调试。

## 输出格式

所有脚本以 JSON 格式返回，包含 `status`（ok/error）、`action`、`summary`、`details` 字段。

成功示例：
```json
{
  "status": "ok",
  "action": "probe",
  "summary": "目标探测成功",
  "details": {
    "interface": "stlink.cfg",
    "target": "stm32f4x.cfg"
  }
}
```

GDB Server 就绪示例：
```json
{
  "status": "ok",
  "action": "gdb-server",
  "summary": "GDB Server 已就绪",
  "details": {
    "gdb_port": 3333,
    "telnet_port": 4444,
    "pid": 12345
  }
}
```

错误示例：
```json
{
  "status": "error",
  "action": "flash",
  "error": {
    "code": "cfg_not_found",
    "message": "未找到指定的 interface 或 target 配置文件"
  }
}
```

## 核心规则

- 不自动猜测 `board`、`interface`、`target` 的组合，缺失时必须询问用户
- 已知 `board` 时优先使用 board 配置，不再需要 interface+target
- `.bin` 文件必须显式提供烧录地址，缺失时报错
- STM32F4 等已映射 target 在 `erase --mode auto` 下会先 `reset halt`，再优先使用 mass erase
- `erase --mode mass` 在未命中映射时直接返回 `mass_erase_unsupported`，避免误以为已做整片擦除
- 检测到 Flash 锁保护时只提示，不自动解锁
- 连接失败时给出排查建议（检查连线、供电、驱动、cfg 路径），不自动尝试更激进参数
- 烧录、擦除、复位在参数完整且用户意图明确时直接执行
- `gdb-server` 启动后返回端口和连接方式，进程保持运行
- 结果回显中始终包含 cfg 组合、端口和执行动作

## 参考

遇到 board/interface/target 配置问题时可查阅 `references/common_targets.md`。
