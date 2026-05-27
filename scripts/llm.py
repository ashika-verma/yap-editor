#!/usr/bin/env python3
"""
Unified LLM client for Yap Editor.

Backend selection (checked in order):
  1. LLM_BASE_URL env var → OpenAI-compatible endpoint (LM Studio, Ollama, etc.)
  2. GEMINI_API_KEY env var → Google Gemini

Environment variables:
  LLM_BASE_URL   http://127.0.0.1:1234   OpenAI-compat base URL (enables local mode)
  LLM_MODEL      google/gemma-4-e4b      Model for OpenAI-compat backend
  LLM_API_KEY    lm-studio               API key (can be anything for LM Studio)
  GEMINI_API_KEY <key>                   Gemini API key
"""
from __future__ import annotations
import json
import os

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"


_DEFAULT_LOCAL_URL = "http://127.0.0.1:1234"


def _probe_local(base_url: str) -> bool:
    """Return True if an OpenAI-compatible server is reachable at base_url."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"{base_url}/v1/models", timeout=2) as r:
            data = json.loads(r.read())
            return bool(data.get("data"))
    except Exception:
        return False


def is_local() -> bool:
    explicit = os.environ.get("LLM_BASE_URL", "")
    if explicit:
        return True
    # Auto-probe: if LM Studio (or Ollama) is running at the default port,
    # use it rather than falling back to rule-based heuristics.
    if _probe_local(_DEFAULT_LOCAL_URL):
        os.environ["LLM_BASE_URL"] = _DEFAULT_LOCAL_URL
        return True
    return False


def generate(
    prompt: str,
    schema: dict | None = None,
    model: str | None = None,
    api_key: str | None = None,
    prefer_cloud: bool = False,
    temperature: float | None = None,
) -> str:
    """
    Generate text. Returns raw string (JSON when schema is given).

    prefer_cloud=True forces Gemini even when LLM_BASE_URL is set (used for
    edit/critic calls where we want Gemini quality, not local speed).
    temperature=None uses each backend's default; pass 0 for deterministic runs.

    Raises on failure — callers are responsible for try/except.
    """
    if prefer_cloud and api_key:
        try:
            return _gemini(prompt, schema=schema, model=model, api_key=api_key,
                           temperature=temperature)
        except Exception as e:
            if _is_rate_limited(e) and _probe_local(_DEFAULT_LOCAL_URL):
                import sys
                print(f"[llm] Gemini rate limited — falling back to local", file=sys.stderr)
                return _openai_compat(prompt, schema=schema, model=model,
                                       temperature=temperature)
            raise
    if is_local():
        return _openai_compat(prompt, schema=schema, model=model,
                               temperature=temperature)
    return _gemini(prompt, schema=schema, model=model, api_key=api_key,
                   temperature=temperature)


def generate_vision(
    prompt: str,
    images: list[bytes],
    schema: dict | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> str:
    """
    Generate text with image inputs. Prefers Gemini (better vision quality);
    falls back to local LLM on rate-limit errors.

    Raises on failure — callers are responsible for try/except.
    """
    try:
        return _gemini_vision(prompt, images=images, schema=schema, model=model, api_key=api_key)
    except Exception as e:
        if _is_rate_limited(e) and _probe_local(_DEFAULT_LOCAL_URL):
            import sys
            print(f"[llm] Gemini rate limited — falling back to local vision", file=sys.stderr)
            return _openai_compat_vision(prompt, images=images, schema=schema, model=model)
        raise


def _is_rate_limited(e: Exception) -> bool:
    msg = str(e).lower()
    return any(tok in msg for tok in ("429", "quota", "rate_limit", "resource_exhausted", "exhausted"))


# ── Gemini backend ─────────────────────────────────────────────────────────────

def _gemini(
    prompt: str,
    schema: dict | None = None,
    model: str | None = None,
    api_key: str | None = None,
    temperature: float | None = None,
) -> str:
    from google import genai                  # type: ignore
    from google.genai import types as gtypes  # type: ignore

    key = api_key or os.environ.get("GEMINI_API_KEY", "")
    client = genai.Client(api_key=key)
    cfg_kwargs: dict = {
        "response_mime_type": "application/json",
        "response_schema":    schema if schema else None,
    }
    if temperature is not None:
        cfg_kwargs["temperature"] = temperature
    cfg = gtypes.GenerateContentConfig(**cfg_kwargs)
    resp = client.models.generate_content(
        model=model or DEFAULT_GEMINI_MODEL,
        contents=prompt,
        config=cfg,
    )
    return resp.text


# ── OpenAI-compatible backend (LM Studio, Ollama, etc.) ───────────────────────

def _openai_compat(
    prompt: str,
    schema: dict | None = None,
    model: str | None = None,
    temperature: float | None = None,
) -> str:
    import urllib.request, urllib.error

    base_url = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:1234").rstrip("/")
    # Callers default to Gemini model names (e.g. "gemini-2.5-flash"), which an
    # OpenAI-compatible local server rejects with a 400. In local mode, ignore a
    # Gemini-style name and use the locally configured / detected model instead.
    passed = model if (model and not model.lower().startswith("gemini")) else None
    effective_model = (
        passed
        or os.environ.get("LLM_MODEL")
        or _detect_model(base_url)
    )
    api_key = os.environ.get("LLM_API_KEY", "lm-studio")

    # Build messages. "Think carefully" in system prompt triggers reasoning_content
    # on models that support it (e.g. Gemma 4B via LM Studio). The thinking goes
    # to reasoning_content, so content is already clean JSON — no stripping needed.
    system = (
        "You are a helpful assistant and expert video editor. "
        "Think carefully before responding. "
        "Respond ONLY with valid JSON — no markdown, no explanation outside the JSON."
    )
    if schema:
        system += f"\n\nYour response must match this JSON schema exactly:\n{json.dumps(schema, indent=2)}"

    payload: dict = {
        "model": effective_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": prompt},
        ],
        "temperature": temperature if temperature is not None else 0.3,
    }

    # Use json_schema when a schema is provided (LM Studio / vllm support this).
    # Fall back to plain text mode — schema is already embedded in the system prompt.
    if schema:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "response", "schema": schema},
        }
    else:
        payload["response_format"] = {"type": "text"}

    body = json.dumps(payload).encode()
    req  = urllib.request.Request(
        f"{base_url}/v1/chat/completions",
        data=body,
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {e.code} from {base_url}: {body[:400]}") from e

    msg     = data["choices"][0]["message"]
    content = msg.get("content") or ""

    # Log thinking tokens when present (goes to stderr so it doesn't pollute stdout JSON)
    reasoning = msg.get("reasoning_content", "")
    if reasoning and os.environ.get("LLM_DEBUG"):
        import sys
        print(f"[llm thinking] {reasoning[:300]}{'...' if len(reasoning) > 300 else ''}", file=sys.stderr)

    # Fallback: strip <think>...</think> if a model puts thinking in content instead
    if "<think>" in content:
        content = content.split("</think>", 1)[-1].strip()

    return content


def _detect_model(base_url: str) -> str:
    """Return first loaded model from /v1/models, or a sensible default."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"{base_url}/v1/models", timeout=5) as r:
            models = json.loads(r.read()).get("data", [])
            if models:
                return models[0]["id"]
    except Exception:
        pass
    return "local-model"


# ── Vision backends ────────────────────────────────────────────────────────────

def _gemini_vision(
    prompt: str,
    images: list[bytes],
    schema: dict | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> str:
    import base64
    from google import genai                  # type: ignore
    from google.genai import types as gtypes  # type: ignore

    key = api_key or os.environ.get("GEMINI_API_KEY", "")
    client = genai.Client(api_key=key)

    parts: list[dict] = []
    for img_bytes in images:
        b64 = base64.b64encode(img_bytes).decode()
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64}})
    parts.append({"text": prompt})

    cfg = gtypes.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=schema if schema else None,
    )
    resp = client.models.generate_content(
        model=model or DEFAULT_GEMINI_MODEL,
        contents=[{"role": "user", "parts": parts}],
        config=cfg,
    )
    return resp.text


def _openai_compat_vision(
    prompt: str,
    images: list[bytes],
    schema: dict | None = None,
    model: str | None = None,
) -> str:
    import base64
    import urllib.request, urllib.error

    base_url = os.environ.get("LLM_BASE_URL", _DEFAULT_LOCAL_URL).rstrip("/")
    effective_model = model or os.environ.get("LLM_MODEL") or _detect_model(base_url)
    api_key = os.environ.get("LLM_API_KEY", "lm-studio")

    # Build vision content array: images first, then the text prompt
    content: list[dict] = []
    for img_bytes in images:
        b64 = base64.b64encode(img_bytes).decode()
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        })
    content.append({"type": "text", "text": prompt})

    payload: dict = {
        "model": effective_model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.3,
    }
    if schema:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "response", "schema": schema},
        }

    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{base_url}/v1/chat/completions",
        data=body,
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_str = e.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {e.code} from {base_url}: {body_str[:400]}") from e

    msg = data["choices"][0]["message"]
    text = msg.get("content") or ""
    if "<think>" in text:
        text = text.split("</think>", 1)[-1].strip()
    return text
