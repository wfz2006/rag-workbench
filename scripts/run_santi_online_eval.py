"""Run the real LLM routing/answer path and keep answers for manual review."""

import argparse
import json
import sys
import time
from pathlib import Path

from rag_workbench.application.evaluation import EvalCase
from rag_workbench.bootstrap import build_services
from scripts.santi_settings import santi_settings


def mean(values):
    return sum(values) / len(values) if values else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("data/santi"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cases = [
        EvalCase.model_validate_json(line)
        for line in args.labels.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    services = build_services(santi_settings(args.data_dir))
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    rows, hits, recalls, eligible_hits, eligible_recalls = [], [], [], [], []
    irrelevant_hits, routes, citations = [], [], []
    errors = 0
    with output.open("w", encoding="utf-8") as stream:
        for index, case in enumerate(cases, 1):
            started = time.perf_counter()
            try:
                result = services.pipeline.answer(case.question)
            except Exception as exc:  # keep the run alive for per-case reporting
                result = None
                error_code = f"unexpected_{type(exc).__name__}"
            elapsed = time.perf_counter() - started
            relevant = set(case.relevant_document_ids)
            if result is None:
                candidates = []
                mode = "error"
                answer = ""
                sources = []
                evidence_reason = ""
            else:
                candidates = result.debug.candidates
                mode = result.mode
                error_code = result.error_code
                answer = result.answer
                sources = result.sources
                evidence_reason = result.debug.reason
            found = {r.chunk.document_id for r in candidates}
            eligible_found = {r.chunk.document_id for r in candidates if r.eligible}
            if error_code is None:
                if relevant:
                    hits.append(bool(found & relevant))
                    recalls.append(len(found & relevant) / len(relevant))
                    eligible_hits.append(bool(eligible_found & relevant))
                    eligible_recalls.append(len(eligible_found & relevant) / len(relevant))
                else:
                    irrelevant_hits.append(any(r.eligible for r in candidates))
            if result is not None:
                routes.append(mode == case.expected_mode)
                if mode == "knowledge_base":
                    actual = {r.chunk.id for r in candidates if r.eligible}
                    citations.append(
                        bool(sources)
                        and all(
                            r.chunk.id in actual and f"[{r.chunk.id}]" in answer for r in sources
                        )
                    )
            errors += int(error_code is not None)
            row = {
                "id": case.id,
                "question": case.question,
                "expected_mode": case.expected_mode,
                "mode": mode,
                "error_code": error_code,
                "answer": answer,
                "evidence_reason": evidence_reason,
                "sources": [
                    {
                        "chunk_id": r.chunk.id,
                        "document": r.chunk.document_name,
                        "score": r.score,
                        "eligible": r.eligible,
                    }
                    for r in sources
                ],
                "retrieved_document_ids": sorted(found),
                "eligible_document_ids": sorted(eligible_found),
                "seconds": round(elapsed, 2),
            }
            rows.append(row)
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
            print(
                f"[{index}/{len(cases)}] {case.id} {mode} {elapsed:.1f}s",
                file=sys.stderr,
                flush=True,
            )
    summary = {
        "cases": len(cases),
        "top_k": services.knowledge.settings.top_k,
        "threshold": services.knowledge.settings.similarity_threshold,
        "retrieval_hit_rate": mean(hits),
        "recall_at_k": mean(recalls),
        "eligible_hit_rate": mean(eligible_hits),
        "eligible_recall_at_k": mean(eligible_recalls),
        "irrelevant_threshold_hit_rate": mean(irrelevant_hits),
        "routing_accuracy": mean(routes),
        "citation_validity": mean(citations),
        "service_errors": errors,
        "successful_retrieval_cases": len(cases) - errors,
        "with_routing": True,
    }
    summary_path = output.with_name(output.stem + "-summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
