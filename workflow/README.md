# Workflow

Workflow 在 Windows 和 Linux 上用 Python 3.10+ 执行只读规划和诊断。每一步返回可核对的原生工具参数数组，或返回缺项及候选。调用方确认后另行执行相应步骤。

```console
python scripts/workflow.py plan --workspace /path/to/project --build-backend gcc --build-profile debug --json
python scripts/workflow.py diagnose --workspace /path/to/project --json
```

工作区 `.embeddedskills/config.json` 示例：

```json
{
  "workflow": {
    "build_backend": "gcc",
    "flash_backend": "probe-rs",
    "debug_backend": "probe-rs",
    "observe_backend": "probe-rs",
    "gcc": { "project": ".", "preset": "debug" },
    "probe-rs": { "chip": "STM32F407VGTx", "protocol": "swd" },
    "firmware": "build/app.hex",
    "elf": "build/app.elf"
  },
  "verify": {
    "action": "run",
    "plan": ".embeddedskills/verify/plan.json"
  }
}
```

Keil 可设置 `keil.project` 和 `keil.target`。EIDE 可设置 `eide.project`、`eide.config` 和已生成的 `eide.params_file`。OpenOCD 使用 `openocd.board`，或 `openocd.interface` 与 `openocd.target`。J-Link 烧录使用 `jlink.command_file`，RTT/SWO 观测由 `jlink.observe_argv` 描述对应原生客户端命令。工具路径在 Skill 本机配置的 `tools` 段设置，参见 [配置示例](config.example.json)。

`verify` 段是顶层配置（与 `workflow` 平级），用于规划测试步骤：`verify.action` 可选 `run`、`unit`、`smoke`，`verify.plan` 指向 [verify 计划](../verify/references/plan-schema.md)。`--verify-action` 和 `--verify-plan` 优先于配置。`plan` 输出的 verify 步骤是只读命令，workflow 不会执行它；调用方执行后由 verify 自己写 `last_verify`。

计划输出 `details.steps`。每项通过 `status` 区分 `ready` 和 `blocked`，并提供 `commands` 或 `requires`。调试服务器步骤的 `background: true` 表示调用方应启动服务器、等待其就绪，再运行后续命令。诊断输出 `details.issues`。若有步骤待补齐，总体结果仍表示只读检查请求本身执行成功。
