# Verify — 嵌入式测试闭环

`verify` 把嵌入式开发闭环从“构建 → 烧录 → 观察”补齐为“构建 → 烧录 → **自动测试判定** → 证据回灌”。
当前版本（P0+P1）覆盖：

- **host 单元测试**：CTest / pytest / 自定义命令，解析 JUnit XML
- **串口黑盒冒烟**：复位、发激励、等待输出、正则断言、超时与失败上下文
- **统一证据**：JSON 结果、时间戳报告、`latest.json`、`.embeddedskills/state/verify.json` 的 `last_verify`
- **workflow 规划**：`workflow plan` 输出只读 verify 命令；测试失败由 verify 返回非零并写入 `last_verify`

压测、白盒探针断言和覆盖率采集是后续阶段，不在当前 P0+P1 范围内。

## 安装

```bash
python -m pip install pyserial
```

`pyserial` 只在串口冒烟时需要；`unit`、`scan` 不依赖它。CTest 随 CMake 提供，
pytest 通过 `python -m pip install pytest` 安装。

## 快速开始

```bash
# 1. 查看当前工程能发现什么
python scripts/verify.py scan --workspace <工程> --json

# 2. 创建测试计划（示例见 references/plan-schema.md）
#    .embeddedskills/verify/plan.json

# 3. 先预演，确认步骤和设备
python scripts/verify.py smoke --workspace <工程> --dry-run --json

# 4. 跑完整闭环
python scripts/verify.py run --workspace <工程> --json
```

## 测试计划

计划是版本化资产，推荐提交到仓库：

```json
{
  "name": "gateway-smoke",
  "unit": {
    "command": ["ctest", "--test-dir", "build", "--output-on-failure", "--output-junit", "{junit}"]
  },
  "smoke": {
    "serial": { "port": "COM7", "baudrate": 115200 },
    "reset": { "backend": "jlink", "device": "STM32F407VG", "wait_ms": 500 },
    "fail_fast": true,
    "cases": [
      {
        "name": "boot-banner",
        "steps": [
          { "reset": {} },
          { "serial_expect": { "contains": "BOOT", "timeout_sec": 5 } }
        ]
      }
    ]
  }
}
```

完整字段、步骤和优先级见 [`references/plan-schema.md`](references/plan-schema.md)。

## 子命令

| 子命令 | 说明 |
|--------|------|
| `scan` | 发现计划、单测框架、串口、调试工具、最近 build/flash |
| `unit` | 执行 host 单测并解析 JUnit |
| `smoke` | 执行串口黑盒冒烟 |
| `run` | 先 unit 后 smoke，任一失败即失败 |
| `report` | 读取最近一次测试报告 |

常用参数：`--workspace`、`--plan`、`--json`、`--dry-run`、`--case`、`--junit`、
`--port`、`--baudrate`、`--report`。

## 与 workflow 配合

workflow 只读规划，不执行测试。`workflow plan` 会输出一个 `stage=verify` 的步骤，
其中 `commands` 就是下面这条测试命令：

```bash
python workflow/scripts/workflow.py plan --workspace <工程> --json
python verify/scripts/verify.py run --workspace <工程> --json --plan .embeddedskills/verify/plan.json
```

`--verify-action` 和 `--verify-plan` 可覆盖 `verify.action` / `verify.plan`。测试结果写入
`last_verify`；`workflow diagnose` 只检查 verify 脚本和计划是否就绪，不执行测试。

## 安全提示

- 冒烟会复位目标，不要在量产或正在调试的板子上直接跑
- 先用 `--dry-run` 检查复位后端、串口和断言
- 串口占用/驱动/波特率问题会返回 `serial_open_failed`，不会静默跳过
- 时间敏感断言请放宽超时，避免把调度抖动误判为失败

## 目录

```
verify/
├── SKILL.md
├── README.md
├── config.example.json
├── references/plan-schema.md
├── scripts/
│   ├── verify.py
│   ├── verify_runtime.py
│   ├── verify_plan.py
│   └── verify_runners.py
└── tests/test_verify.py
```