"""Fallback LLM chain (OpenAI-compatible) used only when local drafting fails.

Providers are tried in the order listed in config.yaml. Every fallback use is
logged so it is never silent.
"""

from __future__ import annotations

from .client import LLMClient


class FallbackChain:
    def __init__(self, cfg, logger=None):
        self.enabled = bool(cfg.raw.fallback_enabled.raw)
        self.logger = logger
        self.providers: list[LLMClient] = []
        if not self.enabled:
            return
        for entry in cfg.raw.fallback:
            name = entry.name.raw
            key_env = entry.api_key_env.raw
            key = cfg.env(key_env)
            if not key:
                if logger:
                    logger.info("fallback provider %s skipped (no %s)", name, key_env)
                continue
            self.providers.append(
                LLMClient(
                    base_url=entry.base_url.raw,
                    api_key=key,
                    model=entry.model.raw,
                    timeout_s=int(cfg.raw.models.request_timeout_s.raw),
                    temperature=float(cfg.raw.models.temperature.raw),
                    max_tokens=int(cfg.raw.models.max_tokens.raw),
                    logger=logger,
                )
            )

    @property
    def available(self) -> bool:
        return bool(self.providers)

    def chat_json(self, messages: list[dict], schema: dict | None = None) -> dict:
        last_exc: Exception | None = None
        for client in self.providers:
            try:
                if self.logger:
                    self.logger.warning("using fallback LLM: %s", client.base_url)
                return client.chat_json(messages, schema=schema)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if self.logger:
                    self.logger.error("fallback %s failed: %s", client.base_url, exc)
        raise RuntimeError(f"all fallback providers failed: {last_exc}")
