---
name: verify
description: >-
  嵌入式测试闭环工具，用于执行 host 单元测试和串口黑盒冒烟，解析 JUnit 结果并把
  通过/失败证据写入 state 和报告。当用户提到单元测试、冒烟测试、回归测试、测试闭环、
  串口自动化测试、JUnit、CTest、pytest、上电复位测试、测试门禁时自动触发，
  也兼容 /verify 显式调用。即使用户只是说“跑一下测试”“验一下板子能不能启动”
  或“把测试接进构建烧录流程”，只要上下文涉及自动判定固件/代码行为就应触发。
argument-hint: "[scan|unit|smoke|run|report] ..."
---

# Verify — 嵌入式测试闭环

把“构建 → 烧录 → 观察”补成“构建 → 烧录 → **测试判定** → 证据回灌”的闭环。
本 skill 只负责执行测试和判定，不修改源码、不自动修复失败。

## 能力边界

当前版本（P0+P1）聚焦两件事：

| 能力 | 方式 | 是否需要硬件 |
|------|------|--------------|
| `unit` | host 单元测试，支持 CTest / pytest / 自定义命令，解析 JUnit XML | 否 |
| `smoke` | 串口黑盒冒烟：复位、发激励、等输出、断言、超时 | 是（目标板 + 串口） |
| `scan` | 发现测试计划、单测框架、串口和最近 build/flash 状态 | 否 |
| `report` | 读取最近一次测试报告 | 否 |

压测、白盒探针断言、覆盖率采集已列入路线图，尚未在 P0+P1 中实现。
白盒能力会复用 `jlink` / `openocd` / `probe-rs` 的读内存与断点接口；压测会复用
`serial` / `can` / `net` 的收发接口，而不是重新实现底层协议。

## 配置

### 环境级配置 (`verify/config.json`)

```json
{
  "plan": "",
  "junit": "",
  "unit_timeout_sec": 600
}
```

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `plan` | 默认测试计划路径；为空时按约定位置查找 | `""` |
| `junit` | 默认 JUnit XML 输出路径 | `.embeddedskills/reports/verify/unit.xml` |
| `unit_timeout_sec` | host 单测超时 | `600` |

### 工程级配置 (`.embeddedskills/config.json`)

```json
{
  "verify": {
    "plan": ".embeddedskills/verify/plan.json",
    "junit": ".embeddedskills/reports/verify/unit.xml",
    "unit_timeout_sec": 600,
    "serial": { "port": "COM7", "baudrate": 115200 }
  },
  "serial": { "port": "COM7", "baudrate": 115200 }
}
```

`verify.serial` 会覆盖顶层 `serial` 中的同名字段；测试计划里的 `smoke.serial`
优先级更高。串口字段与 `serial` skill 保持一致。

### 参数解析优先级

1. **CLI 参数**：`--plan`、`--junit`、`--port`、`--baudrate`
2. **测试计划**：`unit.junit`、`smoke.serial.*`
3. **工程级配置**：`.embeddedskills/config.json` 的 `verify` 段
4. **环境级配置**：`verify/config.json`
5. **约定位置 / 默认值**

默认测试计划查找顺序：

1. `.embeddedskills/verify/plan.json`
2. `.embeddedskills/verify.json`
3. `verify.plan.json`

## 测试计划

测试计划是版本化测试资产，推荐放在 `.embeddedskills/verify/plan.json`，与固件一起提交。
完整 schema 见 [`references/plan-schema.md`](references/plan-schema.md)。

最小示例：

```json
{
  "name": "gateway-smoke",
  "unit": {
    "command": ["ctest", "--test-dir", "build", "--output-on-failure", "--output-junit", "{junit}"],
    "timeout_sec": 300
  },
  "smoke": {
    "serial": { "port": "COM7", "baudrate": 115200 },
    "reset": { "backend": "jlink", "device": "STM32F407VG", "wait_ms": 500 },
    "fail_fast": true,
    "cases": [
      {
        "name": "boot-banner",
        "timeout_sec": 10,
        "steps": [
          { "reset": {} },
          { "serial_expect": { "contains": "BOOT", "timeout_sec": 5 } },
          { "serial_send": { "data": "help", "line_ending": "crlf" } },
          { "serial_expect": { "regex": "(?i)commands?:", "timeout_sec": 3 } }
        ]
      }
    ]
  }
}
```

支持的步骤：

| 步骤 | 作用 |
|------|------|
| `reset` | 复位目标；`backend` 可显式指定，空对象时从工程配置推断 |
| `sleep` | 等待 `ms` 或 `seconds` |
| `serial_send` | 发送文本或 hex，可加 `cr/lf/crlf` 行尾 |
| `serial_expect` | 等待 `contains` 或 `regex`，超时即失败 |
| `serial_flush` | 清空输入缓冲，避免上一条用例残留 |
| `run` | 执行本地命令并校验退出码 |

`serial_expect` 失败时会记录已收到的串口内容，便于直接判断是固件没输出、波特率
不对，还是断言写错。

## 子命令

| 子命令 | 用途 | 风险 |
|--------|------|------|
| `scan` | 发现计划、框架、串口、最近 build/flash | 低 |
| `unit` | 执行 host 单测并解析 JUnit | 低 |
| `smoke` | 执行串口黑盒冒烟 | 中（会复位目标） |
| `run` | 依次执行计划中的 unit 与 smoke | 中 |
| `report` | 输出最近一次报告 | 低 |

```bash
# 发现环境
python scripts/verify.py scan --workspace <工程> --json

# 只跑 host 单测
python scripts/verify.py unit --workspace <工程> --json

# 预演冒烟步骤，不访问硬件
python scripts/verify.py smoke --workspace <工程> --dry-run --json

# 跑完整测试闭环
python scripts/verify.py run --workspace <工程> --json

# 只跑某个冒烟用例
python scripts/verify.py smoke --workspace <工程> --case boot-banner --json

# 读取最近报告
python scripts/verify.py report --workspace <工程> --json
```

## 执行流程

1. 解析工作区、计划路径和配置，校验计划结构（未知字段、非法正则、重复用例名直接报错）
2. `scan`：探测 CTest / pytest / Ceedling、串口、调试工具和最近 build/flash 状态
3. `unit`：清理旧 JUnit，执行命令，解析 XML，`exit=0 且 failures+errors=0` 才算通过
4. `smoke`：打开串口一次，逐用例执行复位/发送/等待/断言；用例失败默认继续下一条，
   `fail_fast=true` 时立即停止
5. `run`：先 unit 后 smoke；unit 失败时不再执行 smoke，避免把环境问题误判成冒烟失败
6. 报告写入 `.embeddedskills/reports/verify/`，同时更新 `latest.json`
7. 通过/失败写入 `.embeddedskills/state/verify.json` 的 `last_verify`
8. 测试失败返回 exit 1；基础设施错误也返回 exit 1，但用 `error.code` 区分

`--dry-run` 只校验计划并打印将要执行的命令/步骤，不打开串口、不写 state/report。

## 输出格式

统一 JSON：

```json
{
  "status": "ok",
  "action": "unit",
  "summary": "unit 通过：12 个用例，0 跳过",
  "details": {
    "unit": {
      "passed": true,
      "framework": "ctest",
      "totals": { "tests": 12, "passed": 12, "failed": 0, "errors": 0, "skipped": 0 },
      "junit": { "available": true, "path": "..." },
      "duration_ms": 1840
    }
  },
  "artifacts": {
    "report": ".embeddedskills/reports/verify/unit-20261118-201500.json",
    "latest_report": ".embeddedskills/reports/verify/latest.json"
  },
  "state": { "last_verify": { "passed": true } }
}
```

错误示例：

```json
{
  "status": "error",
  "action": "smoke",
  "summary": "1 个冒烟用例失败",
  "error": { "code": "test_failed", "message": "1 个冒烟用例失败" }
}
```

### 错误码

| `error.code` | 含义 |
|--------------|------|
| `config_invalid` | 配置/计划路径不存在、JSON 损坏 |
| `plan_invalid` | 计划结构或字段非法 |
| `unit_failed` | 单测失败或单测进程非零退出 |
| `timeout` | 单测或步骤命令超时 |
| `command_not_found` | 单测或步骤命令不存在 |
| `test_failed` | 冒烟断言失败/超时 |
| `serial_open_failed` | 串口打不开、被占用 |
| `tool_failed` | 测试工具缺失、复位命令不可用 |
| `interrupted` | 用户中断（exit 130） |

### 退出码

- `0`：测试通过，或 `scan`/`report`/`--dry-run` 正常完成
- `1`：测试失败或基础设施错误
- `130`：用户中断

## 与 workflow 集成

workflow 是只读规划层，不执行构建、烧录或测试。`plan` 会在 `details.steps` 中给出
一个 `stage=verify` 的只读步骤，调用方执行其 `commands` 即可完成测试判定：

```bash
# 规划（包含构建、烧录、调试、观测和 verify 步骤）
python workflow/scripts/workflow.py plan --workspace <工程> --json

# 单独执行规划出的 verify 命令
python verify/scripts/verify.py run --workspace <工程> --json --plan .embeddedskills/verify/plan.json
```

可用 `--verify-action {run,unit,smoke}` 和 `--verify-plan` 覆盖
`.embeddedskills/config.json` 中 `verify.action` / `verify.plan` 的默认值。
测试结果会写入 `last_verify`；workflow 的 `diagnose` 只负责检查 verify 脚本和计划是否就绪，
不会执行测试或写入运行状态。

## 依赖

- Python 3.10+
- `pyserial`：串口冒烟；未安装时 `unit`/`scan` 仍可用
- `ctest`（CMake）或 `pytest`：自动探测；也可用 `unit.command` 指定任意命令
- 复位后端：`jlink` / `openocd` / `probe-rs`，或计划里的自定义 `reset.command`

## 安全与限制

- 冒烟会复位目标，不要在量产或正在调试的板子上直接跑；先用 `--dry-run` 检查步骤
- 串口打开失败优先检查端口占用、驱动、波特率；错误信息会带上具体端口
- host 单测依赖工程自身的构建/测试命令，verify 不负责编译
- 时间敏感断言（启动耗时、心跳周期）建议放宽超时，避免把调度抖动当失败
- 当前不做自动修复、不做压测、不做覆盖率；这些能力的扩展点已在
  `references/plan-schema.md` 和方案文档中保留