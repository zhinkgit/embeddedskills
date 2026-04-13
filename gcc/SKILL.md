---
name: gcc
description: >-
  GCC 嵌入式工程构建工具（CMake + arm-none-eabi-gcc），用于扫描 CMake 型嵌入式工程、
  列出预设、配置、编译、重建、清理和分析 ELF 大小。当用户提到 GCC、arm-none-eabi、
  CMake 嵌入式编译、Ninja 构建、ELF 大小分析、arm-gcc、交叉编译、cmake --build、
  cmake --preset 时自动触发，也兼容 /gcc 显式调用。即使用户只是说"编译一下"或
  "看看固件多大"，只要上下文涉及 CMake 嵌入式 GCC 工程就应触发此 skill。
argument-hint: "[scan|presets|configure|build|rebuild|clean|size] ..."
---

# GCC 嵌入式工程构建

本 skill 提供基于 CMake + arm-none-eabi-gcc 的嵌入式工程发现、preset 枚举、配置生成、增量编译、全量重建、清理和 ELF 大小分析能力。

范围说明：当前仅支持 **CMake 型** GCC 嵌入式工程，不覆盖纯 `Makefile` 工程。

## 配置

skill 目录下的 `config.json` 包含运行时配置，首次使用前确认 `cmake_exe` 路径正确：

```json
{
  "cmake_exe": "cmake",
  "toolchain_prefix": "arm-none-eabi-",
  "toolchain_path": "",
  "default_project": "",
  "default_preset": "",
  "log_dir": ".build",
  "operation_mode": 1
}
```

- `cmake_exe`：cmake 可执行文件路径，默认从 PATH 查找
- `toolchain_prefix`：工具链前缀，默认 `arm-none-eabi-`，用于定位 size 等工具
- `toolchain_path`：工具链 bin 目录，为空时从 PATH 查找
- `default_project`：默认工程路径，可为空；为空时优先读取 workspace 最近状态
- `default_preset`：默认 CMake preset 名称，为空时需用户选择
- `log_dir`：构建日志输出目录，默认 `.build`
- `operation_mode`：`1` 直接执行 / `2` 输出风险摘要但不阻塞 / `3` 执行前确认

## 子命令

| 子命令 | 用途 | 风险 |
|--------|------|------|
| `scan` | 搜索当前目录下的 CMake 嵌入式工程 | 低 |
| `presets` | 列出 CMakePresets.json 中的 configure/build preset | 低 |
| `configure` | 执行 `cmake --preset` 生成构建系统 | 中 |
| `build` | 增量编译 `cmake --build` | 中 |
| `rebuild` | 清理后全量重建 | 中 |
| `clean` | 清理构建目录 | 高 |
| `size` | 分析 ELF 文件大小（text/data/bss 和内存使用） | 低 |

## 执行流程

1. 读取 `config.json`，确认 `cmake_exe` 路径有效
2. 未指定子命令时默认执行 `scan`
3. 未提供工程路径时先执行 `scan` 搜索工程
4. 发现多个工程或多个 preset 时列出选项让用户选择，绝不自动猜测
5. `configure/build/rebuild/clean` 按 `operation_mode` 决定是否需要确认
6. `build` 前自动检测是否已 configure，未配置时提示先执行 configure
7. `build/rebuild` 成功后返回 `elf_file`，供 `jlink/openocd` 继续使用
8. `size` 默认分析最近一次构建产物的 .elf 文件

## 脚本调用

skill 目录下有三个 Python 脚本，使用标准库实现，无额外依赖。

### gcc_project.py — 工程扫描与 preset 枚举

```bash
# 扫描工程
python <skill-dir>/scripts/gcc_project.py scan --root <搜索目录> --json

# 列出 preset
python <skill-dir>/scripts/gcc_project.py presets --project <工程目录> --json
```

### gcc_build.py — 配置 / 编译 / 重建 / 清理

```bash
python <skill-dir>/scripts/gcc_build.py <configure|build|rebuild|clean> \
  --cmake <cmake路径> \
  --project <工程根目录> \
  --preset <preset名称> \
  --log-dir <日志目录> \
  --json
```

### gcc_size.py — ELF 大小分析

```bash
# 基本分析
python <skill-dir>/scripts/gcc_size.py analyze \
  --elf <elf文件路径> \
  --toolchain-prefix arm-none-eabi- \
  --linker-script <链接脚本路径> \
  --json

# 对比分析
python <skill-dir>/scripts/gcc_size.py compare \
  --elf <elf文件1> \
  --compare <elf文件2> \
  --toolchain-prefix arm-none-eabi- \
  --json
```

## 输出格式

所有脚本以 JSON 格式返回，基础字段为 `status`（ok/error）、`action`、`summary`、`details`，并可能附带 `context`、`artifacts`、`metrics`、`state`、`next_actions`、`timing`。

成功示例：
```json
{
  "status": "ok",
  "action": "build",
  "summary": "build 成功，errors=0 warnings=2",
  "details": { "project": "...", "preset": "Debug", "build_dir": "...", "elf_file": "...", "log_file": "..." },
  "metrics": { "errors": 0, "warnings": 2, "flash_bytes": 99328, "ram_bytes": 46080 }
}
```

错误示例：
```json
{
  "status": "error",
  "action": "build",
  "error": { "code": "not_configured", "message": "构建目录不存在，请先执行 configure" }
}
```

## 核心规则

- 不修改 CMakeLists.txt 或任何 CMake 配置文件
- 当前 skill 仅覆盖 CMake 型 GCC 工程，不对纯 Makefile 工程做识别和构建
- 不自动猜测工程路径或 preset，有歧义时必须询问用户
- 参数解析优先级为：CLI 显式参数 > `config.json` > `.embeddedskills/state.json` > 报错
- `clean` 不在自动流程中隐式执行
- 构建失败时优先展示首个错误和日志文件路径
- 结果回显中始终包含工程名、preset 名、构建目录路径；构建成功时优先回显 `elf_file`
