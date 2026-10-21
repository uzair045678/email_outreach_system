import pytest

from outreach.llm.client import LLMClient


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("http://127.0.0.1:8081", "http://127.0.0.1:8081/v1/chat/completions"),
        ("https://openrouter.ai/api/v1", "https://openrouter.ai/api/v1/chat/completions"),
        (
            "https://generativelanguage.googleapis.com/v1beta/openai",
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        ),
    ],
)
def test_chat_url_handles_root_and_versioned_base_urls(base_url, expected):
    assert LLMClient(base_url)._chat_url() == expected
