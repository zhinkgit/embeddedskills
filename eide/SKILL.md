---
name: eide
description: >-
  EIDE (Embedded IDE) 工程构建工具，用于扫描 .eide/eide.yml 工程、枚举构建
  配置 (ConfigName)、执行 build/rebuild/clean 并解析构建日志，返回可供
  jlink/openocd 复用的产物路径。当用户提到 EIDE、Embedded IDE、eide.yml、
  unify_builder、VS Code EIDE 扩展、Cl.eide 时自动触发，也兼容 /eide 显式调用。
  即使用户只是说"用 EIDE 编译一下"或"EIDE 烧录到板子上"，只要上下文涉及
  EIDE 嵌入式工程就应触发此 skill。
argument-hint: "[scan|configs|build|rebuild|clean|size] ..."
---

# EIDE 嵌入式工程构建

本 skill 提供 EIDE (Embedded IDE) 工程的发现、构建配置枚举、增量编译、全量重建、清理和 ELF 大小分析能力，并返回可供 `jlink/openocd` 继续使用的固件产物路径。

EIDE 是 VS Code 下的嵌入式开发扩展，使用 ARM CC (AC5/AC6) 或 GCC 工具链，通过 `unify_builder` 统一构建后端驱动。

## 运行依赖

Python 3.10+、PyYAML、当前系统对应的 EIDE unify_builder 与工程编译器。大小分析需要 ARM size 工具。

## 配置

### 环境级配置（skill/config.json）

skill 目录下的 `config.json` 包含环境级配置，首次使用前确认 `builder_dir` 路径正确：

```json
{
  "builder_dir": "C:\\Users\\<user>\\.vscode\\extensions\\cl.eide-<version>\\res\\tools\\win32\\unify_builder",
  "builder_exe": "unify_builder.exe",
  "code_exe": "code",
  "toolchain_prefix": "arm-none-eabi-",
  "operation_mode": 1
}
```

- `builder_dir`：EIDE unify_builder 所在目录（必填，位于 VS Code 扩展目录下）
- `builder_exe`：builder 可执行文件名，Windows 默认 `unify_builder.exe`
- `code_exe`：VS Code CLI 路径，默认从 PATH 查找 `code`
- `toolchain_prefix`：用于 size 分析的工具链前缀，默认 `arm-none-eabi-`
- `operation_mode`：`1` 直接执行 / `2` 输出风险摘要但不阻塞 / `3` 执行前确认

### 工程级配置（workspace/.embeddedskills/config.json）

工程级共享配置统一保存在工作区的 `.embeddedskills/config.json` 中：

```json
{
  "eide": {
    "project": "",
    "config": "",
    "log_dir": ".embeddedskills/build"
  }
}
```

- `project`：默认 EIDE 工程根目录（包含 `.eide/eide.yml` 的目录），构建成功后会自动更新
- `config`：默认构建配置名称（对应 eide.yml 中的 ConfigName），构建成功后会自动更新
- `log_dir`：构建日志输出目录，默认 `.embeddedskills/build`

### 参数解析优先级

参数解析顺序（从高到低）：
1. CLI 显式参数
2. 工程级配置（.embeddedskills/config.json）
3. `.embeddedskills/state/eide.json`（上次构建记录）
4. 搜索/询问

工具路径依次读取 CLI、本机配置和 PATH。

## 子命令

| 子命令 | 用途 | 风险 |
|--------|------|------|
| `scan` | 搜索当前目录下的 EIDE 工程（含 `.eide/eide.yml` 的目录） | 低 |
| `configs` | 枚举工程中的构建配置 | 低 |
| `build` | 增量编译 | 中 |
| `rebuild` | 全量重建 | 中 |
| `clean` | 清理构建产物 | 高 |
| `size` | 分析 ELF 文件大小（text/data/bss 和内存使用） | 低 |

## 执行流程

1. 读取 `config.json`，确认 `builder_dir` 路径有效
2. 未指定子命令时默认执行 `scan`
3. 未提供工程路径时先执行 `scan` 搜索工程
4. 同时发现多个工程或多个配置时，列出选项让用户选择，绝不自动猜测
5. `build/rebuild/clean` 按 `operation_mode` 决定是否需要确认
6. `build/rebuild` 成功后，从构建目录解析 `elf_file` / `hex_file` 等产物路径
7. 所有构建命令基于 `builder.params` 调用 `unify_builder`，输出到日志文件后解析
8. `size` 默认分析最近一次构建产物的 .elf 文件

## 脚本调用

skill 目录下有 Python 脚本，使用标准库 + PyYAML 实现。

### eide_project.py — 工程扫描与配置枚举

```bash
# 扫描工程
python <skill-dir>/scripts/eide_project.py scan --root <搜索目录> --json

# 枚举构建配置
python <skill-dir>/scripts/eide_project.py configs --project <工程目录> --json
```

### eide_build.py — 构建 / 重建 / 清理

```bash
python <skill-dir>/scripts/eide_build.py <build|rebuild|clean> \
  --builder-dir <unify_builder目录> \
  --project <工程根目录> \
  --config <配置名称> \
  --log-dir <日志目录> \
  --json
```

`rebuild` 额外支持 `--clean-first` 先清理再重建。

### eide_size.py — ELF 大小分析

```bash
# 基本分析
python <skill-dir>/scripts/eide_size.py analyze \
  --elf <elf文件路径> \
  --toolchain-prefix arm-none-eabi- \
  --json

# 对比分析
python <skill-dir>/scripts/eide_size.py compare \
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
  "details": {
    "project": "Vendor/EIDE",
    "config": "W20_Mainboard",
    "build_dir": "build/W20_Mainboard",
    "elf_file": "build/W20_Mainboard/MDK-ARM_F403A.elf",
    "hex_file": "build/W20_Mainboard/MDK-ARM_F403A.hex",
    "log_file": ".embeddedskills/build/MDK-ARM_F403A-W20_Mainboard-build.log"
  },
  "artifacts": {
    "elf_file": "build/W20_Mainboard/MDK-ARM_F403A.elf",
    "hex_file": "build/W20_Mainboard/MDK-ARM_F403A.hex",
    "flash_file": "build/W20_Mainboard/MDK-ARM_F403A.hex",
    "debug_file": "build/W20_Mainboard/MDK-ARM_F403A.elf"
  },
  "metrics": { "errors": 0, "warnings": 2, "flash_bytes": 32768, "ram_bytes": 8192 }
}
```

错误示例：
```json
{
  "status": "error",
  "action": "build",
  "error": { "code": "builder_not_found", "message": "unify_builder.exe 不存在，请确认 EIDE 扩展已安装" }
}
```

## 核心规则

- 不修改 `.eide/eide.yml` 或任何 EIDE 工程配置文件
- 不自动猜测工程路径或构建配置，有歧义时必须询问用户
- 参数解析优先级详见上方"参数解析优先级"章节
- 构建成功后优先使用返回的 `flash_file` / `debug_file` 与 `jlink/openocd` 串联
- `clean` 不在自动流程中隐式执行
- 构建失败时优先展示首个错误和日志文件路径
- 结果回显中始终包含工程名、配置名、构建目录路径；构建成功时优先回显产物路径
- EIDE 工程以包含 `.eide/eide.yml` 的目录为根目录

## 与 Keil 工程的关系

本项目中的 EIDE 工程与 Keil MDK 工程共享相同的源码和 ARM CC 工具链（`D:\Keil_V543\ARM\ARMCLANG`）。EIDE 工程通过 `eide.yml` 描述工程结构，`builder.params` 由 EIDE 自动生成并供 `unify_builder` 使用。两者的构建产物（.axf/.hex/.elf）格式兼容，可互换使用。

## 参考

- EIDE 扩展：在 VS Code 中搜索 `cl.eide` 安装
- `eide.yml` 格式：见 EIDE 扩展文档
- `builder.params`：由 EIDE 自动生成，位于 `build/<ConfigName>/builder.params`
