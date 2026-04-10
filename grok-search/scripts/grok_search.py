import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Any


def _compact_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=False)


def _default_user_config_path() -> str:
    home = os.path.expanduser("~")
    return os.path.join(home, ".codex", "config", "grok-search.json")


def _skill_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _default_skill_config_paths() -> list[str]:
    root = _skill_root()
    return [
        os.path.join(root, "config.json"),
        os.path.join(root, "config.local.json"),
    ]


def _normalize_api_key(api_key: str) -> str:
    api_key = api_key.strip()
    if not api_key:
        return ""
    placeholder = {"YOUR_API_KEY", "API_KEY", "CHANGE_ME", "REPLACE_ME"}
    if api_key.upper() in placeholder:
        return ""
    return api_key


def _normalize_base_url_value(base_url: str) -> str:
    base_url = base_url.strip()
    if not base_url:
        return ""
    placeholder = {
        "https://your-grok-endpoint.example",
        "YOUR_BASE_URL",
        "BASE_URL",
        "CHANGE_ME",
        "REPLACE_ME",
    }
    if base_url.upper() in placeholder:
        return ""
    return base_url


def _load_json_file(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            value = json.load(f)
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict):
        raise ValueError("config must be a JSON object")
    return value


def _normalize_base_url(base_url: str) -> str:
    base_url = base_url.strip().rstrip("/")
    if base_url.endswith("/v1"):
        return base_url[: -len("/v1")]
    return base_url


def _coerce_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text:
        return None

    # 尝试直接解析 JSON
    if text.startswith("{") and text.endswith("}"):
        try:
            value = json.loads(text)
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            pass

    # 尝试从 Markdown 代码块中提取 JSON
    json_match = re.search(r'```json\s*\n(.*?)\n```', text, re.DOTALL)
    if json_match:
        try:
            value = json.loads(json_match.group(1))
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            pass

    # 尝试查找包含 content 和 sources 的 JSON 对象（花括号计数法）
    json_match = re.search(r'\{"content":', text)
    if json_match:
        try:
            start = json_match.start()
            brace_count = 0
            in_string = False
            escape_next = False

            for i in range(start, len(text)):
                char = text[i]

                if escape_next:
                    escape_next = False
                    continue

                if char == '\\' and in_string:
                    escape_next = True
                    continue

                if char == '"':
                    in_string = not in_string
                    continue

                if not in_string:
                    if char == '{':
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            json_str = text[start:i + 1]
                            value = json.loads(json_str)
                            return value if isinstance(value, dict) else None
        except (json.JSONDecodeError, ValueError):
            pass

    return None


def _extract_urls(text: str) -> list[str]:
    urls = re.findall(r"https?://[^\s)\]}>\"']+", text)
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        url = url.rstrip(".,;:!?'\"")
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _load_json_env(var_name: str) -> dict[str, Any]:
    raw = os.environ.get(var_name, "").strip()
    if not raw:
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"{var_name} must be a JSON object")
    return value


def _parse_json_object(raw: str, *, label: str) -> dict[str, Any]:
    raw = raw.strip()
    if not raw:
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


# ---------------------------------------------------------------------------
# SSE 解析（chat/completions 流式）
# ---------------------------------------------------------------------------

def _parse_chat_sse(raw: str) -> dict[str, Any]:
    """解析 chat/completions SSE 流式响应，合并 delta.content 和 reasoning_content"""
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    model_name = ""

    # 按 SSE 规范：事件以空行分隔，多个 data: 行拼接
    for block in re.split(r"\n\n+", raw.strip()):
        data_lines: list[str] = []
        for line in block.split("\n"):
            line = line.rstrip()
            if not line or line.startswith(":"):
                continue
            if line.startswith("data: "):
                payload = line[6:]
                if payload == "[DONE]":
                    continue
                data_lines.append(payload)
            elif line.startswith("data:"):
                payload = line[5:]
                if payload == "[DONE]":
                    continue
                data_lines.append(payload)

        if not data_lines:
            continue

        json_str = "".join(data_lines)
        try:
            chunk = json.loads(json_str)
        except json.JSONDecodeError:
            continue

        if not model_name and "model" in chunk:
            model_name = chunk["model"]

        choices = chunk.get("choices", [])
        if choices:
            delta = choices[0].get("delta", {})
            content = delta.get("content", "")
            if content:
                content_parts.append(content)
            reasoning = delta.get("reasoning_content") or delta.get("thinking", "")
            if reasoning:
                reasoning_parts.append(reasoning)

    result: dict[str, Any] = {
        "choices": [{"message": {"content": "".join(content_parts)}}],
        "model": model_name,
    }
    if reasoning_parts:
        result["reasoning"] = "".join(reasoning_parts)
    return result


# ---------------------------------------------------------------------------
# SSE 解析（/v1/responses 流式）
# ---------------------------------------------------------------------------

def _parse_responses_sse(raw: str) -> dict[str, Any]:
    """解析 /v1/responses SSE 流式响应"""
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    model_name = ""
    citations: list[dict[str, Any]] = []
    usage: dict[str, Any] = {}

    for block in re.split(r"\n\n+", raw.strip()):
        data_lines: list[str] = []
        for line in block.split("\n"):
            line = line.rstrip()
            if not line or line.startswith(":"):
                continue
            if line.startswith("data: "):
                payload = line[6:]
                if payload == "[DONE]":
                    continue
                data_lines.append(payload)
            elif line.startswith("data:"):
                payload = line[5:]
                if payload == "[DONE]":
                    continue
                data_lines.append(payload)

        if not data_lines:
            continue

        json_str = "".join(data_lines)
        try:
            chunk = json.loads(json_str)
        except json.JSONDecodeError:
            continue

        if not model_name and "model" in chunk:
            model_name = chunk["model"]

        # responses 完整事件
        if "output" in chunk:
            for item in chunk.get("output", []):
                if item.get("type") == "message":
                    for part in item.get("content", []):
                        if part.get("type") == "output_text":
                            content_parts.append(part.get("text", ""))
                        elif part.get("type") == "reasoning":
                            reasoning_parts.append(part.get("text", ""))

        # delta 事件
        event_type = chunk.get("type", "")
        if event_type == "response.output_text.delta":
            content_parts.append(chunk.get("delta", ""))
        elif event_type == "response.reasoning.delta":
            reasoning_parts.append(chunk.get("delta", ""))

        if "citations" in chunk:
            citations.extend(chunk["citations"])
        if "usage" in chunk:
            usage = chunk["usage"]

    result: dict[str, Any] = {
        "output": [
            {"type": "message", "role": "assistant",
             "content": [{"type": "output_text", "text": "".join(content_parts)}]}
        ],
        "model": model_name,
        "citations": citations,
        "usage": usage,
    }
    if reasoning_parts:
        result["reasoning"] = "".join(reasoning_parts)
    return result


# ---------------------------------------------------------------------------
# HTTP 请求（带重试）
# ---------------------------------------------------------------------------

def _http_post(
    *,
    url: str,
    body: dict[str, Any],
    headers: dict[str, str],
    timeout_seconds: float,
    verify_ssl: bool,
    max_retries: int,
) -> str:
    """发送 HTTP POST 请求，返回响应体文本。对 5xx / 超时自动重试。"""
    last_exc: Exception | None = None
    attempts = max_retries + 1

    for attempt in range(attempts):
        if attempt > 0:
            time.sleep(2)

        try:
            # 优先使用 requests 库
            try:
                import requests as _requests
                if not verify_ssl:
                    import urllib3
                    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                r = _requests.post(
                    url,
                    json=body,
                    headers=headers,
                    timeout=timeout_seconds,
                    verify=verify_ssl,
                )
                if r.status_code >= 500 and attempt < attempts - 1:
                    last_exc = urllib.error.HTTPError(url, r.status_code, r.text, {}, None)
                    continue
                if r.status_code != 200:
                    raise urllib.error.HTTPError(url, r.status_code, r.text, {}, None)
                return r.content.decode("utf-8", errors="replace")
            except ImportError:
                pass

            # 回退到 urllib
            req = urllib.request.Request(
                url=url,
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                return resp.read().decode("utf-8", errors="replace")

        except urllib.error.HTTPError as e:
            code = getattr(e, "code", 0) or 0
            if code >= 500 and attempt < attempts - 1:
                last_exc = e
                continue
            raise
        except (TimeoutError, OSError) as e:
            if attempt < attempts - 1:
                last_exc = e
                continue
            raise

    if last_exc:
        raise last_exc
    raise RuntimeError("unexpected retry exhaustion")


# ---------------------------------------------------------------------------
# API 类型自动检测
# ---------------------------------------------------------------------------

def _detect_api_type(api_type: str, model: str) -> str:
    """返回 'chat' 或 'responses'"""
    api_type = api_type.strip().lower()
    if api_type in ("chat", "responses"):
        return api_type
    # auto 模式：根据模型名判断
    model_lower = model.lower()
    if "multi-agent" in model_lower or "responses" in model_lower:
        return "responses"
    return "chat"


# ---------------------------------------------------------------------------
# /v1/chat/completions 请求
# ---------------------------------------------------------------------------

_DEFAULT_SYSTEM_PROMPT = (
    "You are a web research assistant. Use live web search/browsing when answering. "
    "Return ONLY a single JSON object with keys: "
    "content (string), sources (array of objects with url/title/snippet when possible). "
    "Keep content concise and evidence-backed."
)


def _request_chat_completions(
    *,
    base_url: str,
    api_key: str,
    model: str,
    query: str,
    timeout_seconds: float,
    verify_ssl: bool,
    max_retries: int,
    system_prompt: str,
    extra_headers: dict[str, Any],
    extra_body: dict[str, Any],
) -> dict[str, Any]:
    url = f"{_normalize_base_url(base_url)}/v1/chat/completions"

    system = system_prompt or _DEFAULT_SYSTEM_PROMPT

    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": query},
        ],
        "temperature": 0.2,
        "stream": False,
    }
    body.update(extra_body)

    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    for key, value in extra_headers.items():
        headers[str(key)] = str(value)

    raw = _http_post(
        url=url, body=body, headers=headers,
        timeout_seconds=timeout_seconds, verify_ssl=verify_ssl,
        max_retries=max_retries,
    )

    if raw.strip().startswith("data:"):
        return _parse_chat_sse(raw)
    return json.loads(raw)


# ---------------------------------------------------------------------------
# /v1/responses 请求
# ---------------------------------------------------------------------------

def _request_responses(
    *,
    base_url: str,
    api_key: str,
    model: str,
    query: str,
    timeout_seconds: float,
    verify_ssl: bool,
    max_retries: int,
    extra_headers: dict[str, Any],
    extra_body: dict[str, Any],
) -> dict[str, Any]:
    url = f"{_normalize_base_url(base_url)}/v1/responses"

    body: dict[str, Any] = {
        "model": model,
        "input": [
            {"role": "user", "content": query},
        ],
        "tools": [{"type": "web_search"}],
        "stream": False,
    }
    body.update(extra_body)

    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    for key, value in extra_headers.items():
        headers[str(key)] = str(value)

    raw = _http_post(
        url=url, body=body, headers=headers,
        timeout_seconds=timeout_seconds, verify_ssl=verify_ssl,
        max_retries=max_retries,
    )

    if raw.strip().startswith("data:"):
        return _parse_responses_sse(raw)
    return json.loads(raw)


# ---------------------------------------------------------------------------
# 统一响应处理
# ---------------------------------------------------------------------------

def _extract_chat_result(resp: dict[str, Any], query: str) -> dict[str, Any]:
    """从 chat/completions 响应中提取统一格式"""
    message = ""
    try:
        choice0 = (resp.get("choices") or [{}])[0]
        msg = choice0.get("message") or {}
        message = msg.get("content") or ""
    except Exception:
        message = ""

    parsed = _coerce_json_object(message)
    sources: list[dict[str, Any]] = []
    content = ""
    raw = ""

    if parsed is not None:
        content = str(parsed.get("content") or "")
        src = parsed.get("sources")
        if isinstance(src, list):
            for item in src:
                if isinstance(item, dict) and item.get("url"):
                    sources.append({
                        "url": str(item.get("url")),
                        "title": str(item.get("title") or ""),
                        "snippet": str(item.get("snippet") or ""),
                    })
        if not sources:
            for url in _extract_urls(content):
                sources.append({"url": url, "title": "", "snippet": ""})
    else:
        raw = message
        for url in _extract_urls(message):
            sources.append({"url": url, "title": "", "snippet": ""})

    result: dict[str, Any] = {
        "content": content,
        "sources": sources,
        "raw": raw,
        "usage": resp.get("usage") or {},
    }
    if resp.get("reasoning"):
        result["reasoning"] = resp["reasoning"]
    return result


def _extract_responses_result(resp: dict[str, Any], query: str) -> dict[str, Any]:
    """从 /v1/responses 响应中提取统一格式"""
    content_parts: list[str] = []
    for item in resp.get("output", []):
        if item.get("type") == "message":
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    content_parts.append(part.get("text", ""))

    content = "".join(content_parts)

    # 提取 citations
    sources: list[dict[str, Any]] = []
    for cite in resp.get("citations", []):
        if isinstance(cite, dict) and cite.get("url"):
            sources.append({
                "url": str(cite.get("url")),
                "title": str(cite.get("title") or ""),
                "snippet": str(cite.get("snippet") or ""),
            })

    # 如果没有 citations，尝试从内容中提取 URL
    if not sources:
        for url in _extract_urls(content):
            sources.append({"url": url, "title": "", "snippet": ""})

    result: dict[str, Any] = {
        "content": content,
        "sources": sources,
        "raw": "",
        "usage": resp.get("usage") or {},
    }
    if resp.get("reasoning"):
        result["reasoning"] = resp["reasoning"]
    return result


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Web research via OpenAI-compatible Grok endpoint (chat/completions & responses)."
    )
    parser.add_argument("--query", required=True, help="Search query / research task.")
    parser.add_argument("--config", default="", help="Path to config JSON file.")
    parser.add_argument("--base-url", default="", help="Override base URL.")
    parser.add_argument("--api-key", default="", help="Override API key.")
    parser.add_argument("--model", default="", help="Override model.")
    parser.add_argument("--api-type", default="", choices=["auto", "chat", "responses"],
                        help="API type: auto (default), chat, or responses.")
    parser.add_argument("--timeout-seconds", type=float, default=0.0, help="Override timeout (seconds).")
    parser.add_argument("--extra-body-json", default="", help="Extra JSON object merged into request body.")
    parser.add_argument("--extra-headers-json", default="", help="Extra JSON object merged into request headers.")
    args = parser.parse_args()

    # ---- 配置加载 ----
    env_config_path = os.environ.get("GROK_CONFIG_PATH", "").strip()
    explicit_config_path = args.config.strip() or env_config_path

    config_path = ""
    config: dict[str, Any] = {}

    if explicit_config_path:
        config_path = explicit_config_path
        try:
            config = _load_json_file(config_path)
        except Exception as e:
            sys.stderr.write(f"Invalid config ({config_path}): {e}\n")
            return 2
    else:
        fallback_path = ""
        fallback_config: dict[str, Any] = {}
        for candidate in [*_default_skill_config_paths(), _default_user_config_path()]:
            if not os.path.exists(candidate):
                continue
            try:
                candidate_config = _load_json_file(candidate)
            except Exception as e:
                sys.stderr.write(f"Invalid config ({candidate}): {e}\n")
                return 2

            if not fallback_path:
                fallback_path = candidate
                fallback_config = candidate_config

            candidate_key = _normalize_api_key(str(candidate_config.get("api_key") or ""))
            if candidate_key:
                config_path = candidate
                config = candidate_config
                break

        if not config_path and fallback_path:
            config_path = fallback_path
            config = fallback_config

        if not config_path:
            config_path = _default_skill_config_paths()[0]

    # ---- 解析参数（优先级：命令行 > 环境变量 > 配置文件）----
    base_url = _normalize_base_url_value(
        args.base_url.strip()
        or os.environ.get("GROK_BASE_URL", "").strip()
        or str(config.get("base_url") or "").strip()
    )
    api_key = _normalize_api_key(
        args.api_key.strip()
        or os.environ.get("GROK_API_KEY", "").strip()
        or str(config.get("api_key") or "").strip()
    )
    model = (
        args.model.strip()
        or os.environ.get("GROK_MODEL", "").strip()
        or str(config.get("model") or "").strip()
        or "grok-2-latest"
    )
    api_type = (
        args.api_type.strip()
        or os.environ.get("GROK_API_TYPE", "").strip()
        or str(config.get("api_type") or "").strip()
        or "auto"
    )
    system_prompt = str(config.get("system_prompt") or "").strip()

    timeout_seconds = args.timeout_seconds
    if not timeout_seconds:
        timeout_seconds = float(os.environ.get("GROK_TIMEOUT_SECONDS", "0") or "0")
    if not timeout_seconds:
        timeout_seconds = float(config.get("timeout_seconds") or 0) or 60.0

    verify_ssl_env = os.environ.get("GROK_VERIFY_SSL", "").strip().lower()
    if verify_ssl_env in ("0", "false", "no"):
        verify_ssl = False
    elif verify_ssl_env in ("1", "true", "yes"):
        verify_ssl = True
    else:
        verify_ssl = config.get("verify_ssl", True) is not False

    max_retries = int(config.get("max_retries") or 1)

    if not base_url:
        sys.stderr.write(
            "Missing base URL: set GROK_BASE_URL, write it to config, or pass --base-url\n"
            f"Config path: {config_path}\n"
        )
        return 2

    if not api_key:
        sys.stderr.write(
            "Missing API key: set GROK_API_KEY, write it to config, or pass --api-key\n"
            f"Config path: {config_path}\n"
        )
        return 2

    try:
        extra_body: dict[str, Any] = {}
        cfg_extra_body = config.get("extra_body")
        if isinstance(cfg_extra_body, dict):
            extra_body.update(cfg_extra_body)
        extra_body.update(_load_json_env("GROK_EXTRA_BODY_JSON"))
        extra_body.update(_parse_json_object(args.extra_body_json, label="--extra-body-json"))

        extra_headers: dict[str, Any] = {}
        cfg_extra_headers = config.get("extra_headers")
        if isinstance(cfg_extra_headers, dict):
            extra_headers.update(cfg_extra_headers)
        extra_headers.update(_load_json_env("GROK_EXTRA_HEADERS_JSON"))
        extra_headers.update(_parse_json_object(args.extra_headers_json, label="--extra-headers-json"))
    except Exception as e:
        sys.stderr.write(f"Invalid JSON: {e}\n")
        return 2

    # ---- 发送请求 ----
    resolved_api_type = _detect_api_type(api_type, model)
    started = time.time()

    try:
        if resolved_api_type == "responses":
            resp = _request_responses(
                base_url=base_url, api_key=api_key, model=model,
                query=args.query, timeout_seconds=timeout_seconds,
                verify_ssl=verify_ssl, max_retries=max_retries,
                extra_headers=extra_headers, extra_body=extra_body,
            )
            extracted = _extract_responses_result(resp, args.query)
        else:
            resp = _request_chat_completions(
                base_url=base_url, api_key=api_key, model=model,
                query=args.query, timeout_seconds=timeout_seconds,
                verify_ssl=verify_ssl, max_retries=max_retries,
                system_prompt=system_prompt,
                extra_headers=extra_headers, extra_body=extra_body,
            )
            extracted = _extract_chat_result(resp, args.query)

    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") and callable(e.read) else ""
        out = {
            "ok": False,
            "error": f"HTTP {getattr(e, 'code', None)}",
            "detail": raw or str(e),
            "config_path": config_path,
            "base_url": base_url,
            "model": model,
            "api_type": resolved_api_type,
            "elapsed_ms": int((time.time() - started) * 1000),
        }
        sys.stdout.write(_compact_json(out))
        return 1
    except Exception as e:
        out = {
            "ok": False,
            "error": "request_failed",
            "detail": str(e),
            "config_path": config_path,
            "base_url": base_url,
            "model": model,
            "api_type": resolved_api_type,
            "elapsed_ms": int((time.time() - started) * 1000),
        }
        sys.stdout.write(_compact_json(out))
        return 1

    # ---- 构建输出 ----
    out: dict[str, Any] = {
        "ok": True,
        "query": args.query,
        "config_path": config_path,
        "base_url": base_url,
        "model": resp.get("model") or model,
        "api_type": resolved_api_type,
        "content": extracted["content"],
        "sources": extracted["sources"],
        "raw": extracted["raw"],
        "usage": extracted["usage"],
        "elapsed_ms": int((time.time() - started) * 1000),
    }
    if extracted.get("reasoning"):
        out["reasoning"] = extracted["reasoning"]

    output = _compact_json(out)
    if hasattr(sys.stdout, 'buffer'):
        sys.stdout.buffer.write(output.encode('utf-8'))
    else:
        sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
