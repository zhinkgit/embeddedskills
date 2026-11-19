# 测试计划 schema

测试计划是一个 JSON 对象，默认放在 `.embeddedskills/verify/plan.json`。
`verify` 在加载时会做严格校验：未知字段、类型错误、非法正则、重复用例名都会直接报错，
避免“写错了但被静默忽略”。

## 顶层

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 否 | 计划名，用于报告 |
| `description` | string | 否 | 计划说明 |
| `version` | number | 否 | 计划版本，便于后续迁移 |
| `$schema` | string | 否 | 编辑器提示用，不参与执行 |
| `unit` | object | 二选一 | host 单元测试 |
| `smoke` | object | 二选一 | 串口黑盒冒烟 |

`unit` 和 `smoke` 至少提供一段；同时提供时 `run` 先执行 unit，unit 失败则跳过 smoke。

## unit

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `command` | string[] | 自动探测 | 完整命令数组；支持 `{workspace}`、`{junit}` 占位符 |
| `working_dir` | string | 工作区根 | 相对路径按工作区解析 |
| `timeout_sec` | number | `600` | 单测超时 |
| `junit` | string | `.embeddedskills/reports/verify/unit.xml` | JUnit 输出路径 |

不提供 `command` 时自动探测：

1. `build/CTestTestfile.cmake` 或根目录 `CTestTestfile.cmake` → `ctest --test-dir ... --output-junit <junit>`
2. 存在 `pytest.ini` / `pyproject.toml` / `setup.cfg` / `tox.ini` 且存在 `tests/` → `python -m pytest -q --junitxml <junit>`
3. 都没有 → 报错 `tool_failed`，请在计划中显式提供 `command`

判定规则：进程退出码为 0，且 JUnit 中 `failures + errors == 0`，才算通过。
没有生成 JUnit 时，仅以退出码判定，并在报告中标记 `junit.available=false`。

## smoke

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `serial` | object | 工程配置 | 串口参数 |
| `reset` | object | 自动推断 | 默认复位方式，可被步骤内的 `reset` 覆盖 |
| `cases` | object[] | 必填 | 非空用例数组，名称不能重复 |
| `fail_fast` | bool | `false` | 第一条用例失败后是否停止后续用例 |

### smoke.serial

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `port` | string | 工程配置 | 如 `COM7`、`/dev/ttyUSB0` |
| `baudrate` | int | `115200` | 波特率 |
| `bytesize` | int | `8` | 5/6/7/8 |
| `parity` | string | `none` | none/even/odd/mark/space |
| `stopbits` | number | `1` | 1/1.5/2 |
| `encoding` | string | `utf-8` | 文本解码 |
| `timeout_sec` | number | `1.0` | 单次串口读超时 |

串口来源优先级：CLI `--port/--baudrate` > 计划 `smoke.serial` > 工程配置
`verify.serial` > 顶层 `serial` > 默认值。

### smoke.reset

| 字段 | 类型 | 说明 |
|------|------|------|
| `backend` | string | `jlink` / `openocd` / `probe-rs` / `none` |
| `command` | string[] | 自定义复位命令，支持 `{workspace}`；提供后忽略 backend |
| `device` | string | jlink 的 device 或 probe-rs 的 chip |
| `wait_ms` | int | 复位后额外等待 |
| `mode` | string | 传给 openocd reset 的模式 |

不写 `backend` 时按工程配置推断：有 `jlink.device` 用 jlink；有 `openocd` 配置用
openocd；有 `probe-rs.chip` 用 probe-rs。都推断不出且没有自定义命令时，执行 `reset`
步骤会报 `tool_failed`；`backend: "none"` 表示明确跳过复位。

### case

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 必填，非空且全局唯一 |
| `description` | string | 可选说明 |
| `timeout_sec` | number | 可选，整个用例的超时（硬上限，会截短后续阻塞步骤） |
| `steps` | object[] | 必填，非空步骤数组 |

`timeout_sec` 是整个用例的硬上限：`sleep`、`delay_ms`、`serial_expect` 和 `run`
都会按剩余时间截短；耗尽后该用例以“用例超时”失败，而不是等完整步骤结束。

### step

每个 step 必须是“单键对象”，键为步骤类型，值必须是对象（`serial_flush` 必须为空对象）。

#### `reset`

```json
{ "reset": { "backend": "jlink", "device": "STM32F407VG", "wait_ms": 500 } }
```

空对象 `{ "reset": {} }` 表示使用 `smoke.reset` 的默认配置。步骤内显式
`wait_ms: 0` 表示复位后不等待，不会再回退到 `smoke.reset.wait_ms`。

#### `sleep`

```json
{ "sleep": { "ms": 200 } }
{ "sleep": { "seconds": 0.5 } }
```

`ms` 与 `seconds` 二选一，非负。

#### `serial_send`

```json
{ "serial_send": { "data": "AT", "line_ending": "crlf", "delay_ms": 50 } }
{ "serial_send": { "data": "01 03 00 00 00 01", "hex": true } }
```

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `data` | string | 必填 | 文本或 hex 字符串 |
| `line_ending` | string | `none` | none/cr/lf/crlf |
| `hex` | bool | `false` | 按十六进制解析 `data` |
| `encoding` | string | 串口编码 | 文本编码 |
| `delay_ms` | int | `0` | 发送后等待 |

#### `serial_expect`

```json
{ "serial_expect": { "contains": "BOOT", "timeout_sec": 5 } }
{ "serial_expect": { "regex": "(?i)ready|boot ok", "timeout_sec": 3 } }
```

`regex` 与 `contains` 二选一；`contains` 会做正则转义。可选的 `encoding`
覆盖串口默认编码，适合按 ISO-8859-1 等协议读取设备输出。超时会失败，并在错误里带上
已收到的最后 1000 字符，便于判断是没输出、波特率错还是断言错。

#### `serial_flush`

```json
{ "serial_flush": {} }
```

清空输入缓冲，常用于复位后丢弃旧日志。

#### `run`

```json
{ "run": { "command": ["python", "tools/check.py"], "timeout_sec": 30, "expect_exit_code": 0 } }
```

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `command` | string[] | 必填 | 命令数组，支持 `{workspace}` |
| `timeout_sec` | number | 无 | 超时 |
| `expect_exit_code` | int | `0` | 期望退出码 |
| `cwd` | string | 工作区根 | 工作目录 |

命令不存在或超时属于基础设施错误，分别返回 `command_not_found` / `timeout`，
不会记成普通断言失败；用例 `timeout_sec` 到期导致的截短仍记为该用例超时。

## 执行语义

1. `unit`：清理旧 JUnit → 执行 → 解析 → 判定 → 写报告/state
2. `smoke`：打开一次串口 → 逐用例执行步骤 → 用例失败默认继续下一条
3. `run`：unit 通过后才执行 smoke；unit 失败时 smoke 标记为 skipped
4. 任一失败：`status=error`，`error.code` 为 `unit_failed` 或 `test_failed`
5. `--dry-run`：只校验并打印步骤，不打开串口、不写 state/report
6. 报告：`.embeddedskills/reports/verify/<action>-<timestamp>.json` 和 `latest.json`
7. 状态：`.embeddedskills/state/verify.json` 的 `last_verify` 包含 `passed`、`action`、
   `totals`、`report`、`error_code`

## 扩展点

后续阶段会在不破坏现有字段的前提下增加：

- `stress`：循环次数/时长、上下电或复位策略、丢包/时延/内存水位阈值
- `probe`：通过 jlink/openocd 读变量、寄存器、断点命中，做白盒断言
- `coverage`：host 单测覆盖率（gcov/lcov）与门禁阈值

这些字段目前会被严格校验拒绝，避免“写了但没执行”的假闭环。