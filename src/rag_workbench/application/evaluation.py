"""Inspectable metrics. Retrieval scores do not measure factual correctness."""

from typing import Literal

from pydantic import Field

from rag_workbench.application.pipeline import RagPipeline
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.models import Contract


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
    errors = 0
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
            pass  # A service failure is not a retrieval miss or a successful negative rejection.
        elif relevant:
            hits.append(bool(found & relevant))
            recalls.append(len(found & relevant) / len(relevant))
            eligible_hits.append(bool(eligible_found & relevant))
            eligible_recalls.append(len(eligible_found & relevant) / len(relevant))
        else:
            irrelevant_hits.append(any(r.eligible for r in candidates))
        if result:
            routes.append(result.mode == case.expected_mode)
            if result.mode == "knowledge_base":
                actual = {r.chunk.id for r in candidates if r.eligible}
                citations.append(
                    bool(result.sources)
                    and all(
                        r.chunk.id in actual and f"[{r.chunk.id}]" in result.answer
                        for r in result.sources
                    )
                )
        errors += int(error is not None)
        rows.append(
            {
                "id": case.id,
                "question": case.question,
                "error_code": error,
                "mode": result.mode if result else None,
                "retrieved_document_ids": sorted(found),
                "eligible_document_ids": sorted(eligible_found),
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
        "service_errors": errors,
        "successful_retrieval_cases": len(cases) - errors,
        "with_routing": with_routing,
        "results": rows,
    }
