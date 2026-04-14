# gcc

Claude Code skill，用于基于 CMake + arm-none-eabi-gcc 的嵌入式工程扫描、preset 枚举、配置、构建和 ELF 大小分析。

范围说明：当前仅支持 **CMake 型** 嵌入式 GCC 工程，不覆盖纯 `Makefile` 工程。

## 功能

- 扫描目录下的嵌入式 CMake 工程
- 枚举 `CMakePresets.json` 中的 configure/build preset
- 执行 `configure` / `build` / `rebuild` / `clean`
- 分析 ELF 的 `text` / `data` / `bss` 和内存占用
- 返回 `elf_file` / `flash_file` / `debug_file` / `log_file` 等产物路径，便于继续交给 `jlink/openocd`

## 环境要求

- [CMake](https://cmake.org/) 3.21 或更高版本
- Ninja（推荐）或 Make
- [Arm GNU Toolchain](https://developer.arm.com/downloads/-/arm-gnu-toolchain-downloads)（提供 `arm-none-eabi-gcc`、`arm-none-eabi-size` 等）
- Python 3.x（仅标准库，无额外依赖）

## 配置

### 环境级配置（skill/config.json）

复制 `config.example.json` 为 `config.json`，根据实际环境修改：

```json
{
  "cmake_exe": "cmake",
  "toolchain_prefix": "arm-none-eabi-",
  "toolchain_path": "",
  "operation_mode": 1
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `cmake_exe` | 否 | `cmake` 路径或命令名，默认从 PATH 查找 |
| `toolchain_prefix` | 否 | 工具链前缀，默认 `arm-none-eabi-` |
| `toolchain_path` | 否 | 工具链 bin 目录，为空时从 PATH 查找 |
| `operation_mode` | 否 | `1` 直接执行 / `2` 输出风险摘要 / `3` 执行前确认 |

### 工程级配置（workspace/.embeddedskills/config.json）

工程级共享配置保存在工作区的 `.embeddedskills/config.json` 中：

```json
{
  "gcc": {
    "project": "",
    "preset": "",
    "log_dir": ".embeddedskills/build"
  }
}
```

| 字段 | 说明 |
|------|------|
| `project` | 默认工程路径（相对 workspace） |
| `preset` | 默认 CMake preset 名称 |
| `log_dir` | 构建日志输出目录，默认 `.embeddedskills/build` |

### 参数解析优先级

参数解析顺序（从高到低）：
1. CLI 显式参数
2. 环境级配置（skill/config.json）
3. 工程级配置（.embeddedskills/config.json）
4. state.json（上次构建记录）
5. 搜索/询问

## 子命令

| 子命令 | 用途 |
|--------|------|
| `scan` | 搜索嵌入式 CMake 工程 |
| `presets` | 列出 CMake preset |
| `configure` | 生成构建系统 |
| `build` | 增量编译 |
| `rebuild` | 全量重建 |
| `clean` | 清理构建目录 |
| `size` | 分析 ELF 大小 |

## 使用说明

- `build` 前若尚未完成 `configure`，脚本会提示先执行 `configure`
- 发现多个工程或多个 preset 时，只返回候选项，不自动猜测
- `build/rebuild` 成功后会返回 `elf_file`，同时复用为 `flash_file` 和 `debug_file`
- `size` 默认分析最近一次构建产物；底层 `gcc_size.py` 额外支持两个 ELF 的对比分析
- `clean` 不会在自动流程中隐式执行

