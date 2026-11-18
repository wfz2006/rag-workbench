import json
from io import BytesIO

import numpy as np
import pytest
from reportlab.pdfgen.canvas import Canvas

from rag_workbench.application.knowledge import KnowledgeService
from rag_workbench.application.pipeline import RagPipeline


class KeywordEmbedder:
    signature = "keyword-test-embedding-v1"

    def encode_documents(self, texts):
        vectors = []
        for text in texts:
            if "香蕉" in text:
                vectors.append([0.9, 0.435])
            elif "苹果" in text:
                vectors.append([1.0, 0.0])
            else:
                vectors.append([0.0, 1.0])
        return np.array(vectors, dtype=np.float32)

    def encode_query(self, text):
        assert text == "香蕉"
        return np.array([[1.0, 0.0]], dtype=np.float32)


def test_retrieve_fuses_lexical_match_with_dense_ranking(settings):
    knowledge = KnowledgeService(settings, KeywordEmbedder())
    knowledge.settings.top_k = 1
    knowledge.ingest("unrelated.md", "无关内容".encode())
    knowledge.ingest("apple.md", "苹果".encode())
    knowledge.ingest("banana.md", "香蕉".encode())
    results = knowledge.retrieve("香蕉")
    assert len(results) == 1
    assert "香蕉" in results[0].chunk.text
    assert results[0].passed_threshold


def test_lexical_candidate_outside_dense_pool_still_gets_a_score(settings):
    knowledge = KnowledgeService(settings, KeywordEmbedder())
    knowledge.settings.top_k = 2
    knowledge.settings.retrieval_candidate_pool = 1
    knowledge.ingest("unrelated.md", "无关内容".encode())
    knowledge.ingest("apple.md", "苹果".encode())
    knowledge.ingest("banana.md", "香蕉".encode())

    results = knowledge.retrieve("香蕉")

    banana = next(result for result in results if "香蕉" in result.chunk.text)
    assert banana.lexical_score > 0
    assert banana.score == pytest.approx(0.9, abs=0.01)


class NotesEmbedder:
    signature = "notes-test-embedding-v1"

    def encode_documents(self, texts):
        return np.asarray(
            [[1.0, 0.0] if "迁移率" in text else [0.0, 1.0] for text in texts],
            dtype=np.float32,
        )

    def encode_query(self, text):
        return np.array([[1.0, 0.0]], dtype=np.float32)


def test_bilingual_sample_and_temperature_retrieve_the_english_pdf(settings):
    knowledge = KnowledgeService(settings, NotesEmbedder())
    knowledge.settings.top_k = 2
    query = "IDTT-IDD-N 在 210°C 退火下的最高迁移率和开关比是多少？"
    stream = BytesIO()
    pdf = Canvas(stream)
    pdf.drawString(40, 780, "Annealed IDTT-IDD-N at 210 C, hole mobility of 6.1.")
    pdf.save()
    knowledge.ingest_many([
        ("article.pdf", stream.getvalue()),
        ("note1.txt", "IDTT-IDD-N 210°C 退火下的最高迁移率和开关比整理，第一项。".encode()),
        ("note2.txt", "IDTT-IDD-N 210°C 退火下的最高迁移率和开关比整理，第二项。".encode()),
        ("other.txt", b"IDTT and IDD and N are unrelated at 210 C."),
    ])

    results = knowledge.retrieve(query)

    source = next((r for r in results if r.chunk.document_name == "article.pdf"), None)
    assert source is not None and source.lexical_passed
    assert "hole mobility of 6.1" in source.chunk.text
    assert any(r.chunk.document_name.endswith(".txt") for r in results)


class ChinesePreferredEmbedder(NotesEmbedder):
    signature = "chinese-preferred-test-embedding-v1"

    def encode_documents(self, texts):
        return np.asarray(
            [[1.0, 0.0] if not text.isascii() else [0.0, 1.0] for text in texts],
            dtype=np.float32,
        )


class PreparationEvidenceProvider:
    stage_signals = (
        "formed nano-aggregates", "particles retained their size", "interconnected fibers",
    )

    def __init__(self, *, all_stages=True):
        self.required = self.stage_signals if all_stages else self.stage_signals[1:]

    def complete(self, system, user, *, json_mode=False, history=None):
        payload = json.loads(user)
        supports = [
            next((item["chunk_id"] for item in payload["evidence"] if signal in item["text"]), None)
            for signal in self.required
        ]
        answerable = all(supports)
        return json.dumps({
            "answerable": answerable,
            "reason": "Has direct observations for all preparation stages" if answerable else
            "Preparation observations are missing",
            "supporting_chunk_ids": supports if answerable else [],
        })


def preparation_pdf(sample):
    stream = BytesIO()
    pdf = Canvas(stream)
    for line in (
        f"Stage 1: {sample} formed nano-aggregates in the initial solution.",
        f"Stage 2: After gelation, {sample} particles retained their size.",
        f"Stage 3: Solvent exchange assembled {sample} into interconnected fibers.",
        f"The electrical current of {sample} remained stable during measurements.",
    ):
        pdf.drawString(40, 780, line)
        pdf.showPage()
    pdf.save()
    return stream.getvalue()


@pytest.mark.parametrize("sample", ["p(g2T-T)", "P3HT"])
def test_chinese_preparation_question_reaches_original_stage_evidence(settings, sample):
    settings.allow_general = False
    service = KnowledgeService(settings, ChinesePreferredEmbedder())
    service.ingest_many([
        ("preparation.pdf", preparation_pdf(sample)),
        ("different-polymer.pdf", preparation_pdf("p(g2T-TT)")),
        *[(f"note-{number}.txt", f"笔记{number}：在制备过程中有哪些结构上的变化。".encode())
          for number in range(6)],
    ])

    result = RagPipeline(service, PreparationEvidenceProvider()).inspect(
        f"{sample}在制备过程中有哪些结构上的变化"
    )

    assert result.route == "knowledge_base", result.reason
    assert result.evidence is not None
    supporting = [
        row.chunk for row in result.candidates
        if row.chunk.id in result.evidence.supporting_chunk_ids
    ]
    assert {chunk.document_name for chunk in supporting} == {"preparation.pdf"}
    assert {chunk.page for chunk in supporting} == {1, 2, 3}


def test_named_stages_reach_evidence_without_expanding_unstated_conditions(settings):
    settings.allow_general = False
    settings.top_k = 3
    service = KnowledgeService(settings, ChinesePreferredEmbedder())
    service.ingest_many([
        ("preparation.pdf", preparation_pdf("p(g2T-T)")),
        *[(f"note-{number}.txt", f"笔记{number}：凝胶化和溶剂置换过程中形貌如何演变？".encode())
          for number in range(4)],
    ])
    pipeline = RagPipeline(service, PreparationEvidenceProvider(all_stages=False))

    grounded = pipeline.inspect("p(g2T-T)在凝胶化和溶剂置换过程中形貌如何演变？")
    assert grounded.route == "knowledge_base"
    assert grounded.evidence is not None
    assert {row.chunk.page for row in grounded.candidates
            if row.chunk.id in grounded.evidence.supporting_chunk_ids} == {2, 3}

    unconfirmed = pipeline.inspect("p(g2T-T)在无氧条件下凝胶化和溶剂置换过程中形貌如何演变？")
    assert unconfirmed.route == "insufficient_evidence"
