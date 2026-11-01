"""Inspectable metrics. Retrieval scores do not measure factual correctness."""

from typing import Literal

from pydantic import Field

from rag_workbench.application.pipeline import RagPipeline
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.models import Contract

SERVICE_ERROR_CODES = frozenset({
    "missing_api_key", "timeout", "connection", "authentication", "balance", "permission",
    "rate_limit", "provider_error", "embedding_unavailable", "index_missing", "index_corrupt",
    "index_incompatible", "dimension_mismatch", "index_busy", "knowledge_busy",
})
OUTPUT_ERROR_CODES = frozenset({
    "invalid_answer", "invalid_evidence", "invalid_teaching_hint", "invalid_citation",
    "empty_response", "incomplete_response",
})


def is_service_error(code: str | None) -> bool:
    """Only explicitly identified infrastructure failures qualify for a retryable run."""
    return code in SERVICE_ERROR_CODES


class EvalCase(Contract):
    id: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=2000)
    relevant_document_ids: list[str] = Field(default_factory=list)
    expected_mode: Literal["knowledge_base", "general", "insufficient_evidence"]


def _mean(values: list[float | bool]) -> float | None:
    return sum(values) / len(values) if values else None


def evaluate(pipeline: RagPipeline, cases: list[EvalCase], *, with_routing: bool = False) -> dict:
    if not cases or len({c.id for c in cases}) != len(cases):
        raise AppError("invalid_dataset", "评测集不能为空，且问题 ID 必须唯一。")
    hits, recalls, irrelevant_hits, routes, citations = [], [], [], [], []
    eligible_hits, eligible_recalls = [], []
    errors = service_errors = 0
    rows = []
    for case in cases:
        result = pipeline.answer(case.question) if with_routing else None
        error = result.error_code if result else None
        try:
            candidates = (
                result.debug.candidates if result else pipeline.knowledge.retrieve(case.question)
            )
        except AppError as exc:
            candidates, error = [], exc.code
        found = {r.chunk.document_id for r in candidates}
        eligible_found = {r.chunk.document_id for r in candidates if r.eligible}
        relevant = set(case.relevant_document_ids)
        if error is not None:
            pass  # A failed case is neither a retrieval miss nor a successful negative rejection.
        elif relevant:
            hits.append(bool(found & relevant))
            recalls.append(len(found & relevant) / len(relevant))
            eligible_hits.append(bool(eligible_found & relevant))
            eligible_recalls.append(len(eligible_found & relevant) / len(relevant))
        else:
            irrelevant_hits.append(any(r.eligible for r in candidates))
        citation_ok = None
        if result:
            routes.append(result.mode == case.expected_mode)
            if error == "invalid_citation":
                citation_ok = False
                citations.append(False)
            elif result.mode == "knowledge_base":
                actual = {r.chunk.id for r in candidates if r.eligible}
                citation_ok = bool(result.sources) and all(
                    r.chunk.id in actual and f"[{r.chunk.id}]" in result.answer
                    for r in result.sources
                )
                citations.append(citation_ok)
        errors += int(error is not None)
        service_errors += int(is_service_error(error))
        rows.append(
            {
                "id": case.id,
                "question": case.question,
                "expected_mode": case.expected_mode,
                "relevant_document_ids": sorted(relevant),
                "error_code": error,
                "mode": result.mode if result else None,
                "route_reason": result.debug.reason if result else None,
                "answer": result.answer if result else None,
                "cited_chunk_ids": [r.chunk.id for r in result.sources] if result else [],
                "citation_valid": citation_ok,
                "retrieved_document_ids": sorted(found),
                "eligible_document_ids": sorted(eligible_found),
                "retrieved_chunk_ids": [r.chunk.id for r in candidates],
                "eligible_chunk_ids": [r.chunk.id for r in candidates if r.eligible],
                "candidate_scores": [r.score for r in candidates],
                "candidates": [r.model_dump() for r in candidates],
            }
        )
    return {
        "cases": len(cases),
        "top_k": pipeline.knowledge.settings.top_k,
        "threshold": pipeline.knowledge.settings.similarity_threshold,
        "retrieval_hit_rate": _mean(hits),
        "recall_at_k": _mean(recalls),
        "eligible_hit_rate": _mean(eligible_hits),
        "eligible_recall_at_k": _mean(eligible_recalls),
        "irrelevant_threshold_hit_rate": _mean(irrelevant_hits),
        "routing_accuracy": _mean(routes),
        "citation_validity": _mean(citations),
        "service_errors": service_errors,
        "evaluation_errors": errors,
        "successful_retrieval_cases": len(cases) - errors,
        "with_routing": with_routing,
        "results": rows,
    }
