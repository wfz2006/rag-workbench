import pytest

from rag_workbench.application.query_features import (
    contains_quote,
    contains_title,
    question_type,
)


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("宝玉听了哪支曲文后悟到禅机？", "title"),
        ("黛玉临终前最后喊的是什么？", "quote"),
        ("宝玉说“任凭弱水三千，我只取一瓢饮”，表达什么意思？", "meaning"),
        ("刘姥姥第一次进荣国府得到多少银子？", "general"),
    ],
)
def test_question_type(question, expected):
    assert question_type(question) == expected


def test_quote_and_title_markers():
    assert contains_quote("“宝玉，宝玉，你好……”")
    assert not contains_quote("宝玉，宝玉，你好")
    assert contains_title("《寄生草》")
    assert not contains_title("一支曲子")
