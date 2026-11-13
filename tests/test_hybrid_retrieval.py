from io import BytesIO

import numpy as np
import pytest
from reportlab.pdfgen.canvas import Canvas

from rag_workbench.application.knowledge import KnowledgeService


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
