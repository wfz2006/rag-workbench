import json

import httpx2 as httpx
import numpy as np
import pytest
from openai import OpenAI

from rag_workbench.config import Settings
from rag_workbench.domain.errors import AppError
from rag_workbench.infrastructure.embedding import BgeEmbedder
from rag_workbench.infrastructure.llm import OpenAIChatProvider


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "authentication"),
        (402, "balance"),
        (403, "permission"),
        (429, "rate_limit"),
        (500, "provider_error"),
        (422, "provider_error"),
    ],
)
def test_http_errors_sanitized(status, code):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            status,
            json={"error": {"message": "private sk-secret body"}},
            request=request,
        )
    )
    client = OpenAI(
        api_key="sk-test",
        base_url="https://example.test",
        max_retries=0,
        http_client=httpx.Client(transport=transport),
    )
    with pytest.raises(AppError) as captured:
        OpenAIChatProvider(Settings(_env_file=None), client).complete("hello", "world")
    assert captured.value.code == code
    assert "secret" not in str(captured.value)
    client.close()


@pytest.mark.parametrize(
    "error,code", [(httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "connection")]
)
def test_transport_failure(error, code):
    def handler(request):
        raise error("secret detail", request=request)

    client = OpenAI(
        api_key="sk-test",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(AppError) as captured:
        OpenAIChatProvider(Settings(_env_file=None), client).complete("hi", "question")
    assert captured.value.code == code
    assert "secret" not in str(captured.value)
    client.close()


def test_missing_key_is_configuration_error(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(AppError) as captured:
        OpenAIChatProvider(Settings(_env_file=None)).complete("hi", "question")
    assert captured.value.code == "missing_api_key"


def test_json_request_and_truncation_use_real_sdk():
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 0,
                "model": "test",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "length",
                        "message": {"role": "assistant", "content": "{"},
                    }
                ],
            },
        )

    client = OpenAI(
        api_key="sk-test",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=0,
    )
    with pytest.raises(AppError) as captured:
        OpenAIChatProvider(Settings(_env_file=None), client).complete(
            "return json", "hi", json_mode=True
        )
    assert captured.value.code == "incomplete_response"
    assert requests[0]["response_format"] == {"type": "json_object"}
    assert requests[0]["thinking"] == {"type": "disabled"}
    client.close()


def test_bge_query_instruction_only_on_query_and_long_input_rejected(monkeypatch):
    from rag_workbench.infrastructure import embedding

    inputs = []

    class Model:
        max_seq_length = 40

        def tokenizer(self, text, **kwargs):
            return {"input_ids": list(range(len(text) + 2))}

        def encode(self, texts, **kwargs):
            inputs.extend(texts)
            return np.ones((len(texts), 2), np.float32)

    monkeypatch.setattr(embedding, "_load_model", lambda *args: Model())
    adapter = BgeEmbedder(Settings(_env_file=None))
    adapter.encode_documents(["原文"])
    adapter.encode_query("问题")
    assert inputs == ["原文", "为这个句子生成表示以用于检索相关文章：问题"]
    with pytest.raises(AppError, match="静默截断"):
        adapter.encode_documents(["长" * 100])
