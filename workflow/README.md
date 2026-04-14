# Workflow

`workflow` 是一个薄编排层，只负责：

- 发现当前 workspace 中的 Keil / GCC 工程
- 选择构建、烧录、调试、观测后端
- 串联 `.embeddedskills/state.json`
- 聚合底层脚本输出

当前 `observe` 阶段会返回 `jlink:rtt`、`jlink:swo`、`openocd:semihosting`、`openocd:itm` 这几类候选观测后端。

它不会重写底层构建器、烧录器或 GDB 解析逻辑。

## 命令

```bash
python workflow/scripts/workflow_plan.py --json
python workflow/scripts/workflow_run.py plan --json
python workflow/scripts/workflow_run.py build --json
python workflow/scripts/workflow_run.py build-flash --json
python workflow/scripts/workflow_run.py build-debug --json
python workflow/scripts/workflow_run.py observe --json
python workflow/scripts/workflow_run.py diagnose --json
```

## 配置

workflow 不再维护独立的工程配置结构，所有工程参数统一从 `.embeddedskills/config.json` 读取。

### 工程级共享配置

在 `.embeddedskills/config.json` 中，`workflow` 段仅包含首选后端配置：

```json
{
  "workflow": {
    "preferred_build": "auto",
    "preferred_flash": "auto",
    "preferred_debug": "auto",
    "preferred_observe": "auto"
  },
  "keil": { "project": "...", "target": "..." },
  "gcc": { "project": "...", "preset": "..." },
  "jlink": { "device": "...", "interface": "SWD" },
  "openocd": { "board": "...", "interface": "..." }
}
```

### 参数解析顺序

1. **CLI 参数**（如 `--build-backend=keil`）优先级最高
2. **`.embeddedskills/config.json`** 中的 `workflow` 段配置
3. **自动发现**（当 `preferred_*` 为 `"auto"` 时）

成功执行后，实际使用的后端会自动写回 `.embeddedskills/config.json` 的 `workflow` 段，供下次使用。

### 与其他 Skill 的协同

workflow 与其他 Skill 的协同只通过以下方式：
- `.embeddedskills/config.json`：读取各 Skill 的工程配置
- `.embeddedskills/state.json`：读取/写入运行状态
- 子进程调用底层 Skill 脚本
