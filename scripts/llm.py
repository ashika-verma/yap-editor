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


def is_local() -> bool:
    return bool(os.environ.get("LLM_BASE_URL", ""))


def generate(
    prompt: str,
    schema: dict | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> str:
    """
    Generate text. Returns raw string (JSON when schema is given).

    Raises on failure — callers are responsible for try/except.
    """
    if is_local():
        return _openai_compat(prompt, schema=schema, model=model)
    return _gemini(prompt, schema=schema, model=model, api_key=api_key)


# ── Gemini backend ─────────────────────────────────────────────────────────────

def _gemini(
    prompt: str,
    schema: dict | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> str:
    from google import genai                  # type: ignore
    from google.genai import types as gtypes  # type: ignore

    key = api_key or os.environ.get("GEMINI_API_KEY", "")
    client = genai.Client(api_key=key)
    cfg = gtypes.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=schema if schema else None,
    )
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
) -> str:
    import urllib.request

    base_url = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:1234").rstrip("/")
    effective_model = (
        model
        or os.environ.get("LLM_MODEL")
        or _detect_model(base_url)
    )
    api_key = os.environ.get("LLM_API_KEY", "lm-studio")

    # Build messages — if a schema is requested, embed it in the system prompt
    # because local models vary in structured-output support.
    system = "You are a helpful assistant. Respond ONLY with valid JSON — no markdown, no explanation."
    if schema:
        system += f"\n\nYour response must match this JSON schema exactly:\n{json.dumps(schema, indent=2)}"

    payload: dict = {
        "model": effective_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": prompt},
        ],
        "temperature": 0.3,
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
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read())

    return data["choices"][0]["message"]["content"]


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
