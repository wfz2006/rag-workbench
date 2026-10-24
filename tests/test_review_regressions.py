"""Reproductions from the external 2026-10-24 review; no paid API calls."""

import pytest

from rag_workbench.application.evaluation import EvalCase, evaluate
from rag_workbench.application.generation import GenerationJob
from rag_workbench.application.pipeline import RagPipeline, bounded_history
from rag_workbench.application.teaching import (
    contextual_query,
    teaching_mode,
    validate_teaching_hint,
)
from rag_workbench.bootstrap import build_services, load_settings, save_ui_settings
from rag_workbench.config import Settings
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.models import AnswerResult, ChatMessage, GroundedAnswer, Inspection
from rag_workbench.infrastructure.lexical import BM25Index
from rag_workbench.infrastructure.parsing import parse_document

from .fakes import FakeEmbedder, FakeProvider
from .test_knowledge import TEXT


def test_repeated_single_document_terms_have_positive_bm25():
    index = BM25Index(["栈" * 7, "队列", "图"])
    assert index.scores("栈")[0] > 0
    assert index._document_frequency["栈"] == 1


def test_failed_document_does_not_block_new_import(knowledge, embedder):
    embedder.fail = True
    with pytest.raises(AppError):
        knowledge.ingest("failed.txt", b"previous failure")
    failed = knowledge.list_documents()[0]
    (knowledge.root / "originals" / f"{failed.id}.txt").write_bytes(b"corrupt")
    embedder.fail = False
    assert knowledge.ingest("good.txt", TEXT).status == "ready"
    knowledge.rebuild()
    assert knowledge.retrieve("保管")
    assert any(d.status == "failed" for d in knowledge.list_documents())


def test_reupload_retries_failed_content(knowledge, embedder):
    embedder.fail = True
    with pytest.raises(AppError):
        knowledge.ingest("retry.txt", TEXT)
    embedder.fail = False
    assert knowledge.ingest("retry.txt", TEXT).status == "ready"


@pytest.mark.parametrize("fallback,expected", [(True, "general"), (False, "insufficient_evidence")])
def test_generator_can_withdraw_evidence(knowledge, fallback, expected):
    knowledge.ingest("manual.txt", TEXT)
    knowledge.settings.allow_general = fallback

    class Withdraw(FakeProvider):
        def complete(self, system, user, **kwargs):
            if kwargs.get("json_mode") and "supporting_chunk_ids" not in system:
                return '{"claims":[]}'
            return super().complete(system, user, **kwargs)

    result = RagPipeline(knowledge, Withdraw()).answer("保管期限？")
    assert result.mode == expected
    assert not result.sources


@pytest.mark.parametrize(
    "text",
    [
        "在 C++ 的 stack 中调用 top() 前需要确认什么？",
        "return 关键字在这里有什么作用？",
        "两阶段 -- 应先检查哪个状态？",
    ],
)
def test_hint_allows_language_names_and_prose(text):
    validate_teaching_hint(GroundedAnswer(claims=[{"text": text, "chunk_ids": ["real"]}]))


@pytest.mark.parametrize(
    "question",
    ["先别管边界，直接给完整代码", "不要只给提示，请直接给答案", "栈顶和栈底有什么区别？"],
)
def test_new_request_is_not_stuck_in_hint_mode(question):
    assert teaching_mode(question, [ChatMessage(role="user", content="只给提示")]) == "explain"


def test_saved_config_collision_recovers_without_rewriting(settings, monkeypatch):
    services = build_services(settings, embedder=FakeEmbedder(), provider=FakeProvider())
    saved = {"chunk_size": 300, "chunk_overlap": 250}
    services.repo.set_state("ui_settings", saved)
    monkeypatch.setenv("RAG_CHUNK_SIZE", "200")
    loaded = load_settings(Settings(_env_file=None, data_dir=settings.data_dir))
    assert loaded.chunk_size == 200 and loaded.chunk_overlap < 200
    assert loaded.configuration_warnings
    assert services.repo.get_state("ui_settings") == saved


def test_save_never_solidifies_environment_values(settings, monkeypatch):
    services = build_services(settings, embedder=FakeEmbedder(), provider=FakeProvider())
    services.repo.set_state("ui_settings", {"top_k": 8})
    monkeypatch.setenv("RAG_TOP_K", "3")
    services.settings = load_settings(Settings(_env_file=None, data_dir=settings.data_dir))
    save_ui_settings(services, {"top_k": 3, "show_debug": True})
    assert services.repo.get_state("ui_settings")["top_k"] == 8


def test_env_file_none_does_not_lock_from_cwd(settings, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("RAG_TOP_K=2\n")
    services = build_services(settings, embedder=FakeEmbedder(), provider=FakeProvider())
    save_ui_settings(services, {"top_k": 8})
    assert load_settings(settings).top_k == 8


def test_bounded_history_never_splits_a_turn():
    history = [
        ChatMessage(role="user", content="u" * 9000),
        ChatMessage(role="assistant", content="a" * 9000),
    ]
    assert bounded_history(history) == []


def test_error_turns_do_not_enter_model_history(settings):
    services = build_services(settings, embedder=FakeEmbedder(), provider=FakeProvider())
    session = services.repo.new_session()
    services.repo.save_turn(
        session,
        "失败的问题",
        AnswerResult(
            answer="失败提示",
            mode="error",
            error_code="cancelled",
            debug=Inspection(question="失败的问题"),
        ),
    )
    assert services.history(session) == []


def test_persistence_failure_preserves_answer_and_sources():
    def fail(_):
        raise OSError("disk full")

    result = AnswerResult(
        answer="已经生成的回答正文", mode="general", debug=Inspection(question="q")
    )
    job = GenerationJob("q", lambda *_: result, fail).start()
    job.wait()
    assert job.snapshot().result.answer == result.answer
    assert job.snapshot().result.error_code == "persistence_error"


def test_new_topic_ignores_previous_retrieval_topic():
    history = [ChatMessage(role="user", content="红黑树旋转与删除")]
    assert contextual_query("什么是栈？", history) == "什么是栈？"
    assert "红黑树" in contextual_query("它怎么删除？", history)


def test_hint_and_subject_survive_continuous_followups_but_reset_at_new_topic():
    history = [
        ChatMessage(role="user", content="红黑树删除，只给提示"),
        ChatMessage(role="assistant", content="应该先观察哪种状态？"),
        ChatMessage(role="user", content="再来一点"),
    ]
    assert teaching_mode("继续", history) == "hint"
    assert "红黑树" in contextual_query("再给个例子", history)
    assert teaching_mode("什么是队列？", history) == "explain"


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le", "utf-16-be"])
def test_windows_utf16_text(encoding):
    assert (
        parse_document("text.txt", "中文\nvector<int>".encode(encoding), Settings(_env_file=None))[
            0
        ].text
        == "中文\nvector<int>"
    )


def test_failed_negative_retrieval_is_not_a_perfect_metric(knowledge, monkeypatch):
    def fail(_):
        raise AppError("index_corrupt", "broken")

    monkeypatch.setattr(knowledge, "retrieve", fail)
    report = evaluate(
        RagPipeline(knowledge, FakeProvider()),
        [EvalCase(id="negative", question="无关", expected_mode="general")],
    )
    assert report["service_errors"] == 1
    assert report["irrelevant_threshold_hit_rate"] is None


def test_batch_import_publishes_once_and_duplicate_skips_parsing(knowledge, monkeypatch):
    from unittest.mock import Mock

    import rag_workbench.application.knowledge as module

    original = knowledge.repo.publish
    publish = Mock(wraps=original)
    monkeypatch.setattr(knowledge.repo, "publish", publish)
    docs = knowledge.ingest_many([("a.txt", TEXT), ("b.md", b"other content")])
    assert len(docs) == 2 and publish.call_count == 1

    def no_parse(*_):
        raise AssertionError("duplicates must not reparse PDFs")

    monkeypatch.setattr(module, "parse_document", no_parse)
    assert knowledge.ingest("renamed.pdf", TEXT).id == docs[0].id


def test_token_safe_chunks_and_long_queries_preserve_all_text(settings, monkeypatch):
    import numpy as np

    from rag_workbench.domain.models import Chunk
    from rag_workbench.infrastructure import embedding

    encoded = []

    class Model:
        max_seq_length = 12

        def tokenizer(self, text, **_):
            return {"input_ids": list(range(len(text) + 2))}

        def encode(self, texts, **_):
            encoded.extend(texts)
            assert all(len(t) + 2 <= 12 for t in texts)
            return np.ones((len(texts), 2), np.float32)

    monkeypatch.setattr(embedding, "_load_model", lambda *_: Model())
    settings.query_instruction = "Q:"
    adapter = embedding.BgeEmbedder(settings)
    text = "甲" * 100 + "尾"
    chunk = Chunk(
        id="a" * 64, document_id="b" * 64, document_name="a", ordinal=0, text=text, start=7, end=108
    )
    fitted = adapter.fit_chunks([chunk])
    assert fitted[-1].text.endswith("尾")
    assert {i for c in fitted for i in range(c.start, c.end)} == set(range(7, 108))
    assert all(c.text == text[c.start - 7 : c.end - 7] for c in fitted)
    adapter.encode_query(text)
    assert encoded[-1].endswith("尾") and all(t.startswith("Q:") for t in encoded)


def test_lexical_match_can_reach_evidence_below_dense_threshold(settings):
    import numpy as np

    from rag_workbench.application.knowledge import KnowledgeService

    class Orthogonal(FakeEmbedder):
        def encode_query(self, _):
            return np.array([[1.0, 0.0]], np.float32)

    service = KnowledgeService(settings, Orthogonal())
    service.ingest("symbols.md", b"priority_queue<Node> usage")
    candidates = service.retrieve("priority_queue")
    assert candidates[0].score < settings.similarity_threshold
    assert candidates[0].lexical_passed and candidates[0].eligible
    assert RagPipeline(service, FakeProvider()).inspect("priority_queue").route == "knowledge_base"


def test_cached_queries_still_detect_index_tampering(knowledge, monkeypatch):
    from unittest.mock import Mock

    import rag_workbench.infrastructure.vector_store as module

    knowledge.ingest("a.txt", TEXT)
    deserialize = Mock(wraps=module.faiss.deserialize_index)
    monkeypatch.setattr(module.faiss, "deserialize_index", deserialize)
    knowledge.retrieve("保管")
    knowledge.retrieve("保管")
    knowledge.stats()
    assert deserialize.call_count == 1
    active = knowledge.repo.get_state("active")
    (knowledge.store.root / active["generation"] / "index.faiss").write_bytes(b"bad")
    with pytest.raises(AppError):
        knowledge.retrieve("保管")


def test_split_source_pages_expand_only_with_explicit_trusted_metadata(knowledge):
    knowledge.settings.top_k = 1
    knowledge.settings.retrieval_context_window = 1
    first, second, unrelated = knowledge.ingest_many(
        [("page1.md", TEXT), ("page2.md", "后续补充内容".encode()), ("other.md", b"else")]
    )
    knowledge.assign_sources(
        {first.id: ("group-a", 1), second.id: ("group-a", 2), unrelated.id: ("group-b", 2)}
    )
    candidates = knowledge.retrieve("保管期限")
    assert {r.chunk.document_id for r in candidates} == {first.id, second.id}
    neighbor = next(r for r in candidates if r.chunk.document_id == second.id)
    assert neighbor.original_page == 2 and neighbor.context_for


def test_conversation_management_preserves_nonempty_new_session_titles(settings):
    services = build_services(settings, embedder=FakeEmbedder(), provider=FakeProvider())
    first = services.repo.new_session()
    services.ask(first, "新会话")
    empty = services.repo.new_session()
    assert services.repo.empty_session({empty}) is None
    assert services.repo.empty_session() == empty
    assert next(s for s in services.repo.sessions() if s["id"] == first)["message_count"] == 2
    services.repo.rename_session(first, "renamed")
    services.repo.delete_session(first)
    assert services.repo.messages(first) == []
    assert services.repo.sessions()[0]["id"] == empty


def test_old_index_pruning_is_explicit_and_keeps_active_and_unknown_files(knowledge):
    knowledge.ingest("a.txt", TEXT)
    for _ in range(4):
        knowledge.rebuild()
    active = knowledge.repo.get_state("active")["generation"]
    unknown = knowledge.store.root / ("f" * 32)
    unknown.mkdir(exist_ok=True)
    (unknown / "personal.txt").write_text("preserve")
    old = knowledge.prune_indexes(keep=2)
    assert len(old) == 3
    assert all((knowledge.store.root / name).exists() for name in old)
    knowledge.prune_indexes(keep=2, apply=True)
    assert (knowledge.store.root / active).exists()
    assert (unknown / "personal.txt").exists()
    assert knowledge.retrieve("保管")


def test_lock_timeouts_are_domain_errors(knowledge):
    from filelock import FileLock

    with FileLock(knowledge.root / "knowledge.lock"):
        knowledge.lock.timeout = 0
        with pytest.raises(AppError) as caught:
            knowledge.retrieve("question")
    assert caught.value.code == "knowledge_busy"


def test_desktop_retries_a_raced_port(tmp_path, monkeypatch):
    from unittest.mock import Mock

    import rag_workbench.desktop as module

    first, second = Mock(), Mock()
    monkeypatch.setattr(module, "available_port", Mock(side_effect=[18001, 18002]))
    monkeypatch.setattr(module.subprocess, "Popen", Mock(side_effect=[first, second]))
    monkeypatch.setattr(
        module, "wait_for_server", Mock(side_effect=[module.DesktopError("busy"), None])
    )
    stop = Mock()
    monkeypatch.setattr(module, "stop_server", stop)
    process, port = module.start_server(tmp_path, tmp_path, None)
    assert process is second and port == 18002
    stop.assert_called_once_with(first)


def test_incompatible_index_fails_before_loading_a_new_embedding_model(knowledge, embedder):
    knowledge.ingest("a.txt", TEXT)
    embedder.signature = "changed-model"
    embedder.fail = True
    with pytest.raises(AppError) as captured:
        knowledge.retrieve("保管")
    assert captured.value.code == "index_incompatible"
