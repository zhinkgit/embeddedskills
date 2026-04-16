---
name: workflow
description: >-
  embeddedskills 的薄编排层，用于在当前 workspace 中发现工程、选择 build/flash/debug/observe
  后端、串联 .embeddedskills/state.json，并聚合底层 skill 的结果。
  当用户提到"一键构建烧录""自动诊断""串起 build -> flash -> debug -> observe"
  或显式调用 /workflow 时触发。
argument-hint: "[plan|build|build-flash|build-debug|observe|diagnose] ..."
---

# Workflow 编排层

本 skill 不重复实现底层逻辑，只做发现、选择、串联和聚合。

`observe` 阶段当前会给出 `jlink:rtt`、`jlink:swo`、`openocd:semihosting`、`openocd:itm`、`probe-rs:rtt` 这几类候选后端。

## 命令

```bash
python <skill-dir>/scripts/workflow_plan.py --json
python <skill-dir>/scripts/workflow_run.py plan --json
python <skill-dir>/scripts/workflow_run.py build --json
python <skill-dir>/scripts/workflow_run.py build-flash --json
python <skill-dir>/scripts/workflow_run.py build-debug --json
python <skill-dir>/scripts/workflow_run.py observe --json
python <skill-dir>/scripts/workflow_run.py diagnose --json
```

## 配置说明

workflow 不再维护独立的工程配置结构，所有工程参数统一从 `.embeddedskills/config.json` 读取。

### 配置结构

`.embeddedskills/config.json` 中的 `workflow` 段仅包含首选后端配置：

```json
{
  "workflow": {
    "preferred_build": "auto",
    "preferred_flash": "auto",
    "preferred_debug": "auto",
    "preferred_observe": "auto"
  }
}
```

workflow 通过读取 `.embeddedskills/config.json` 中其他 skill 的配置段来获取工程参数（如 `keil.project`、`jlink.device`、`probe-rs.chip` 等）。

### 参数解析顺序

1. **CLI 参数**（如 `--build-backend=keil`）优先级最高
2. **`.embeddedskills/config.json`** 中的 `workflow` 段配置
3. **自动发现**（当 `preferred_*` 为 `"auto"` 时）

成功执行后，实际使用的后端会自动写回 `.embeddedskills/config.json` 的 `workflow` 段。

## 规则

- 发现多个工程或多个候选后端时，只返回候选列表，不自动猜测
- 构建、烧录、调试、观测之间优先通过 `.embeddedskills/state.json` 串联
- `observe` 只生成推荐命令，不在 workflow 内直接长时间占用观测通道
- 失败时优先返回哪个阶段失败，以及底层脚本的结构化错误
- workflow 与其他 Skill 的协同只通过 `.embeddedskills/config.json`、`.embeddedskills/state.json` 和子进程调用底层 Skill
