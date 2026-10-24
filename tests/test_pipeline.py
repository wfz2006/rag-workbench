import json

import pytest

from rag_workbench.application.pipeline import RagPipeline

from .fakes import FakeProvider
from .test_knowledge import TEXT


def test_empty_uses_general_without_embedding(knowledge, embedder):
    embedder.fail = True
    result = RagPipeline(knowledge, FakeProvider()).answer("某内部项目明天的安排？")
    assert result.mode == "general"
    assert "不是来自知识库" in result.answer
    assert not result.sources


def test_hit_contains_only_real_sources(knowledge):
    knowledge.ingest("manual.txt", TEXT)
    result = RagPipeline(knowledge, FakeProvider()).answer("保管多久？")
    assert result.mode == "knowledge_base"
    assert result.sources[0].chunk.text == TEXT.decode()
    assert result.sources[0].chunk.id in result.answer
    assert result.sources[0].chunk.id in result.debug.evidence.supporting_chunk_ids


def test_below_threshold_skips_judge(knowledge):
    knowledge.ingest("manual.txt", TEXT)
    provider = FakeProvider()
    result = RagPipeline(knowledge, provider).answer("无关问题")
    assert result.mode == "general"
    assert len(provider.calls) == 1
    assert all(not r.passed_threshold for r in result.debug.candidates)


def test_related_but_not_answerable_routes_general(knowledge):
    knowledge.ingest("manual.txt", TEXT)
    result = RagPipeline(knowledge, FakeProvider(answerable=False)).answer("保管负责人的私人电话？")
    assert result.mode == "general"
    assert result.debug.candidates[0].passed_threshold
    assert not result.debug.evidence.answerable
    assert not result.sources


@pytest.mark.parametrize("failure", ["timeout", "rate_limit", "authentication", "balance"])
def test_service_error_never_becomes_general(knowledge, failure):
    knowledge.ingest("manual.txt", TEXT)
    result = RagPipeline(knowledge, FakeProvider(failure=failure)).answer("保管期限？")
    assert result.mode == "error"
    assert result.error_code == failure
    assert not result.sources


@pytest.mark.parametrize("provider", [FakeProvider(invalid_id=True), FakeProvider(malformed=True)])
def test_judge_failure_does_not_approve_evidence(knowledge, provider):
    knowledge.ingest("manual.txt", TEXT)
    result = RagPipeline(knowledge, provider).answer("保管期限？")
    assert result.mode == "error"
    assert not result.sources


def test_malformed_evidence_is_retried_once_then_errors(knowledge):
    knowledge.ingest("manual.txt", TEXT)
    provider = FakeProvider(malformed=True)
    result = RagPipeline(knowledge, provider).answer("保管期限？")
    assert result.error_code == "invalid_evidence"
    assert len(provider.calls) == 2


def test_flaky_evidence_format_recovers_on_retry(knowledge):
    knowledge.ingest("manual.txt", TEXT)

    class FlakyEvidence(FakeProvider):
        def __init__(self):
            super().__init__()
            self.evidence_attempts = 0

        def complete(self, system, user, **kwargs):
            if "supporting_chunk_ids" in system:
                self.evidence_attempts += 1
                if self.evidence_attempts == 1:
                    self.calls.append((system, user, kwargs.get("history")))
                    return "not JSON"
            return super().complete(system, user, **kwargs)

    provider = FlakyEvidence()
    result = RagPipeline(knowledge, provider).answer("保管期限？")
    assert result.mode == "knowledge_base"
    assert provider.evidence_attempts == 2
    assert len(provider.calls) == 3
    assert result.sources[0].chunk.id in result.answer


def test_answer_citation_outside_support_is_rejected(knowledge):
    knowledge.ingest("manual.txt", TEXT)

    class BadAnswer(FakeProvider):
        def complete(self, system, user, **kwargs):
            if "supporting_chunk_ids" not in system:
                return json.dumps({"claims": [{"text": "编造", "chunk_ids": ["madeup"]}]})
            return super().complete(system, user, **kwargs)

    result = RagPipeline(knowledge, BadAnswer()).answer("保管期限？")
    assert result.mode == "error"
    assert result.error_code == "invalid_citation"


def test_disabled_fallback_abstains_without_llm(knowledge):
    knowledge.settings.allow_general = False
    provider = FakeProvider()
    result = RagPipeline(knowledge, provider).answer("问题")
    assert result.mode == "insufficient_evidence"
    assert not provider.calls


def test_index_failure_does_not_call_llm(knowledge):
    knowledge.ingest("manual.txt", TEXT)
    generation = knowledge.repo.get_state("active")["generation"]
    (knowledge.root / "indexes" / generation / "index.faiss").unlink()
    provider = FakeProvider()
    result = RagPipeline(knowledge, provider).answer("保管期限？")
    assert result.mode == "error"
    assert not provider.calls
