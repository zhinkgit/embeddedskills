# grok-search

Claude Code skill，通过 Grok API 进行实时联网搜索，返回结构化 JSON 结果（含来源链接）。

## 功能

- 实时联网搜索，获取最新信息
- 支持 `chat` 和 `responses` 两种 API 模式（自动检测）
- 返回结构化 JSON：答案、来源链接、token 用量、耗时
- 支持 thinking 模型的推理输出
- 失败自动重试

## 环境要求

- Python 3.x（仅标准库，无额外依赖）
- 可用的 Grok API 端点和 API Key

## 配置

复制 `config.example.json` 为 `config.json`，填入实际值：

```json
{
  "base_url": "https://your-grok-endpoint.example",
  "api_key": "YOUR_API_KEY",
  "model": "grok-2-latest",
  "timeout_seconds": 60,
  "api_type": "auto",
  "verify_ssl": true,
  "system_prompt": "",
  "max_retries": 1,
  "extra_body": {},
  "extra_headers": {}
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `base_url` | 是 | API 端点地址 |
| `api_key` | 是 | API 密钥 |
| `model` | 否 | 模型名称，默认 `grok-2-latest` |
| `timeout_seconds` | 否 | 请求超时秒数，默认 60 |
| `api_type` | 否 | `auto` / `chat` / `responses`，默认 `auto` |
| `verify_ssl` | 否 | SSL 证书验证，默认 `true` |
| `system_prompt` | 否 | 自定义系统提示词（仅 chat 模式） |
| `max_retries` | 否 | 5xx/超时重试次数，默认 1 |
| `extra_body` | 否 | 合并到请求体的额外字段 |
| `extra_headers` | 否 | 额外 HTTP 头 |

也支持环境变量配置：`GROK_BASE_URL`、`GROK_API_KEY`、`GROK_MODEL`、`GROK_API_TYPE`、`GROK_VERIFY_SSL`。
