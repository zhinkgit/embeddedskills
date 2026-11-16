# codecheck

嵌入式 C/C++ 编码规范检查 skill：在编译前检查格式、命名与静态分析问题，输出统一 JSON，
供 AI 自动修复或作为 CI 门禁。

## 能力

| 子命令 | 说明 | 依赖 |
|--------|------|------|
| `scan` | 发现工程内可检查的 C/C++ 文件 | 无 |
| `format` | `clang-format --dry-run --Werror` 格式检查 | clang-format |
| `tidy` | `clang-tidy` 静态检查 | clang-tidy |
| `check` | 格式 + 静态检查，按 `fail_on` 判定 | 两者 |
| `rules` | 输出规则库（MISRA/CERT/项目规范映射） | 无 |

## 快速开始

```bash
# 1. 查看可检查文件
python scripts/codecheck.py scan --workspace <workspace> --json

# 2. 跑完整检查（使用工程内 .clang-format / .clang-tidy）
python scripts/codecheck.py check --workspace <workspace> --fail-on warning --json

# 3. 查看规则库
python scripts/codecheck.py rules --json
```

工具不在 `PATH` 时，可通过 CLI 或 `config.json` 指定：

```bash
python scripts/codecheck.py format --workspace <workspace> --clang-format-exe "C:/LLVM/bin/clang-format.exe" --json
```

## 配置

- 环境级：`skill/config.json`（工具路径、超时、默认 `fail_on`），参考 `config.example.json`
- 工程级：`.embeddedskills/config.json` 的 `codecheck` 段（源根、include、define、排除、compile_db、style/tidy 文件）
- 状态：`.embeddedskills/state.json` 的 `last_check`

完整字段、参数优先级与输出格式见 [SKILL.md](SKILL.md)。

## 规范内容

- [references/coding-guide.md](references/coding-guide.md)：团队版嵌入式 C 编码规范（写码前指南）
- [references/rules.json](references/rules.json)：机器可读规则库（14 条起步规则）

## 依赖

- Python 3.10+（仅标准库）
- clang-format、clang-tidy
- 可选：cppcheck / PC-Lint（后续后端）

## 测试

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

测试不依赖本机 clang 工具链：工具调用通过 `run_process` 注入 mock，重点验证参数解析、
诊断解析、门禁判定与配置/状态读写。

## 已知限制

- 只读检查，不自动修改源文件（后续版本提供 `--fix` + diff 确认）
- 暂无 baseline/豁免机制（存量工程建议先用 `--fail-on error` 逐步收紧）
- clang-tidy 依赖编译参数；优先通过 build skill 生成 `compile_commands.json`
- `readability-identifier-naming` 需要在 `.clang-tidy` 的 `CheckOptions` 中显式配置项目命名风格