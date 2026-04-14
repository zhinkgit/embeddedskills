---
name: keil
description: >-
  Keil MDK 工程构建工具，用于扫描 .uvprojx/.uvmpw 工程、枚举 Target、执行
  build/rebuild/clean 并解析构建日志，返回可供 jlink/openocd 复用的产物路径。
  flash 子命令仅作为兼容入口保留。当用户提到 Keil、MDK、uVision、UV4、
  Target 枚举、编译、重建、清理、烧录、下载固件、flash 时自动触发，也兼容 /keil 显式调用。
  即使用户只是说"编译一下"或"烧录到板子上"，只要上下文涉及嵌入式 Keil 工程就应触发此 skill。
argument-hint: "[scan|targets|build|rebuild|clean|flash] ..."
---

# Keil MDK 工程构建

本 skill 提供 Keil MDK 工程的发现、Target 枚举、构建、重建、清理能力，并返回可供 `jlink/openocd` 继续使用的固件产物路径。`flash` 仅作为兼容入口保留。

## 配置

### 环境级配置（skill/config.json）

skill 目录下的 `config.json` 包含环境级配置，首次使用前确认 `uv4_exe` 路径正确：

```json
{
  "uv4_exe": "C:\\Keil_v5\\UV4\\UV4.exe",
  "operation_mode": 1
}
```

- `uv4_exe`：UV4.exe 完整路径（必填）
- `operation_mode`：`1` 直接执行 / `2` 输出风险摘要但不阻塞 / `3` 执行前确认

### 工程级配置（workspace/.embeddedskills/config.json）

工程级共享配置统一保存在工作区的 `.embeddedskills/config.json` 中：

```json
{
  "keil": {
    "project": "",
    "target": "",
    "log_dir": ".embeddedskills/build"
  }
}
```

- `project`：默认工程路径（相对 workspace），构建成功后会自动更新
- `target`：默认 Target 名称，构建成功后会自动更新
- `log_dir`：构建日志输出目录，默认 `.embeddedskills/build`

### 参数解析优先级

参数解析顺序（从高到低）：
1. CLI 显式参数
2. 环境级配置（skill/config.json）
3. 工程级配置（.embeddedskills/config.json）
4. state.json（上次构建记录）
5. 搜索/询问

## 子命令

| 子命令 | 用途 | 风险 |
|--------|------|------|
| `scan` | 搜索当前目录下的 .uvprojx/.uvmpw 工程 | 低 |
| `targets` | 枚举工程中的 Target | 低 |
| `build` | 增量编译 | 中 |
| `rebuild` | 全量重建 | 中 |
| `clean` | 清理工程 | 高 |
| `flash` | 通过 Keil 烧录固件（兼容入口，优先建议使用 jlink/openocd） | 高 |

## 执行流程

1. 读取 `config.json`，确认 `uv4_exe` 路径有效
2. 未指定子命令时默认执行 `scan`
3. 未提供工程路径时先执行 `scan` 搜索工程
4. 同时发现多个工程或多个 Target 时，列出选项让用户选择，绝不自动猜测
5. `build/rebuild/clean` 按 `operation_mode` 决定是否需要确认
6. `build/rebuild` 成功后，尽量从工程配置中解析 `flash_file` / `debug_file` 等产物路径
7. `flash` 仅在最近一次构建成功时允许执行
8. 所有构建命令输出到日志文件后解析，返回结构化结果

## 脚本调用

skill 目录下有两个 Python 脚本，使用标准库实现，无额外依赖。

### keil_project.py — 工程扫描与 Target 枚举

```bash
# 扫描工程
python <skill-dir>/scripts/keil_project.py scan --root <搜索目录> --json

# 枚举 Target
python <skill-dir>/scripts/keil_project.py targets --project <工程路径> --json
```

### keil_build.py — 构建 / 重建 / 清理 / 烧录

```bash
python <skill-dir>/scripts/keil_build.py <build|rebuild|clean|flash> \
  --uv4 <UV4路径> \
  --project <工程路径> \
  --target <TargetName> \
  --log-dir <日志目录> \
  --json
```

`rebuild` 额外支持 `--clean-first` 使用 `-cr` 而非 `-r`。

## 输出格式

所有脚本以 JSON 格式返回，基础字段为 `status`（ok/error）、`action`、`summary`、`details`，并可能附带 `context`、`artifacts`、`metrics`、`state`、`next_actions`、`timing`。

成功示例：
```json
{
  "status": "ok",
  "action": "build",
  "summary": "build 成功，errors=0 warnings=2",
  "details": {
    "project": "project.uvprojx",
    "target": "Debug",
    "log_file": ".build/project-Debug-build.log",
    "flash_file": "Objects/project.hex",
    "debug_file": "Objects/project.axf"
  },
  "metrics": { "errors": 0, "warnings": 2, "flash_bytes": 32768, "ram_bytes": 8192 }
}
```

错误示例：
```json
{
  "status": "error",
  "action": "flash",
  "error": { "code": "build_not_clean", "message": "最近一次构建存在错误，禁止继续烧录" }
}
```

## 核心规则

- 不修改工程配置文件（.uvprojx / .uvmpw / .uvoptx）
- 不自动猜测工程路径或 Target，有歧义时必须询问用户
- 参数解析优先级为：CLI 显式参数 > 环境级配置 > 工程级配置 > `.embeddedskills/state.json` > 搜索/询问
- 构建成功后优先使用返回的 `flash_file` / `debug_file` 与 `jlink/openocd` 串联
- `flash` 前必须确认最近一次构建成功（errors == 0）
- `clean` 不在自动流程中隐式执行
- 构建失败时优先展示首个错误和日志文件路径
- 结果回显中始终包含工程名、Target 名、日志路径；若识别到产物路径也要回显

## 参考

遇到编译器相关问题时可查阅 `references/compiler-notes.md`。
