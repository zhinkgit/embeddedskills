# serial

Claude Code skill，用于嵌入式串口调试：端口扫描、实时监控、数据发送、Hex 查看和日志记录。

## 功能

- 扫描系统可用串口
- 实时监控串口文本输出（支持正则过滤、时间戳）
- 发送文本或 Hex 数据（支持 AT 命令调试）
- 二进制流 Hex 查看
- 串口日志保存（text / csv / json 格式）

## 环境要求

- Python 3.x
- [pyserial](https://pypi.org/project/pyserial/) — `pip install pyserial`
- USB 转串口芯片驱动（CH340、CP2102、FT232 等，按硬件安装对应驱动）

## 配置

复制 `config.example.json` 为 `config.json`，根据实际环境修改：

```json
{
  "default_port": "",
  "default_baudrate": 115200,
  "default_bytesize": 8,
  "default_parity": "none",
  "default_stopbits": 1,
  "default_encoding": "utf-8",
  "default_timeout_sec": 1.0,
  "default_log_dir": ".logs"
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `default_port` | 否 | 串口号（如 `COM3`），为空时需手动指定 |
| `default_baudrate` | 否 | 波特率，默认 115200 |
| `default_bytesize` | 否 | 数据位，默认 8 |
| `default_parity` | 否 | 校验位：`none` / `even` / `odd` / `mark` / `space` |
| `default_stopbits` | 否 | 停止位：`1` / `1.5` / `2` |
| `default_encoding` | 否 | 文本编码，默认 `utf-8` |
| `default_timeout_sec` | 否 | 读写超时秒数，默认 1.0 |
| `default_log_dir` | 否 | 日志输出目录，默认 `.logs` |

> 注意：所有连接参数仅从 `config.json` 读取，不通过命令行传递。
