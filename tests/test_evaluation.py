from rag_workbench.application.evaluation import EvalCase, evaluate
from rag_workbench.application.pipeline import RagPipeline

from .fakes import FakeProvider
from .test_knowledge import TEXT


def test_metrics_count_positive_negative_and_citation_consistency(knowledge):
    doc = knowledge.ingest("manual.txt", TEXT)
    cases = [
        EvalCase(
            id="positive",
            question="保管期限",
            relevant_document_ids=[doc.id],
            expected_mode="knowledge_base",
        ),
        EvalCase(id="negative", question="无关问题", expected_mode="general"),
    ]
    report = evaluate(RagPipeline(knowledge, FakeProvider()), cases, with_routing=True)
    assert report["retrieval_hit_rate"] == 1.0
    assert report["recall_at_k"] == 1.0
    assert report["eligible_hit_rate"] == 1.0
    assert report["eligible_recall_at_k"] == 1.0
    assert report["irrelevant_threshold_hit_rate"] == 0.0
    assert report["routing_accuracy"] == 1.0
    assert report["citation_validity"] == 1.0
    assert report["service_errors"] == 0


def test_eligible_recall_separates_retrieval_from_threshold(knowledge):
    doc = knowledge.ingest("manual.txt", TEXT)
    cases = [
        EvalCase(
            id="below_threshold",
            question="无关问题",
            relevant_document_ids=[doc.id],
            expected_mode="general",
        )
    ]
    report = evaluate(RagPipeline(knowledge, FakeProvider()), cases)
    assert report["retrieval_hit_rate"] == 1.0
    assert report["recall_at_k"] == 1.0
    assert report["eligible_hit_rate"] == 0.0
    assert report["eligible_recall_at_k"] == 0.0


def test_api_errors_not_counted_as_correct_routes(knowledge):
    doc = knowledge.ingest("manual.txt", TEXT)
    cases = [
        EvalCase(
            id="q",
            question="保管期限",
            relevant_document_ids=[doc.id],
            expected_mode="knowledge_base",
        )
    ]
    report = evaluate(
        RagPipeline(knowledge, FakeProvider(failure="balance")), cases, with_routing=True
    )
    assert report["routing_accuracy"] == 0
    assert report["service_errors"] == 1
    assert report["citation_validity"] is None
