import json
from math import sqrt

import numpy as np

from rag_workbench.application.knowledge import KnowledgeService
from rag_workbench.application.pipeline import RagPipeline

from .fakes import FakeProvider


class NeighborRankEmbedder:
    signature = "neighbor-rank-test-embedding-v1"

    def encode_documents(self, texts):
        vectors = []
        for text in texts:
            if "种子" in text:
                vectors.append([0.9, sqrt(0.19)])
            elif "目标" in text:
                vectors.append([0.7, sqrt(0.51)])
            elif "近邻" in text:
                vectors.append([0.6, 0.8])
            elif "旁支" in text:
                vectors.append([0.5, sqrt(0.75)])
            elif "保管" in text:
                vectors.append([0.85, sqrt(0.2775)])
            else:
                vectors.append([0.0, 1.0])
        return np.array(vectors, dtype=np.float32)

    def encode_query(self, _):
        return np.array([[1.0, 0.0]], dtype=np.float32)


def test_context_keeps_real_scores_and_only_adjacent_same_document(knowledge):
    knowledge.settings.top_k = 1
    knowledge.settings.retrieval_context_window = 1
    first = "保管" + "甲" * 98
    knowledge.ingest("code.md", (first + "乙" * 150).encode())
    knowledge.ingest("other.md", "无关文档丙".encode())
    results = knowledge.retrieve("保管")
    assert len(results) == 2
    seed, context = results
    assert seed.passed_threshold and seed.context_for is None
    assert context.context_for == seed.chunk.id
    assert context.score == 0.0 and not context.passed_threshold and context.eligible
    assert context.chunk.document_id == seed.chunk.document_id
    assert context.chunk.ordinal == seed.chunk.ordinal + 1


def test_context_is_opt_in(knowledge):
    knowledge.settings.top_k = 1
    knowledge.ingest("code.md", ("保管" + "甲" * 200).encode())
    assert len(knowledge.retrieve("保管")) == 1


def test_context_window_survives_full_top_k_primary(knowledge):
    knowledge.settings.top_k = 20
    knowledge.settings.retrieval_context_window = 1
    document = knowledge.ingest("long.md", ("保管期限为三年。" * 300).encode())
    results = knowledge.retrieve("保管")
    assert len(results) > 20
    assert len(results) <= 40
    assert all(result.chunk.document_id == document.id for result in results)
    assert any(result.context_for for result in results)


def test_full_context_budget_prefers_more_relevant_neighbor(knowledge):
    knowledge.settings.chunk_size = 20
    knowledge.settings.chunk_overlap = 0
    knowledge.settings.top_k = 20
    knowledge.settings.retrieval_context_window = 2
    service = KnowledgeService(knowledge.settings, NeighborRankEmbedder())
    seed = "保管种子" + "甲" * 15 + "。"
    near = "近邻说明" + "乙" * 15 + "。"
    target = "目标说明" + "丙" * 15 + "。"
    service.ingest("seed.md", (seed + near + target).encode())
    for index in range(19):
        primary = f"保管其他{index:02d}" + "丁" * 13 + "。"
        branch = f"旁支说明{index:02d}" + "戊" * 13 + "。"
        service.ingest(f"other{index}.md", (primary + branch).encode())
    results = service.retrieve("保管")
    assert len(results) <= 40
    target_result = next(result for result in results if "目标说明" in result.chunk.text)
    assert target_result.context_for is not None


def test_context_can_support_answer_with_actual_chunk_id(knowledge):
    knowledge.settings.top_k = 1
    knowledge.settings.retrieval_context_window = 1
    knowledge.ingest("code.md", ("保管" + "甲" * 98 + "尾部" + "乙" * 130).encode())

    class TailProvider(FakeProvider):
        def complete(self, system, user, **kwargs):
            data = json.loads(user)
            evidence = data["evidence"]
            tail = next((e for e in evidence if "尾部" in e["text"]), evidence[0])
            if "supporting_chunk_ids" in system:
                return json.dumps(
                    dict(
                        answerable=True,
                        reason="尾部包含所需证据",
                        supporting_chunk_ids=[tail["chunk_id"]],
                    )
                )
            return json.dumps(
                {"claims": [{"text": "尾部有证据。", "chunk_ids": [tail["chunk_id"]]}]}
            )

    result = RagPipeline(knowledge, TailProvider()).answer("保管")
    assert result.mode == "knowledge_base"
    assert result.sources[0].context_for and result.sources[0].score == 0.0
