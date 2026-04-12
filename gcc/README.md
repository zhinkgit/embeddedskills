# GCC 嵌入式工程构建 Skill

基于 CMake + arm-none-eabi-gcc 的嵌入式工程构建工具。

范围说明：当前仅支持 **CMake 型** 嵌入式 GCC 工程，不覆盖纯 `Makefile` 工程。

## 环境要求

- **CMake** >= 3.21（支持 CMakePresets.json v3）
- **Ninja**（推荐）或 Make
- **ARM GNU Toolchain**（arm-none-eabi-gcc、arm-none-eabi-size 等）
- **Python** >= 3.10

## 快速开始

1. 复制 `config.example.json` 为 `config.json`，按需修改
2. 使用 `/gcc scan` 扫描工程
3. 使用 `/gcc build` 编译工程

## 子命令

| 命令 | 说明 |
|------|------|
| `scan` | 搜索嵌入式 CMake 工程 |
| `presets` | 列出 CMake preset |
| `configure` | 生成构建系统 |
| `build` | 增量编译 |
| `rebuild` | 全量重建 |
| `clean` | 清理构建目录 |
| `size` | 分析 ELF 大小 |

`build/rebuild` 成功后会返回 `elf_file`，便于继续交给 `jlink/openocd` 做烧录和调试。
