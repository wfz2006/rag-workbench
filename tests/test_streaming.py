import json
from threading import Event

import httpx2 as httpx
import pytest
from openai import OpenAI

from rag_workbench.application.pipeline import RagPipeline
from rag_workbench.domain.errors import AppError
from rag_workbench.infrastructure.llm import OpenAIChatProvider

from .fakes import FakeProvider
from .test_knowledge import TEXT


def sse_chunk(text, finish=None):
    return (
        "data: "
        + json.dumps(
            {
                "id": "qa",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "qa",
                "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": finish}],
            }
        )
        + "\n\n"
    )


@pytest.mark.parametrize("finish", ["stop", "length", None])
def test_sdk_stream_checks_finish_and_sends_stream_flag(settings, finish):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        body = sse_chunk("第一段") + sse_chunk("第二段", finish) + "data: [DONE]\n\n"
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    with OpenAI(
        api_key="qa",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    ) as client:
        provider = OpenAIChatProvider(settings, client)
        if finish == "stop":
            assert list(provider.stream("system", "question")) == ["第一段", "第二段"]
        else:
            with pytest.raises(AppError) as error:
                list(provider.stream("system", "question"))
            assert error.value.code == "incomplete_response"
    assert requests[0]["stream"] is True
    assert requests[0]["thinking"] == {"type": "disabled"}


@pytest.mark.parametrize(
    "status,code", [(401, "authentication"), (402, "balance"), (429, "rate_limit")]
)
def test_stream_http_errors_are_sanitized(settings, status, code):
    with (
        OpenAI(
            api_key="qa",
            max_retries=0,
            http_client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        status, json={"error": {"message": "private sk-secret"}}
                    )
                )
            ),
        ) as client,
        pytest.raises(AppError) as error,
    ):
        list(OpenAIChatProvider(settings, client).stream("system", "question"))
    assert error.value.code == code
    assert "secret" not in str(error.value)


class StreamingProvider(FakeProvider):
    def stream(self, system, user, **kwargs):
        yield "第一段。"
        yield "第二段。"


def test_pipeline_general_emits_real_deltas_and_final_answer(knowledge):
    events = []
    result = RagPipeline(knowledge, StreamingProvider()).answer("你好", on_event=events.append)
    deltas = [event.text for event in events if event.kind == "delta"]
    assert deltas == ["通用模式 · 本回答不是来自知识库。\n\n", "第一段。", "第二段。"]
    assert result.answer == "".join(deltas)
    assert result.mode == "general"


def test_grounded_invalid_future_citation_never_emits_unvalidated_text(knowledge):
    knowledge.ingest("manual.txt", TEXT)

    class Invalid(StreamingProvider):
        def stream(self, system, user, **kwargs):
            if "supporting_chunk_ids" in system:
                yield self.complete(system, user, json_mode=True)
                return
            identity = json.loads(user)["evidence"][0]["chunk_id"]
            yield json.dumps(
                {
                    "claims": [
                        {"text": "前半段", "chunk_ids": [identity]},
                        {"text": "未经验证", "chunk_ids": ["invented"]},
                    ]
                }
            )

    events = []
    result = RagPipeline(knowledge, Invalid()).answer("保管期限", on_event=events.append)
    assert result.error_code == "invalid_citation"
    assert not any(event.kind == "delta" for event in events)


def test_cancel_between_network_deltas_closes_stream(knowledge):
    from rag_workbench.application.generation import CancellationToken

    token = CancellationToken()
    closed = Event()

    class Cancellable(StreamingProvider):
        def stream(self, system, user, **kwargs):
            try:
                yield "部分内容"
                token.cancel()
                yield "不应继续显示"
            finally:
                closed.set()

    events = []
    result = RagPipeline(knowledge, Cancellable()).answer(
        "你好", on_event=events.append, cancellation=token
    )
    assert result.error_code == "cancelled"
    assert closed.is_set()
    assert not any("不应继续" in event.text for event in events)


def test_background_job_cancel_is_persisted_once_and_drops_partial(knowledge, settings):
    from rag_workbench.bootstrap import build_services

    gate, entered = Event(), Event()

    class Slow(StreamingProvider):
        def stream(self, system, user, **kwargs):
            yield "不完整的正文"
            entered.set()
            assert gate.wait(3)
            yield "后续内容"

    services = build_services(settings, embedder=knowledge.embedder, provider=Slow())
    session = services.repo.new_session()
    job = services.start_answer(session, "你好")
    assert entered.wait(3)
    assert "不完整的正文" in job.snapshot().text
    assert not services.repo.messages(session)
    assert job.cancel()
    gate.set()
    job.wait()
    assert job.snapshot().done
    assert job.snapshot().result.error_code == "cancelled"
    messages = services.repo.messages(session)
    assert len(messages) == 2
    assert "不完整的正文" not in messages[-1]["content"]
    assert not job.cancel()  # A late stop must not alter the committed turn.
    assert len(services.repo.messages(session)) == 2


def test_saving_failure_reports_error_without_claiming_success():
    from rag_workbench.application.generation import GenerationJob
    from rag_workbench.domain.models import AnswerResult, Inspection

    result = AnswerResult(answer="完整内容", mode="general", debug=Inspection(question="问题"))

    def failed_save(result):
        raise OSError("private detail")

    job = GenerationJob("问题", lambda *args: result, failed_save).start()
    job.wait()
    saved = job.snapshot().result
    assert saved.mode == "general"
    assert saved.error_code == "persistence_error"
    assert saved.debug.error_code is None  # Persistence is separate from generation diagnostics.
    assert saved.persistence_error
    assert saved.debug.route == "general"
    assert saved.answer == "完整内容"
    assert "private" not in saved.answer


def test_successful_background_turn_is_committed_once(settings):
    from rag_workbench.bootstrap import build_services

    from .fakes import FakeEmbedder

    services = build_services(settings, embedder=FakeEmbedder(), provider=StreamingProvider())
    session = services.repo.new_session()
    job = services.start_answer(session, "问题")
    job.wait()
    assert job.snapshot().done
    assert job.snapshot().result.mode == "general"
    assert services.repo.messages(session)[-1]["content"].endswith("第一段。第二段。")
    assert len(services.repo.messages(session)) == 2


@pytest.mark.parametrize(
    "failure,code", [(httpx.ReadTimeout, "timeout"), (httpx.ReadError, "connection")]
)
def test_real_sdk_maps_midstream_network_failure(settings, failure, code):
    class Interrupted(httpx.SyncByteStream):
        def __iter__(self):
            yield sse_chunk("已收到一部分").encode()
            raise failure("private network detail")

    with (
        OpenAI(
            api_key="qa",
            max_retries=0,
            http_client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200, stream=Interrupted(), headers={"content-type": "text/event-stream"}
                    )
                )
            ),
        ) as client,
        pytest.raises(AppError) as error,
    ):
        list(OpenAIChatProvider(settings, client).stream("system", "question"))
    assert error.value.code == code
    assert "private" not in str(error.value)


def test_sdk_json_stream_and_empty_output(settings):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            text=sse_chunk(" ", "stop") + "data: [DONE]\n\n",
            headers={"content-type": "text/event-stream"},
        )

    with (
        OpenAI(
            api_key="qa",
            max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        ) as client,
        pytest.raises(AppError) as error,
    ):
        list(OpenAIChatProvider(settings, client).stream("return json", "question", json_mode=True))
    assert error.value.code == "empty_response"
    assert requests[0]["response_format"] == {"type": "json_object"}


def test_valid_grounded_stream_is_validated_before_any_body_event(knowledge):
    knowledge.ingest("manual.txt", TEXT)
    events = []
    result = RagPipeline(knowledge, FakeProvider()).answer("保管期限", on_event=events.append)
    assert result.mode == "knowledge_base"
    assert result.sources[0].chunk.id in result.answer
    assert [event.phase for event in events] == [
        "retrieving",
        "evidence",
        "generating_grounded",
        "validating",
    ]
