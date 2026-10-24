"""Sixth review: explicit edit state, stable timeline and safe retrieval/presentation."""

from types import SimpleNamespace

import pytest
from markdown_it import MarkdownIt

from rag_workbench.application.retrieval_expansion import coverage_queries
from rag_workbench.domain.models import AnswerResult, Inspection
from rag_workbench.ui.components import answer_presentation
from rag_workbench.ui.session_state import UnsavedTurn, conversation_timeline

from .test_review_round5 import grounded


@pytest.mark.parametrize(
    "text",
    ["```python\nx=1", "~~~~text\nvalue", "段落\\", "参见[说明][1]\n\n[1]: https://example.com"],
)
def test_markdown_boundaries_do_not_consume_sources_or_next_claim(text):
    result = grounded(text)
    display, plain = answer_presentation(result)
    parser = MarkdownIt("commonmark").enable("table")
    html = parser.render(display)
    assert "<p>下一段正常正文。" in html
    assert "〔来源" not in "".join(t.content for t in parser.parse(display) if t.type == "fence")
    if text.endswith("example.com"):
        assert 'href="https://example.com"' in html
    if text.endswith("\\"):
        assert "\\ 〔" not in display
    assert "〔来源" not in plain


def test_replacement_unsaved_is_one_overlay_at_original_position():
    messages = [
        {"id": i, "created_at": str(i), "role": "user" if i % 2 else "assistant"}
        for i in range(1, 7)
    ]
    result = AnswerResult(
        answer="new", mode="general", error_code="persistence_error", debug=Inspection(question="q")
    )
    turn = UnsavedTurn(1, "edited", result, "9", replaces_assistant_id=4)
    timeline = conversation_timeline(messages, [turn])
    assert [m["id"] if isinstance(m, dict) else m.question for m in timeline] == [
        1,
        2,
        "edited",
        5,
        6,
    ]


def test_running_replacement_is_at_original_position():
    from rag_workbench.ui.session_state import RunningTurn

    messages = [
        {"id": i, "created_at": str(i), "role": "user" if i % 2 else "assistant"}
        for i in range(1, 7)
    ]
    job = SimpleNamespace(question="retry", created_at="9", replaces_assistant_id=4)
    timeline = conversation_timeline(messages, [], job)
    assert isinstance(timeline[2], RunningTurn)
    assert timeline[3]["id"] == 5


def test_navigation_cancels_editing(monkeypatch):
    from rag_workbench.ui import shell

    state = {"workspace_page": "AI 聊天", "editing_failed_turn": {"assistant_id": 4}}
    monkeypatch.setattr(shell.st, "session_state", state)
    shell.navigate("系统设置")
    assert "editing_failed_turn" not in state


def test_running_first_retry_uses_new_title_and_manual_name_stays():
    from rag_workbench.ui.shell import session_title

    s = {
        "id": "s",
        "message_count": 2,
        "title": "old",
        "first_assistant_id": 2,
        "custom_title": False,
    }
    job = SimpleNamespace(question="new", replaces_assistant_id=2)
    assert session_title(s, {"s": job}, {}) == "new"
    s.update(title="新会话", custom_title=True, message_count=0)
    assert session_title(s, {"s": job}, {}) == "新会话"


def test_substitutions_do_not_rewrite_inserted_aliases():
    variants = coverage_queries("林冲和教头", {"aliases": {"林冲": ["林教头"], "教头": ["武师"]}})
    assert "林武师和武师" not in variants
    assert "林教头和武师" in variants


def test_synonym_limit_shares_space_between_keys():
    variants = coverage_queries(
        "alpha beta", {"aliases": {"alpha": [f"a{i}" for i in range(50)], "beta": ["b0"]}}, limit=10
    )
    assert "alpha b0" in variants and any(q == "a0 b0" for q in variants)
    assert len(variants) <= 10


def test_relations_proposals_retain_entity_constraint():
    values = {"relations": [{"people": ["甲"], "cues": ["父亲"], "related": ["乙"]}]}
    variants = coverage_queries("甲的父亲是谁", values)
    assert any("甲" in q and "乙" in q for q in variants)
    assert "乙" not in variants


@pytest.mark.parametrize(
    "question,subject,expected",
    [("他怎么死的？", "王伦是谁？", "王伦 死"), ("那林冲呢？", "王伦是谁？", "林冲")],
)
def test_followup_lexical_focus_keeps_subject_not_particles(question, subject, expected):
    from rag_workbench.application.retrieval_expansion import coverage_focus

    assert coverage_focus(question, f"{subject}\n{question}") == expected


@pytest.mark.parametrize(
    "status,requires",
    [("ready", False), ("empty", False), ("incompatible", True), ("corrupt", True)],
)
def test_saved_feedback_tracks_actual_index_not_previous_settings(status, requires):
    from rag_workbench.ui.pages import saved_feedback

    assert saved_feedback({"index_status": status})["requires_reindex"] is requires


def test_collection_retains_retry_target_and_overlay_is_discardable(monkeypatch):
    from rag_workbench.ui import session_state as ui

    result = AnswerResult(
        answer="new", mode="general", error_code="persistence_error", debug=Inspection(question="q")
    )
    snapshot = SimpleNamespace(done=True, result=result, question="q", created_at="now")
    job = SimpleNamespace(replaces_assistant_id=4, snapshot=lambda: snapshot)
    state = {"answer_jobs": {"s": job}}
    monkeypatch.setattr(ui.st, "session_state", state)
    assert ui.collect_finished_jobs()
    turn = state["unsaved_turns"]["s"][0]
    assert turn.replaces_assistant_id == 4
    ui.discard_unsaved_turn("s", turn.key)
    assert "s" not in state["unsaved_turns"]


def test_delete_nonfirst_failed_turn_does_not_attempt_retitle(knowledge, monkeypatch):
    from unittest.mock import Mock

    repo = knowledge.repo
    sid = repo.new_session()
    repo.save_turn(
        sid, "first", AnswerResult(answer="ok", mode="general", debug=Inspection(question="q"))
    )
    repo.save_turn(
        sid, "second", AnswerResult(answer="err", mode="error", debug=Inspection(question="q"))
    )
    retitle = Mock()
    monkeypatch.setattr(repo, "_retitle", retitle)
    repo.delete_failed_turn(sid, repo.messages(sid)[-1]["id"])
    retitle.assert_not_called()
