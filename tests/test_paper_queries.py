import pytest

from rag_workbench.application.paper_queries import matching_documents, paper_question
from rag_workbench.domain.models import Document


def document(identity: str, name: str) -> Document:
    return Document(
        id=identity, name=name, extension="." + name.rsplit(".", 1)[-1], size=100, status="ready"
    )


@pytest.mark.parametrize(
    "question",
    [
        "请你阅读这篇薄膜文献1，然后给我论文标题、作者、年份、DOI、材料体系、选择理由，"
        "以及对应的结构、薄膜、器件和性能信息",
        "请你阅读薄膜文献1，告诉我这个论文主要讲了什么",
        "请总结薄膜文献1",
        "chemical review 2012.pdf 的 DOI 和作者是谁？",
        "这篇论文为什么选择该材料体系？",
        "这篇论文采用了什么器件结构和薄膜工艺？",
        "请整理论文中报告的性能指标",
        "请给出这篇论文的结构化概况",
        "What does this paper mainly discuss?",
        "Summarize this research article.",
        "Who are the authors of chemical review 2012.pdf?",
        "What material system did this paper study?",
        "Explain the rationale for choosing the materials in this paper.",
        "Give a structured profile of this paper: title, authors, year, DOI, "
        "material system, rationale, structure, thin film, device and performance.",
    ],
)
def test_recognizes_requested_paper_overview_and_profile_fields(question):
    assert paper_question(question)


@pytest.mark.parametrize(
    "question",
    [
        "",
        "保管多久？",
        "数据结构有哪些？",
        "为什么二叉树性能比链表好？",
        "这首诗的作者是谁？",
        "今年是哪一年？",
        "什么是 DOI？",
        "请讲解有机半导体的材料体系和器件性能",
        "How can I improve the performance of Python code?",
        "What is a thin film?",
        "论文里的排序算法如何实现？",
        "这篇论文中的结构体如何定义？",
        "请将这篇论文全文翻译为中文",
        "请总结论文并预测新材料性能",
        "编写代码复现这篇论文的器件性能",
        "Summarize this paper and predict the performance of a new material.",
        "chemical review 2012.pdf",
    ],
)
def test_leaves_general_rag_and_unsupported_paper_tasks_unchanged(question):
    assert not paper_question(question)


@pytest.mark.parametrize(
    "question",
    [
        "请你阅读这篇薄膜文献1，然后给我论文标题和作者",
        "请你阅读薄膜文献1，告诉我这个论文主要讲了什么",
        "请总结薄膜文献1",
        "薄膜文献1的 DOI 是什么？",
        "请阅读《薄膜文献1.pdf》。",
    ],
)
def test_resolves_literal_chinese_filename_with_optional_extension(question):
    documents = [document("one", "薄膜文献1.pdf"), document("ten", "薄膜文献10.pdf")]
    assert [item.id for item in matching_documents(question, documents)] == ["one"]


def test_explicit_extension_disambiguates_same_stem():
    documents = [
        document("pdf", "chemical review 2012.pdf"),
        document("text", "chemical review 2012.txt"),
    ]
    found = matching_documents("chemical review 2012.PDF 的 DOI 和作者是谁？", documents)
    assert [item.id for item in found] == ["pdf"]


def test_extension_free_name_preserves_ambiguity_in_input_order():
    documents = [
        document("text", "chemical review 2012.txt"),
        document("pdf", "chemical review 2012.pdf"),
    ]
    found = matching_documents("Summarize chemical review 2012", documents)
    assert [item.id for item in found] == ["text", "pdf"]


def test_duplicate_filenames_are_not_silently_resolved():
    first = document("first", "薄膜文献1.pdf")
    second = document("second", "薄膜文献1.pdf")
    found = matching_documents("请总结薄膜文献1.pdf", [first, second, first])
    assert [item.id for item in found] == ["first", "second"]


def test_filename_matching_uses_unicode_casefold():
    found = matching_documents("Summarize STRASSE.PDF.", [document("paper", "Straße.pdf")])
    assert [item.id for item in found] == ["paper"]


def test_filename_regex_characters_are_literal():
    found = matching_documents(
        "请阅读《DPP+(2) [draft].pdf》", [document("paper", "DPP+(2) [draft].pdf")]
    )
    assert [item.id for item in found] == ["paper"]


@pytest.mark.parametrize(
    "question",
    [
        "Summarize pre-thinfilm.pdf",
        "Summarize otherthinfilm.pdf",
        "Summarize préthinfilm.pdf",
        "Summarize thinfilm.pdf.bak",
        "Summarize thinfilm2",
        "Summarize thinfilm_notes.pdf",
        "Summarize thinfilm.docx",
    ],
)
def test_does_not_select_part_of_another_file_identifier(question):
    assert matching_documents(question, [document("paper", "thinfilm.pdf")]) == []


@pytest.mark.parametrize(
    "question",
    [
        "请总结有机薄膜文献1.pdf",
        "请总结薄膜文献1续篇.pdf",
        "请总结文献1",
        "请介绍 Bao 在 2012 年的论文",
    ],
)
def test_does_not_guess_chinese_filename_alias_or_author(question):
    assert matching_documents(question, [document("paper", "薄膜文献1.pdf")]) == []


def test_longer_filename_does_not_also_match_nested_shorter_name():
    documents = [document("short", "review.pdf"), document("long", "chemical review.pdf")]
    found = matching_documents("Summarize chemical review.pdf", documents)
    assert [item.id for item in found] == ["long"]


def test_two_separate_mentions_remain_multiple_targets():
    documents = [document("short", "review.pdf"), document("long", "chemical review.pdf")]
    found = matching_documents("Compare review.pdf and chemical review.pdf", documents)
    assert [item.id for item in found] == ["short", "long"]


def test_short_bare_word_is_not_assumed_to_name_a_file():
    assert matching_documents("What is a thin film?", [document("letter", "a.pdf")]) == []
    found = matching_documents("Summarize `a`", [document("letter", "a.pdf")])
    assert [item.id for item in found] == ["letter"]


def test_empty_question_or_catalog_has_no_matches():
    assert matching_documents("", [document("paper", "thinfilm.pdf")]) == []
    assert matching_documents("请总结薄膜文献1", []) == []


def test_resolves_author_and_year_from_standard_filename_forms():
    wiley = document("wiley", "Macro Chemistry   Physics - 2017 - Opoku - Copolymers.pdf")
    jps = document("jps", "Yanagisawa_2008_Jpn._J._Appl._Phys._47_4728.pdf")

    assert matching_documents("总结 Opoku 2017 的内容", [wiley, jps]) == [wiley]
    assert matching_documents("总结 Yanagisawa_2008 的内容", [wiley, jps]) == [jps]
    assert not matching_documents("总结 Opoku 2018 的内容", [wiley, jps])


def test_stem_does_not_match_a_different_parenthesized_filename():
    assert not matching_documents("请总结文献2(1).pdf", [document("base", "文献2.pdf")])


def test_generic_stems_need_an_explicit_document_reference():
    methods = document("methods", "methods.pdf")
    paper = document("paper", "paper.pdf")

    assert not matching_documents("Summarize the experimental methods.", [methods])
    assert not matching_documents("What does the paper say about mobility?", [paper])
    assert matching_documents("Summarize methods.pdf.", [methods]) == [methods]
    assert matching_documents("Summarize ‘paper’.", [paper]) == [paper]
