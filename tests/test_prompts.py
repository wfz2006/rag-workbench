import json
from pathlib import Path

from rag_workbench.application.pipeline import evidence_payload
from rag_workbench.application.prompts import EVIDENCE_SYSTEM, GROUNDED_SYSTEM
from rag_workbench.domain.models import Chunk, RetrievalResult


def chunk(identity: str, ordinal: int, text: str) -> Chunk:
    return Chunk(
        id=identity,
        document_id="d" * 64,
        document_name="d.md",
        text=text,
        ordinal=ordinal,
        start=0,
        end=len(text),
    )


def test_grounded_prompt_keeps_language_and_complete_evidence_guard():
    assert "与用户问题相同的语言" in GROUNDED_SYSTEM
    assert "不要补全被截断的句子" in GROUNDED_SYSTEM


def test_evidence_payload_marks_primary_and_context_roles():
    primary = RetrievalResult(
        chunk=chunk("a" * 64, 0, "原始片段"), score=0.9, passed_threshold=True
    )
    context = RetrievalResult(
        chunk=chunk("b" * 64, 1, "相邻片段"),
        score=0.4,
        passed_threshold=False,
        context_for=primary.chunk.id,
    )
    payload = json.loads(evidence_payload("问题", [primary, context]))
    assert payload["evidence"][0]["retrieval_role"] == "primary"
    assert payload["evidence"][1]["retrieval_role"] == "context"


def test_evidence_prompt_requires_literal_specific_answers_and_honest_reason():
    assert "具体名称" in EVIDENCE_SYSTEM
    assert "不得声称" in EVIDENCE_SYSTEM
    assert "context 片段与 primary 片段同等可用" in EVIDENCE_SYSTEM
    assert "contains_title" in EVIDENCE_SYSTEM


def test_evidence_payload_prioritizes_title_chunk_for_title_question():
    plain = RetrievalResult(
        chunk=chunk("a" * 64, 0, "黛玉说，都是昨儿一支曲子惹出来的。"),
        score=1.0,
        passed_threshold=True,
    )
    title = RetrievalResult(
        chunk=chunk("b" * 64, 1, "宝玉又填一支《寄生草》，也写在偈后。"),
        score=0.5,
        passed_threshold=True,
    )
    payload = json.loads(evidence_payload("宝玉听了哪支曲文后悟到禅机？", [plain, title]))
    assert payload["question_type"] == "title"
    assert payload["evidence"][0]["chunk_id"] == title.chunk.id
    assert payload["evidence"][0]["contains_title"] is True
    assert payload["evidence"][1]["contains_title"] is False


def test_evidence_payload_marks_quote_question_and_prioritizes_quote_chunk():
    plain = RetrievalResult(
        chunk=chunk("a" * 64, 0, "黛玉只留下一句话。"),
        score=1.0,
        passed_threshold=True,
    )
    quote = RetrievalResult(
        chunk=chunk("b" * 64, 1, "猛听黛玉直声叫道：“宝玉，宝玉，你好……”"),
        score=0.5,
        passed_threshold=True,
    )
    payload = json.loads(evidence_payload("黛玉临终前最后喊的是什么？", [plain, quote]))
    assert payload["question_type"] == "quote"
    assert payload["evidence"][0]["chunk_id"] == quote.chunk.id
    assert payload["evidence"][0]["contains_quote"] is True
    assert payload["evidence"][1]["contains_quote"] is False


def test_hongloumeng_profile_limits_fact_answers():
    profile = (
        Path(__file__).resolve().parents[1]
        / "knowledge_packs"
        / "hongloumeng"
        / "profile"
        / "story.md"
    )
    text = profile.read_text(encoding="utf-8")
    assert "1–2 句" in text
    assert "最多三条 claims" in text
    assert "120 字" in text


def test_hongloumeng_profile_requires_named_meaning_answers():
    profile = (
        Path(__file__).resolve().parents[1]
        / "knowledge_packs"
        / "hongloumeng"
        / "profile"
        / "story.md"
    )
    text = profile.read_text(encoding="utf-8")
    assert "解释含义" in text
    assert "之一" in text
    assert "具体态度" in text
    assert "只在意黛玉" in text
