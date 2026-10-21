"""OpenAI-compatible chat client for llama-server and fallback APIs."""

from __future__ import annotations

import json
import re
from typing import Any

import requests

from .. import util

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _clean_json(text: str) -> str:
    text = text.strip()
    text = _FENCE_RE.sub("", text).strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
    return text


class LLMClient:
    """Thin wrapper over ``/v1/chat/completions``."""

    def __init__(self, base_url: str, timeout_s: int = 240, temperature: float = 0.5,
                 max_tokens: int = 700, api_key: str = "", model: str = "local", logger=None):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.api_key = api_key
        self.model = model
        self.logger = logger

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _chat_url(self) -> str:
        if self.base_url.endswith(("/v1", "/openai")):
            return f"{self.base_url}/chat/completions"
        return f"{self.base_url}/v1/chat/completions"

    def chat(self, messages: list[dict], schema: dict | None = None,
             temperature: float | None = None, max_tokens: int | None = None) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
            "stream": False,
        }
        if schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "out", "strict": True, "schema": schema},
            }
        resp = requests.post(
            self._chat_url(),
            headers=self._headers(),
            json=payload,
            timeout=self.timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"].get("content") or ""
        return content

    def chat_json(self, messages: list[dict], schema: dict | None = None,
                  temperature: float | None = None, max_tokens: int | None = None) -> dict:
        raw = self.chat(messages, schema=schema, temperature=temperature, max_tokens=max_tokens)
        cleaned = _clean_json(raw)
        return json.loads(cleaned)


def local_client(cfg, server, logger=None) -> LLMClient:
    models = cfg.raw.models
    return LLMClient(
        base_url=server.base_url,
        timeout_s=int(models.request_timeout_s.raw),
        temperature=float(models.temperature.raw),
        max_tokens=int(models.max_tokens.raw),
        model=server.name,
        logger=logger,
    )


def add_no_think(messages: list[dict]) -> list[dict]:
    """Append /no_think so Qwen does not leak reasoning text."""
    out = [dict(m) for m in messages]
    for m in out:
        if m.get("role") == "system":
            m["content"] = m["content"] + "\n/no_think"
            return out
    out.insert(0, {"role": "system", "content": "/no_think"})
    return out
