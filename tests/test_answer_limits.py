import json

from rag_workbench.application.pipeline import RagPipeline

from .fakes import FakeProvider


class MultiClaimProvider(FakeProvider):
    def __init__(self, texts):
        super().__init__()
        self.texts = texts

    def complete(self, system, user, **kwargs):
        self.calls.append((system, user, kwargs.get("history")))
        data = json.loads(user)
        chunk_id = data["evidence"][0]["chunk_id"]
        if "supporting_chunk_ids" in system:
            return json.dumps(
                {"answerable": True, "reason": "测试证据", "supporting_chunk_ids": [chunk_id]}
            )
        return json.dumps(
            {"claims": [{"text": text, "chunk_ids": [chunk_id]} for text in self.texts]}
        )


def test_fact_answers_are_limited_to_claims_and_chars(knowledge):
    knowledge.settings.max_answer_claims = 2
    knowledge.settings.max_answer_chars = 1000
    knowledge.ingest("a.txt", "保管期限为三年。".encode())
    provider = MultiClaimProvider(["第一句答案。", "第二句补充。", "第三句补充。"])
    result = RagPipeline(knowledge, provider).answer("保管期限是多久？")
    assert "第一句答案" in result.answer
    assert "第二句补充" in result.answer
    assert "第三句补充" not in result.answer


def test_fact_answers_drop_trailing_claims_to_meet_char_cap(knowledge):
    knowledge.settings.max_answer_claims = 3
    knowledge.settings.max_answer_chars = 40
    knowledge.ingest("a.txt", "保管期限为三年。".encode())
    long = "甲" * 25
    provider = MultiClaimProvider([long, long, long])
    result = RagPipeline(knowledge, provider).answer("保管期限是多久？")
    assert result.answer.count("甲" * 25) == 1


def test_quote_answers_are_not_truncated(knowledge):
    knowledge.settings.max_answer_claims = 1
    knowledge.settings.max_answer_chars = 10
    knowledge.ingest("a.txt", "保管原话：“到期复核。”".encode())
    provider = MultiClaimProvider(["第一句原话。", "第二句原话。", "第三句原话。"])
    result = RagPipeline(knowledge, provider).answer("保管原话是什么？")
    assert "第三句原话" in result.answer
