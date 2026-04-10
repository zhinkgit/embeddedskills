---
name: grok-search
description: Real-time web research/search with sources (outputs JSON).
---

## When to use (aggressive)

- Default to using this skill before answering anything that might be outdated, ambiguous, or requires external confirmation (APIs, versions, errors, docs, releases).
- If you feel even slightly unsure, search first, then answer with evidence.

## Quick start

### Configure (recommended)

Run once to write config:

**Windows (PowerShell):**
```powershell
powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\.codex\skills\grok-search\configure.ps1"
```

**macOS / Linux:**
```bash
bash ~/.codex/skills/grok-search/configure.sh
```

Default config path: `~/.codex/skills/grok-search/config.json` (override with `--config` or `GROK_CONFIG_PATH`).

### Configure (env vars)

```bash
export GROK_BASE_URL="https://your-grok-endpoint.example"
export GROK_API_KEY="YOUR_API_KEY"
export GROK_MODEL="grok-2-latest"
export GROK_API_TYPE="auto"          # auto | chat | responses
export GROK_VERIFY_SSL="true"        # true | false
```

### Run

```bash
python scripts/grok_search.py --query "What changed in X recently?"
```

## API types

The skill supports two API endpoints, selected via `api_type` config or `--api-type` flag:

| Value | Endpoint | When |
|-------|----------|------|
| `chat` | `/v1/chat/completions` | Standard models (grok-2, grok-4.1-thinking, etc.) |
| `responses` | `/v1/responses` | Multi-agent models (grok-4.20-multi-agent-0309) |
| `auto` (default) | Auto-detect | Uses `responses` when model name contains `multi-agent`, otherwise `chat` |

The `responses` endpoint automatically enables `web_search` tool for the model.

## Output

Prints JSON to stdout:

- `ok`: boolean success flag
- `content`: the synthesized answer
- `sources`: best-effort list of URLs (and optional titles/snippets)
- `raw`: raw assistant content (if JSON parsing failed, chat mode only)
- `api_type`: which API was used (`chat` or `responses`)
- `reasoning`: thinking/reasoning content (only present for thinking models)
- `usage`: token usage info
- `elapsed_ms`: request duration in milliseconds

## Config fields

| Field | Default | Description |
|-------|---------|-------------|
| `base_url` | (required) | API endpoint base URL |
| `api_key` | (required) | Authentication key |
| `model` | `grok-2-latest` | Model identifier |
| `timeout_seconds` | `60` | Request timeout |
| `api_type` | `auto` | `auto`, `chat`, or `responses` |
| `verify_ssl` | `true` | Enable SSL certificate verification |
| `system_prompt` | (built-in) | Custom system prompt (chat mode only) |
| `max_retries` | `1` | Retry count for 5xx / timeout errors |
| `extra_body` | `{}` | Extra fields merged into request body |
| `extra_headers` | `{}` | Extra HTTP headers |

## Notes

- Endpoint: `POST {base_url}/v1/chat/completions` or `POST {base_url}/v1/responses`
- You can override model via `--model` or `GROK_MODEL`.
- If your API requires custom flags to enable web search, pass them via `--extra-body-json` / `GROK_EXTRA_BODY_JSON`.
- SSL verification is enabled by default. Set `verify_ssl: false` in config or `GROK_VERIFY_SSL=false` to disable.
- Failed requests (5xx, timeout) are retried up to `max_retries` times with 2s delay.
