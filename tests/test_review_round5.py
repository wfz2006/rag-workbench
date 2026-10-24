"""Regression contracts for the fifth external review."""

from unittest.mock import Mock

import pytest
from markdown_it import MarkdownIt

from rag_workbench.bootstrap import build_services
from rag_workbench.config import Settings
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.models import AnswerResult, Chunk, Inspection, RetrievalResult
from rag_workbench.ui.components import answer_presentation

from .fakes import FakeEmbedder, FakeProvider
from .test_knowledge import TEXT


def grounded(text, *, second="下一段正常正文。"):
    sources = [
        RetrievalResult(
            chunk=Chunk(
                id=str(i) * 64,
                document_id="d",
                document_name="d.txt",
                text="资料",
                ordinal=i,
                start=0,
                end=2,
            ),
            score=1,
            passed_threshold=True,
        )
        for i in (1, 2)
    ]
    return AnswerResult(
        answer=f"{text}\n\n[{'2' * 64}]\n\n{second}\n\n[{'1' * 64}]",
        mode="knowledge_base",
        sources=sources,
        debug=Inspection(question="q"),
    )


@pytest.mark.parametrize(
    "block",
    [
        "```cpp\nvector<int> xs;\n```",
        "~~~~python\nx=1\n~~~~",
        "| 字段 | 值 |\n| --- | --- |\n| a | b |",
        "---",
        "    indented_code()",
    ],
)
def test_citations_preserve_markdown_block_boundaries(block):
    result = grounded(block)
    display, plain = answer_presentation(result)
    parser = MarkdownIt("commonmark").enable("table")
    html = parser.render(display)
    assert "<p>下一段正常正文。" in html
    assert "〔来源" not in "".join(
        t.content for t in parser.parse(display) if t.type in {"fence", "code_block"}
    )
    assert plain == block + "\n\n下一段正常正文。"
    if block.startswith("|"):
        assert "<td>b</td>" in html and "<td>b 〔" not in html
    if block == "---":
        assert "<hr" in html


def test_source_numbers_follow_first_citation_order():
    display, _ = answer_presentation(grounded("第一段。"))
    assert display.index("〔来源 1〕") < display.index("〔来源 2〕")


def test_removing_only_failure_resets_automatic_title(knowledge):
    repo = knowledge.repo
    session = repo.new_session()
    repo.save_turn(
        session,
        "旧失败标题",
        AnswerResult(answer="error", mode="error", debug=Inspection(question="q")),
    )
    repo.delete_failed_turn(session, repo.messages(session)[1]["id"])
    assert repo.sessions()[0]["title"] == "新会话"
    repo.save_turn(
        session,
        "真正的问题",
        AnswerResult(answer="ok", mode="general", debug=Inspection(question="q")),
    )
    assert repo.sessions()[0]["title"] == "真正的问题"


def test_custom_empty_session_is_not_reused(knowledge):
    repo = knowledge.repo
    session = repo.new_session()
    repo.rename_session(session, "我命名的空会话")
    assert repo.empty_session() is None


def test_retry_replaces_failed_pair_in_place_and_preserves_later_turn(settings):
    services = build_services(settings, embedder=FakeEmbedder(), provider=FakeProvider())
    sid = services.repo.new_session()
    services.repo.save_turn(
        sid, "失败", AnswerResult(answer="error", mode="error", debug=Inspection(question="q"))
    )
    services.repo.save_turn(
        sid, "后来", AnswerResult(answer="later", mode="general", debug=Inspection(question="q"))
    )
    before = services.repo.messages(sid)
    job = services.start_answer(sid, "修改后的问题", retry_assistant_id=before[1]["id"])
    job.wait()
    after = services.repo.messages(sid)
    assert len(after) == 4 and [m["id"] for m in after] == [m["id"] for m in before]
    assert after[0]["content"] == "修改后的问题" and after[2:] == before[2:]
    assert services.repo.sessions()[0]["title"] == "修改后的问题"
    assert not services.pipeline.provider.calls[-1][2]


@pytest.mark.parametrize(
    "field", ["teaching_profile_path", "domain_profile_path", "retrieval_expansions_path"]
)
@pytest.mark.parametrize("value", ["", "  ", "disabled"])
def test_optional_paths_from_dotenv_are_consistently_disabled(tmp_path, field, value):
    path = tmp_path / ".env"
    path.write_text(f'RAG_{field.upper()}="{value}"\n')
    assert getattr(Settings(_env_file=path), field) is None


def test_restore_does_not_need_other_originals_and_preserves_metadata(knowledge, embedder):
    first, second = knowledge.ingest_many([("a.txt", TEXT), ("b.txt", TEXT + b" second")])
    knowledge.assign_sources({second.id: ("book", 42)})
    second = next(d for d in knowledge.list_documents() if d.id == second.id)
    knowledge.delete(second.id)
    knowledge._raw_path(first).unlink()
    restored = knowledge.restore(second.id)
    assert restored.created_at == second.created_at
    assert (restored.source_group, restored.source_page) == ("book", 42)
    assert {c.document_id for c in knowledge.repo.chunks()} == {first.id, second.id}


def test_restore_publish_failure_leaves_no_staged_catalog_or_original(knowledge, monkeypatch):
    document = knowledge.ingest("a.txt", TEXT)
    knowledge.delete(document.id)
    before = knowledge.repo.get_state("active")
    monkeypatch.setattr(knowledge.repo, "publish", Mock(side_effect=OSError("disk full")))
    with pytest.raises(AppError):
        knowledge.restore(document.id)
    assert not knowledge.repo.documents() and not knowledge._raw_path(document).exists()
    assert knowledge.repo.get_state("active") == before
    assert (knowledge.root / "trash" / f"{document.id}.txt").exists()


def test_expansion_does_not_dilute_original_keyword_admission(knowledge):
    document = knowledge.ingest("a.txt", b"alpha target")
    knowledge.expansions = {"aliases": {"alpha": [f"alias{i}" for i in range(20)]}}
    knowledge.settings.similarity_threshold = 1.0
    result = next(
        r for r in knowledge.retrieve("alpha target") if r.chunk.document_id == document.id
    )
    assert result.lexical_passed


def test_alias_replacement_can_meet_coverage_without_all_aliases(knowledge):
    document = knowledge.ingest("a.txt", b"nickname target")
    knowledge.expansions = {"aliases": {"alpha": ["nickname", "another", "third"]}}
    result = next(
        r for r in knowledge.retrieve("alpha target") if r.chunk.document_id == document.id
    )
    assert result.lexical_passed


def test_context_budget_marks_existing_neighbor_even_when_primary_pool_full(knowledge, monkeypatch):
    knowledge.settings.top_k = 20
    knowledge.settings.retrieval_context_window = 1
    knowledge.ingest("long.txt", TEXT * 300)
    chunks = knowledge.repo.chunks()
    assert len(chunks) > 20
    monkeypatch.setattr(
        knowledge.store,
        "search_loaded",
        lambda *_: [(chunk, 1.0 if i == 0 else 0.0) for i, chunk in enumerate(chunks)],
    )
    from rag_workbench.infrastructure.lexical import BM25Index

    monkeypatch.setattr(BM25Index, "scores", lambda *_: [0.0] * len(chunks))
    results = knowledge.retrieve("保管")
    assert results[1].chunk.id == chunks[1].id and results[1].context_for == chunks[0].id
    assert len(results) <= 40


def test_feedback_reconciles_against_actual_index_without_mutating_error():
    from rag_workbench.ui.pages import current_feedback

    old = {"message": "need rebuild", "requires_reindex": True, "warning": True, "error": False}
    assert not current_feedback(old, "ready")["requires_reindex"]
    assert current_feedback(old, "incompatible") == old
    error = {"message": "invalid", "requires_reindex": False, "warning": False, "error": True}
    assert current_feedback(error, "ready") == error


def test_failed_retry_write_preserves_original_pair(settings, monkeypatch):
    services = build_services(settings, embedder=FakeEmbedder(), provider=FakeProvider())
    sid = services.repo.new_session()
    services.repo.save_turn(
        sid, "first", AnswerResult(answer="error", mode="error", debug=Inspection(question="q"))
    )
    before = services.repo.messages(sid)
    monkeypatch.setattr(
        services.repo, "replace_failed_turn", Mock(side_effect=OSError("disk full"))
    )
    job = services.start_answer(sid, "retry", retry_assistant_id=before[1]["id"])
    job.wait()
    assert job.snapshot().result.error_code == "persistence_error"
    assert services.repo.messages(sid) == before


def test_manual_title_survives_remove_and_retry_even_when_equal_to_question(knowledge):
    repo = knowledge.repo
    sid = repo.new_session()
    repo.save_turn(
        sid, "same", AnswerResult(answer="error", mode="error", debug=Inspection(question="q"))
    )
    repo.rename_session(sid, "same")
    repo.delete_failed_turn(sid, repo.messages(sid)[1]["id"])
    assert repo.sessions()[0]["title"] == "same" and repo.empty_session() is None
