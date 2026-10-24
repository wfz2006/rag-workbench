from rag_workbench.infrastructure.lexical import BM25Index, reciprocal_rank_fusion, tokenize


def test_tokenize_keeps_chinese_bigrams_and_code_terms():
    tokens = tokenize("链表 list ne[i]")
    assert "链表" in tokens
    assert "list" in tokens
    assert "ne" in tokens


def test_bm25_ranks_exact_term_above_unrelated():
    index = BM25Index(["顺序表定义", "链表操作", "顺序表按位查找"])
    scores = index.scores("顺序表")
    assert scores[0] > scores[1]
    assert scores[2] > scores[1]


def test_rrf_combines_dense_and_lexical_rankings():
    fused = reciprocal_rank_fusion([[0, 1, 2], [1, 2, 0]])
    assert fused == [1, 0, 2]
