# probe-rs

`probe-rs` skill 为本仓库新增的第三调试后端，目标是和现有 `jlink`、`openocd` 保持同一套 JSON 输出和 `workflow` 编排方式。

## 能力范围

- 探针发现：`list`
- 目标信息：`info`
- 烧录/擦除/复位：`flash` `erase` `reset`
- 内存访问：`read-mem` `write-mem`
- one-shot 调试：`probe_rs_gdb.py`
- RTT 观测：`probe_rs_rtt.py`

## 配置示例

环境级 `config.json` 可参考 config.example.json。

工程级 `.embeddedskills/config.json`：

```json
{
  "probe-rs": {
    "chip": "STM32F407VGTx",
    "protocol": "swd",
    "probe": "",
    "speed": 4000,
    "connect_under_reset": false
  }
}
```

## 重要说明

- `probe-rs` 默认依赖外部官方 CLI，不在本仓库内代管安装器
- `workflow` 中的 `probe-rs` 只接入 one-shot 调试，不直接暴露交互式 DAP 会话
- Windows 下如需让 `probe-rs` 访问 `J-Link`，通常需要将驱动切换到 `WinUSB`；这可能导致 SEGGER 官方工具不可用
