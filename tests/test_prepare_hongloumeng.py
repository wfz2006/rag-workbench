import pytest

from scripts.prepare_hongloumeng_pack import (
    derive_chapter_plan,
    extract_chapter,
    parse_chinese_number,
    title_key,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("一", 1),
        ("十", 10),
        ("十一", 11),
        ("二十", 20),
        ("二十一", 21),
        ("九十九", 99),
        ("一百", 100),
        ("一百零一", 101),
        ("一百一十", 110),
        ("一百二十", 120),
        ("一○○", 100),
        ("一○一", 101),
        ("一一○", 110),
        ("一二○", 120),
    ],
)
def test_parse_chinese_number(text, expected):
    assert parse_chinese_number(text) == expected


def test_extract_chapter_keeps_narrative_and_drops_notes():
    raw = """
    <html><body>
    <h1>第二回 贾夫人仙逝扬州城 冷子兴演说荣国府</h1>
    <p class="calibre5">却说<sup>[1]</sup>封肃听了，<img src="../1.jpeg"/>大喜。</p>
    <p class="center">一局输赢料不真<br/>香销茶尽尚逡巡</p>
    <p class="note">[1] 封肃——甄士隐的岳父。</p>
    </body></html>
    """
    result = extract_chapter(raw, "第二回 贾夫人仙逝扬州城 冷子兴演说荣国府")
    assert result["notes"] == 1
    assert result["images"] == 1
    assert result["paragraphs"] == [
        "却说封肃听了，〔图〕大喜。",
        "一局输赢料不真\n香销茶尽尚逡巡",
    ]
    assert all("甄士隐的岳父" not in item for item in result["paragraphs"])


def test_title_key_treats_missing_glyph_placeholder_as_equal():
    assert title_key("第七十八回 老学士闲征姽■词") == title_key("第七十八回 老学士闲征姽〔图〕词")


def test_extract_chapter_rejects_wrong_heading():
    raw = "<h1>第一回 甄士隐梦幻识通灵 贾雨村风尘怀闺秀</h1><p>正文</p>"
    with pytest.raises(ValueError, match="heading"):
        extract_chapter(raw, "第二回 贾夫人仙逝扬州城 冷子兴演说荣国府")


def test_derive_chapter_plan_supports_combined_chapters():
    navpoints = [
        {"label": "第一回 甄士隐梦幻识通灵 贾雨村风尘怀闺秀", "src": "text/c1.html"},
        {"label": "第二回至三回 贾夫人仙逝扬州城 冷子兴演说荣国府", "src": "text/c2.html"},
        {"label": "第四回 薄命女偏逢薄命郎 葫芦僧乱判葫芦案", "src": "text/c4.html"},
    ]
    rows = derive_chapter_plan(
        navpoints, {"text/c1.html", "text/c2.html", "text/c4.html"}, expected_total=4
    )
    assert [(row["chapter_start"], row["chapter_end"]) for row in rows] == [(1, 1), (2, 3), (4, 4)]
    assert sum(row["chapter_end"] - row["chapter_start"] + 1 for row in rows) == 4


def test_derive_chapter_plan_rejects_gap_and_duplicate_href():
    with pytest.raises(ValueError, match="contiguous"):
        derive_chapter_plan(
            [
                {"label": "第一回 甲", "src": "text/c1.html"},
                {"label": "第三回 乙", "src": "text/c3.html"},
            ],
            {"text/c1.html", "text/c3.html"},
        )
    with pytest.raises(ValueError, match="Duplicate"):
        derive_chapter_plan(
            [
                {"label": "第一回 甲", "src": "text/c1.html"},
                {"label": "第二回 乙", "src": "text/c1.html"},
            ],
            {"text/c1.html"},
        )
