import numpy as np

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
