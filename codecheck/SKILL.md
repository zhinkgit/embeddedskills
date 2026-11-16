---
name: codecheck
description: >-
  嵌入式 C/C++ 编码规范检查与门禁工具，用于在编译前检查代码格式、命名规范、
  MISRA C / CERT C 类静态问题，并输出可定位、可修复的结构化诊断。封装
  clang-format、clang-tidy（可选 cppcheck / PC-Lint），支持 scan/format/tidy/check/rules
  子命令，返回统一 JSON，可供 AI 自动修复或作为 CI 门禁。当用户提到编码规范、代码规范、
  代码风格、格式化检查、MISRA、CERT C、静态分析、clang-tidy、clang-format、cppcheck、
  PC-Lint、代码审查、提交前检查时自动触发，也兼容 /codecheck 显式调用。即使用户只是说
  "检查一下代码规范"、"这代码符合 MISRA 吗"、"跑一下 lint"，只要上下文涉及嵌入式 C/C++
  代码质量检查，就应触发此 skill。
argument-hint: "[scan|format|tidy|check|rules] ..."
---

# codecheck — 嵌入式编码规范检查

在 `build` 之前补一个"编码规范检查"阶段，把格式、命名、静态分析统一成 AI 可消费的
结构化诊断；默认只读，不修改源文件。

## 闭环位置

```text
生成/修改代码 → [codecheck check] → build → flash → debug → observe
                  ↑ 失败即停，可配合 workflow check / build-check 使用
```

## 配置

### 环境级配置 (`skill/config.json`)

只放本机工具路径、超时和默认门禁等级：

```json
{
  "clang_format_exe": "",
  "clang_tidy_exe": "",
  "cppcheck_exe": "",
  "timeout_seconds": 600,
  "fail_on": "warning"
}
```

### 工程级配置 (`.embeddedskills/config.json`)

```json
{
  "codecheck": {
    "source_roots": ["APP", "BSP", "USER", "SYS", "NET", "src", "include"],
    "include_dirs": ["APP", "BSP", "CMSIS", "include", "LIB/inc", "RTE"],
    "defines": ["USE_HAL_DRIVER", "STM32F10X_HD"],
    "exclude": ["LIB", "CMSIS", "portable", "DebugConfig", "build"],
    "compile_db": ".embeddedskills/build",
    "style_file": ".clang-format",
    "tidy_config_file": ".clang-tidy",
    "fail_on": "warning"
  }
}
```

`compile_db` 指向 `compile_commands.json` 所在目录；没有时退化为 `include_dirs` + `defines`
（此时 clang-tidy 可能因头文件缺失产生解析错误，结果里会给出 note）。

### 状态文件 (`.embeddedskills/state.json`)

成功执行（包括门禁未通过）后写入 `last_check`：

```json
{
  "last_check": {
    "provider": "codecheck",
    "action": "check",
    "passed": false,
    "counts": {"error": 1, "warning": 3, "info": 0},
    "files": 82,
    "fail_on": "warning",
    "timestamp": "2026-11-16T15:00:00+08:00"
  }
}
```

## 参数解析优先级

- 工具路径：`CLI > skill/config.json > PATH > 默认命令名 > 报错`
- 工程参数（源根、include、define、排除、compile_db、style/tidy 文件、fail_on）：
  `CLI > .embeddedskills/config.json > 默认值/自动发现`
- `fail_on` / `timeout_seconds`：`CLI > 工程配置 > 环境配置 > 默认值`
- 不自动把本机绝对路径写回工程配置;成功后只更新 `state.json`。

## 子命令

| 子命令 | 用途 | 依赖 |
|--------|------|------|
| `scan` | 按源根/排除规则发现 C/C++ 文件 | Python 标准库 |
| `format` | `clang-format --dry-run --Werror` 检查格式 | clang-format |
| `tidy` | `clang-tidy` 静态检查，解析为 findings | clang-tidy |
| `check` | 格式 + 静态检查，按 `fail_on` 出结论 | clang-format + clang-tidy |
| `rules` | 输出机器可读规则库 | Python 标准库 |

```bash
# 发现可检查文件
python <skill-dir>/scripts/codecheck.py scan --workspace <workspace> --json

# 格式检查（未指定 style-file 时自动使用工作区 .clang-format）
python <skill-dir>/scripts/codecheck.py format --workspace <workspace> --fail-on warning --json

# 静态检查（优先使用 compile_commands.json）
python <skill-dir>/scripts/codecheck.py tidy --workspace <workspace> --compile-db <workspace>/build --json

# 完整门禁
python <skill-dir>/scripts/codecheck.py check --workspace <workspace> --fail-on error --json

# 列出规则库
python <skill-dir>/scripts/codecheck.py rules --json
```

## 执行流程

1. 读取环境级配置、工程级配置、状态文件；
2. 解析工具路径与工程参数，记录每个参数来源到 `context.parameter_sources`；
3. 按 `source_roots` / `exclude` 发现源文件（或使用 `--files` 精确指定）；
4. 依次执行格式检查、静态检查，解析为统一 findings；
5. 按 `fail_on` 判定门禁，写入 `state.last_check`；
6. 返回标准 JSON；失败时 `status=error`，`error.code=standard_violations`。

## 输出格式

```json
{
  "status": "error",
  "action": "check",
  "summary": "规范检查未通过：1 errors, 3 warnings（fail_on=error）",
  "details": {
    "files_checked": 82,
    "findings": [
      {
        "file": "APP/Order_config.c",
        "line": 128,
        "column": 9,
        "severity": "warning",
        "category": "tidy",
        "rule": "readability-identifier-naming",
        "message": "invalid case style for variable 'CurrentState'",
        "tool": "clang-tidy"
      }
    ],
    "finding_count": 4,
    "truncated": false,
    "gate": {"fail_on": "error", "passed": false,
             "counts": {"error": 1, "warning": 3, "info": 0}},
    "backends": {"clang_format": "...", "clang_tidy": "..."},
    "notes": []
  },
  "context": {
    "provider": "codecheck",
    "workspace": "...",
    "parameter_sources": {"clang_tidy_exe": "path:clang-tidy.exe"}
  },
  "metrics": {"files": 82, "findings": 4},
  "state": {"file": ".../.embeddedskills/state.json", "updated_keys": ["last_check"]},
  "error": {"code": "standard_violations", "message": "..."},
  "timing": {"started_at": "...", "finished_at": "...", "elapsed_ms": 8123}
}
```

退出码：

- `0`：检查完成且门禁通过
- `1`：检查完成但门禁未通过（`error.code=standard_violations`），或参数/工具错误
- `130`：用户中断

> 与部分既有 skill 不同，`--json` 模式不会把失败退出码改成 0;失败时退出码始终非 0。

## 规则

- 默认只读，不修改源文件；确需格式化时由用户显式执行 `clang-format -i` 或后续版本提供 `--fix`。
- 工具缺失、配置损坏、没有源文件都返回 `status=error`，不静默成功。
- 检查范围默认排除 `LIB`、`CMSIS`、`portable` 等第三方/生成代码（可在工程配置中覆盖）。
- findings 必须带 `file:line:column` 与 `rule`，便于 AI 定位与批量修复；单次最多返回 200 条，超出置 `truncated=true`。
- 规范内容见 `references/coding-guide.md`，规则库见 `references/rules.json`（`rules` 子命令可直接读取）。

## 依赖

- Python 3.10+（仅标准库）
- clang-format（格式检查）
- clang-tidy（静态检查）
- 可选：cppcheck（后续后端）、PC-Lint（商业工具，后续后端）

## 参考

- `references/coding-guide.md`：团队版嵌入式 C 编码规范（11 章，写码前指南）
- `references/rules.json`：规则库（规则 ID / 标准出处 / 工具映射 / 修复提示 / 正反例）
- `docs/skill-template-reference.md`：本 skill 遵循的运行时约定