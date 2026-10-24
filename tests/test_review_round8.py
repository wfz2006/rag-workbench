"""Eighth review: feedback identity and consistent contextual/multiline intent."""

import pytest

from rag_workbench.application.pipeline import RagPipeline
from rag_workbench.application.retrieval_expansion import coverage_focus
from rag_workbench.application.teaching import contextual_query, is_followup
from rag_workbench.domain.models import ChatMessage
from rag_workbench.ui import pages

from .fakes import FakeProvider
from .test_review_round7 import line


@pytest.mark.parametrize(
    "question,expected",
    [
        ("那他怎么死的？", "王伦 死"),
        ("后来他怎么死的？", "王伦 死"),
        ("为什么他会落草？", "王伦 会落草"),
        ("那么后来他怎么死的？", "王伦 死"),
        ("之后，她后来怎么样？", "王伦 怎么样"),
    ],
)
def test_prefixed_followups_share_context_and_lexical_focus(question, expected):
    history = [ChatMessage(role="user", content="王伦是谁？")]
    assert is_followup(question)
    context = contextual_query(question, history)
    assert context == "王伦是谁？\n" + question
    assert coverage_focus(question, context) == expected


@pytest.mark.parametrize(
    "question", ["后来林冲怎么上梁山？", "为什么罗辑没有被杀？", "那三体人为什么这样做？"]
)
def test_connectors_do_not_import_history_for_named_new_topics(question):
    assert not is_followup(question)


@pytest.mark.parametrize("status", ["ready", "empty", "incompatible"])
def test_saved_feedback_keeps_operation_prefix_until_status_changes(status):
    feedback = pages.saved_feedback({"index_status": status}, "已恢复页面默认设置。")
    assert feedback["index_status"] == status
    assert pages.current_feedback(feedback, status) == feedback
    changed = "empty" if status == "ready" else "ready"
    refreshed = pages.current_feedback(feedback, changed)
    assert refreshed["index_status"] == changed
    assert "已恢复页面默认设置" not in refreshed["message"]


@pytest.mark.parametrize(
    "message", ["设置已保存，索引重建完成。", "索引重建完成，当前设置已生效。"]
)
def test_rebuild_feedback_survives_first_render(monkeypatch, message):
    state = {}
    monkeypatch.setattr(pages.st, "session_state", state)
    monkeypatch.setattr(pages.st, "toast", lambda *_: None)
    pages._save_feedback(message, index_status="ready")
    assert pages.current_feedback(state["settings_feedback"], "ready")["message"] == message


def test_multiline_quote_question_expands_and_reaches_evidence(knowledge):
    knowledge.settings.chunk_size = 20
    knowledge.settings.chunk_overlap = 0
    knowledge.settings.top_k = 1
    knowledge.settings.retrieval_context_window = 1
    knowledge.ingest(
        "quote.md", (line("保管种子") + line("近邻说明") + line("原话“到期复核”")).encode()
    )
    question = "保管最后说了什么？\n请结合原文说明。"
    direct = knowledge.retrieve(question)
    assert any("到期复核" in r.chunk.text and r.context_for for r in direct)
    inspection = RagPipeline(knowledge, FakeProvider()).inspect(question)
    assert any("到期复核" in r.chunk.text for r in inspection.candidates)
