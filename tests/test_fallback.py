from types import SimpleNamespace

from outreach.config import NS
from outreach.llm.fallback import FallbackChain


def test_fallback_chain_reads_list_entries_as_dicts():
    raw = SimpleNamespace(
        fallback_enabled=SimpleNamespace(raw=True),
        fallback=NS([
            {
                "name": "example",
                "base_url": "https://example.invalid/v1",
                "model": "example-model",
                "api_key_env": "EXAMPLE_API_KEY",
            }
        ]),
        models=SimpleNamespace(
            request_timeout_s=SimpleNamespace(raw=30),
            temperature=SimpleNamespace(raw=0.5),
            max_tokens=SimpleNamespace(raw=100),
        ),
    )
    cfg = SimpleNamespace(raw=raw, env=lambda key: "secret" if key == "EXAMPLE_API_KEY" else "")

    chain = FallbackChain(cfg)

    assert len(chain.providers) == 1
    assert chain.providers[0].base_url == "https://example.invalid/v1"
