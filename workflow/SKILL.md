---
name: workflow
description: 独立的嵌入式工程规划与只读诊断工具。用于识别 Keil、GCC、EIDE 工程和已有固件，检查本机工具，并生成构建、烧录、调试、观测和测试验证的原生工具命令与前置条件。
argument-hint: "[plan|diagnose] ..."
---

# Workflow

支持 Windows 和 Linux，需 Python 3.10+。原生工具由用户按工程选用，规划可在工具缺失时返回缺项。

```console
python <skill-dir>/scripts/workflow.py plan --workspace . --json
python <skill-dir>/scripts/workflow.py diagnose --workspace . --json
```

- `plan` 按构建、烧录、调试、观测、测试验证列出步骤，输出可用的参数数组和工作目录，或列出候选与前置条件。
- `diagnose` 检查工程文件、产物、工具路径和 verify 计划，返回问题及建议。

两条命令只读。支持 `--project`、`--build-profile`、`--firmware`、`--elf`、各阶段的 `--*-backend`，以及 `--verify-action {run,unit,smoke}`、`--verify-plan`。工程默认值用工作区 `.embeddedskills/config.json` 的 `workflow` 段和顶层 `verify` 段，本机工具路径用 Skill 自己的 `config.json`。输出是 UTF-8 JSON，基本字段为 `status`、`action`、`summary` 和 `details`。

完整配置与返回约定见 [README](README.md)。
