"""Seventh review: editing, Markdown containers, connected query features and live state."""

import json

import pytest
from markdown_it import MarkdownIt

from rag_workbench.application.pipeline import RagPipeline, evidence_payload
from rag_workbench.application.prompts import EVIDENCE_SYSTEM
from rag_workbench.application.query_features import question_type
from rag_workbench.application.retrieval_expansion import coverage_focus
from rag_workbench.domain.models import ChatMessage, RetrievalResult
from rag_workbench.ui.components import _close_fences, answer_presentation
from rag_workbench.ui.pages import current_feedback, saved_feedback

from .fakes import FakeProvider
from .test_prompts import chunk
from .test_review_round5 import grounded


@pytest.mark.parametrize("prefix", ["> ", "  "])
def test_container_closed_fence_never_appends_ghost_code(prefix):
    first = "> ```python\n> x=1" if prefix == "> " else "- item\n\n  ```python\n  x=1"
    text = first + "\n\n容器外的普通段落。"
    assert _close_fences(text) == text
    display, plain = answer_presentation(grounded(text))
    fences = [t for t in MarkdownIt().parse(display) if t.type == "fence"]
    assert len(fences) == 1 and fences[0].content.strip() == "x=1"
    assert "容器外的普通段落。 〔来源 1〕" in display
    assert plain.startswith(text + "\n\n")


@pytest.mark.parametrize(
    "question,expected",
    [
        ("焦大醉骂宁国府时骂了什么？", "quote"),
        ("刘姥姥在贾母宴席上说的自嘲话是什么？", "quote"),
        ("这首曲文里最后一句说了什么？", "quote"),
        ("《葬花吟》这首曲文表达什么意思？", "meaning"),
        ("这句誓言表达什么？", "meaning"),
        ("宝玉听了哪支曲文后悟到禅机？", "title"),
    ],
)
def test_question_intents_cover_plan_cases_without_title_overreach(question, expected):
    assert question_type(question) == expected


def test_payload_uses_shared_quote_and_meaning_features():
    plain = RetrievalResult(
        chunk=chunk("a" * 64, 0, "他说了自嘲话。"), score=1, passed_threshold=True
    )
    quote = RetrievalResult(
        chunk=chunk("b" * 64, 1, "笑道：“老刘，老刘，食量大似牛。”"),
        score=0,
        passed_threshold=False,
        context_for=plain.chunk.id,
    )
    payload = json.loads(evidence_payload("他说的自嘲话是什么？", [plain, quote]))
    assert payload["question_type"] == "quote"
    assert payload["evidence"][0]["chunk_id"] == quote.chunk.id
    assert payload["evidence"][0]["contains_quote"] is True
    assert json.loads(evidence_payload("这句表达什么？", [plain]))["question_type"] == "meaning"
    assert "question_type=quote" in EVIDENCE_SYSTEM and "逐字" in EVIDENCE_SYSTEM


def line(text):
    return text + "甲" * (19 - len(text)) + "。"


@pytest.mark.parametrize("window", [0, 1])
def test_quote_context_is_scoped_bounded_and_opt_in(knowledge, window):
    knowledge.settings.chunk_size = 20
    knowledge.settings.chunk_overlap = 0
    knowledge.settings.top_k = 1
    knowledge.settings.retrieval_context_window = window
    text = (
        line("保管种子") + line("近邻说明") + "".join(line(f"引文{i}“测试语句”") for i in range(8))
    )
    document = knowledge.ingest("quote.md", text.encode())
    knowledge.ingest("unrelated.md", line("“不相关引用”").encode())
    results = knowledge.retrieve("保管最后说了什么？")
    quotes = [r for r in results if "测试语句" in r.chunk.text]
    assert len(quotes) == (4 if window else 0)
    assert all(r.chunk.document_id == document.id and r.context_for for r in quotes)
    assert all(r.score == 0 and r.lexical_score >= 0 for r in quotes)
    assert not any("测试语句" in r.chunk.text for r in knowledge.retrieve("保管"))


def test_quote_expansion_obeys_spoiler_boundary(knowledge):
    knowledge.settings.chunk_size = 20
    knowledge.settings.chunk_overlap = 0
    knowledge.settings.top_k = 1
    knowledge.settings.retrieval_context_window = 1
    knowledge.settings.spoiler_max_page = 10
    knowledge.ingest(
        "spoiler.md", (line("保管种子") + line("近邻说明") + line("〔p0011〕“秘密原话”")).encode()
    )
    assert not any("秘密原话" in r.chunk.text for r in knowledge.retrieve("保管最后说了什么？"))


@pytest.mark.parametrize(
    "question,context,expected",
    [
        ("他后来怎么死的？", "林冲是怎么上梁山的？\n他后来怎么死的？", "林冲 死"),
        ("他后来怎么死的？", "宝玉为什么摔玉？\n他后来怎么死的？", "宝玉 死"),
        ("他怎么死的？", "请介绍一下王伦的生平\n他怎么死的？", "王伦 死"),
        ("他后来呢？", "王伦是谁？\n那林冲呢？\n他后来呢？", "林冲"),
        ("那么林冲呢？", "王伦是谁？\n那么林冲呢？", "林冲"),
    ],
)
def test_followup_focus_uses_nearest_named_subject(question, context, expected):
    assert coverage_focus(question, context) == expected


def test_focused_followup_can_pass_lexical_admission(knowledge):
    from rag_workbench.application.teaching import contextual_query

    knowledge.ingest("history.md", "林冲被逼落草。王伦被杀死。".encode())
    history = [ChatMessage(role="user", content="请介绍一下王伦的生平")]
    question = "他后来怎么死的？"
    results = knowledge.retrieve(contextual_query(question, history), coverage_question=question)
    assert any(r.lexical_passed for r in results)


@pytest.mark.parametrize(
    "before,after", [("empty", "ready"), ("ready", "incompatible"), ("empty", "corrupt")]
)
def test_feedback_reconciles_in_both_directions(before, after):
    feedback = current_feedback(saved_feedback({"index_status": before}), after)
    assert feedback["requires_reindex"] == (after not in {"ready", "empty"})
    assert ("为空知识库" in feedback["message"]) == (after == "empty")


def test_incompatible_index_shows_feedback_before_any_save():
    feedback = current_feedback({}, "incompatible")
    assert feedback["requires_reindex"] and "重建" in feedback["message"]


def test_quote_payload_reaches_real_pipeline_path(knowledge):
    knowledge.ingest("quote.md", "保管原话：“到期复核。”".encode())
    provider = FakeProvider()
    result = RagPipeline(knowledge, provider).answer("保管原话是什么？")
    assert result.mode == "knowledge_base"
    assert all(json.loads(call[1])["question_type"] == "quote" for call in provider.calls)
    assert all("contains_quote" in json.loads(call[1])["evidence"][0] for call in provider.calls)
