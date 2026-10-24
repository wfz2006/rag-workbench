import json

import pytest

from rag_workbench.application.retrieval_expansion import (
    expand_query,
    load_retrieval_expansions,
)
from rag_workbench.domain.errors import AppError


def write_map(tmp_path, data):
    path = tmp_path / "expansions.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_expands_aliases_and_kinship_terms(tmp_path):
    path = write_map(
        tmp_path,
        {
            "aliases": {"林黛玉": ["颦儿", "绛珠草"]},
            "kinship": {"前身": ["前世", "仙草"], "原名": ["本名"]},
        },
    )
    expansions = load_retrieval_expansions(path)
    result = expand_query("林黛玉的前身是什么？", expansions)
    assert result.text.startswith("林黛玉的前身是什么？")
    assert {"颦儿", "绛珠草", "前世", "仙草"}.issubset(set(result.terms))


def test_expands_relation_edges(tmp_path):
    path = write_map(
        tmp_path,
        {
            "aliases": {"宝玉": ["贾宝玉"], "王夫人": ["王氏", "太太"]},
            "relations": [
                {
                    "people": ["宝玉", "贾宝玉"],
                    "cues": ["母亲", "妈妈"],
                    "related": ["王夫人"],
                }
            ],
        },
    )
    result = expand_query("宝玉的母亲是谁？", load_retrieval_expansions(path))
    assert {"王夫人", "王氏", "太太"}.issubset(set(result.terms))


def test_unmatched_question_is_unchanged(tmp_path):
    path = write_map(tmp_path, {"aliases": {"林黛玉": ["颦儿"]}})
    result = expand_query("今天天气如何？", load_retrieval_expansions(path))
    assert result.text == "今天天气如何？"
    assert result.terms == ()


def test_terms_are_deduplicated_and_capped(tmp_path):
    path = write_map(
        tmp_path,
        {"aliases": {"宝玉": ["宝玉", "怡红公子", "宝二爷"]}},
    )
    result = expand_query("宝玉是谁？", load_retrieval_expansions(path), max_terms=1)
    assert result.terms == ("怡红公子",)


def test_missing_path_disables_expansion(tmp_path):
    assert load_retrieval_expansions(None) == {}
    result = expand_query("林黛玉的前身是什么？", {})
    assert result.text == "林黛玉的前身是什么？"


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"unknown": {}},
        {"aliases": []},
        {"aliases": {"林黛玉": "颦儿"}},
        {"aliases": {"林黛玉": []}},
        {"relations": [{"people": ["宝玉"], "cues": ["母亲"]}]},
        {"relations": {"people": ["宝玉"]}},
    ],
)
def test_invalid_expansion_files_fail_loudly(tmp_path, data):
    path = write_map(tmp_path, data)
    with pytest.raises(AppError, match="查询扩展"):
        load_retrieval_expansions(path)
