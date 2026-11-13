"""Paper intents and references grounded in names from the document catalog."""

import re
import unicodedata

from rag_workbench.domain.models import Document

_PAPER_REFERENCE = re.compile(
    r"论文|文献|本研究|这篇|该篇|此篇|本文|"
    r"\b(?:papers?|articles?|publications?|manuscripts?)\b|"
    r"\.(?:pdf|txt|md)(?=$|[^a-z0-9_.-])"
)
_PAPER_INTENT = re.compile(
    r"总结|概括|概述|概览|概况|综述|结构化|主要(?:讲|研究|内容|发现|结论)|"
    r"(?:讲|说|写)了(?:什么|啥)|内容是(?:什么|啥)|对比|比较|区别|结论|实验方法|"
    r"标题|题目|作者|年份|发表(?:时间|日期)|材料体系|选材|选择(?:理由|原因)|"
    r"(?<!数据)结构(?!体)|薄膜|器件|性能|迁移率|开关比|阈值电压|"
    r"\b(?:summari[sz]e|summary|overview|profile|titles?|authors?|year|doi|"
    r"materials?|rationale|structure|thin[ -]films?|devices?|performance|"
    r"compare|comparison|contrast|conclusions?|mobilit(?:y|ies)|vth|threshold voltages?)\b|"
    r"\bon\s*[/\-:]\s*off\b|"
    r"\bmain(?:ly)?\s+(?:discuss|about|findings|conclusions)\b"
)
_OTHER_TASK = re.compile(
    r"预测|编写.{0,8}代码|写.{0,6}代码|全文.{0,8}翻译|翻译.{0,12}全文|"
    r"\b(?:predict|forecast|translate|implement|code)\b"
)
_CHINESE_LEFT_CONTEXT = re.compile(
    r"(?:阅读|读|总结|概括|概述|介绍|查看|分析|提取|整理|比较|对比|论文|文献|"
    r"文件|这篇|该篇|此篇|这份|该份|关于|针对|根据|依据|从|和|与|及)$"
)
_CHINESE_RIGHT_CONTEXT = re.compile(
    r"^(?:的|中|里|是|有|和|与|及|这篇|该篇|这份|该份|主要|讲|采用|使用|"
    r"研究|介绍|涉及|然后|告诉|给|帮|关于|对比|比较)"
)
_NAME_YEAR = re.compile(r"^([a-z][a-z'-]{2,})[_ -]+((?:19|20)\d{2})(?=[_. -]|$)")
_YEAR_NAME = re.compile(r" - ((?:19|20)\d{2}) - ([a-z][a-z'-]{2,}) - ")
_GENERIC_STEM = re.compile(
    r"(?:papers?|articles?|publications?|manuscripts?|documents?|files?|"
    r"abstracts?|methods?|results?|discussions?|conclusions?|references?|notes?|"
    r"reviews?|summar(?:y|ies)|experiments?|reports?|"
    r"论文|文献|文章|文件|报告|摘要|方法|实验|结果|讨论|结论|笔记|综述)"
)


def paper_question(question: str, *, has_reference: bool = False) -> bool:
    """Recognize paper overviews/profile fields, excluding other requested operations.

    This is only an intent gate: it neither identifies a document nor proves that
    any requested field exists. A caller must resolve and ground the target.
    """
    folded = question.casefold()
    return bool(
        (has_reference or _PAPER_REFERENCE.search(folded))
        and _PAPER_INTENT.search(folded)
        and not _OTHER_TASK.search(folded)
    )


def _is_han(character: str) -> bool:
    return (
        "\u3400" <= character <= "\u9fff"
        or "\uf900" <= character <= "\ufaff"
        or "\U00020000" <= character <= "\U000323af"
    )


def _word_character(character: str) -> bool:
    return character.isalnum() or unicodedata.category(character).startswith("M")


def _literal_boundary(question: str, start: int, end: int, name: str) -> bool:
    if start:
        previous = question[start - 1]
        if previous in "_.-" or (_word_character(previous) and not _is_han(previous)):
            return False
        if (
            _is_han(previous)
            and _is_han(name[0])
            and not _CHINESE_LEFT_CONTEXT.search(question[:start])
        ):
            return False
    if end < len(question):
        following = question[end]
        if following in "_-" or (_word_character(following) and not _is_han(following)):
            return False
        if re.match(r"[\[(][a-z0-9_-]", question[end:]):
            return False
        if (
            following == "."
            and end + 1 < len(question)
            and (_word_character(question[end + 1]) or question[end + 1] in "_-")
        ):
            return False
        if (
            _is_han(following)
            and any(_is_han(character) for character in name)
            and not _CHINESE_RIGHT_CONTEXT.match(question[end:])
        ):
            return False
    return True


def _quoted(question: str, start: int, end: int) -> bool:
    return (
        start > 0
        and end < len(question)
        and (question[start - 1], question[end])
        in {('"', '"'), ("'", "'"), ("`", "`"), ("《", "》"), ("“", "”"), ("‘", "’")}
    )


def resolve_documents(
    question: str, documents: list[Document]
) -> tuple[list[Document], bool, str]:
    """Resolve targets, ambiguity and the query with explicit source labels masked."""
    folded = question.casefold()
    spans: list[tuple[int, int, str]] = []
    source_labels: set[tuple[int, int]] = set()
    for document in documents:
        name = document.name.casefold()
        if not name:
            continue
        extension = document.extension.casefold()
        if extension and not extension.startswith("."):
            extension = "." + extension
        stem = name[: -len(extension)] if extension and name.endswith(extension) else name
        for literal in dict.fromkeys([name, stem]):
            if not literal:
                continue
            needs_quotes = len(literal) < 3 or _GENERIC_STEM.fullmatch(literal) is not None
            for match in re.finditer(re.escape(literal), folded):
                start, end = match.span()
                if needs_quotes and not _quoted(folded, start, end):
                    continue
                if _literal_boundary(folded, start, end, literal):
                    spans.append((start, end, document.id))
                    if literal == name and extension and name.endswith(extension):
                        source_labels.add((start, end))
        # Both parts must occur in a known filename form; surnames alone are ambiguous.
        author_year = _NAME_YEAR.match(stem)
        year_author = _YEAR_NAME.search(stem)
        if document.extension == ".pdf" and (author_year or year_author):
            author, year = author_year.groups() if author_year else year_author.groups()[::-1]
            alias = rf"{re.escape(author)}[ _-]+{year}"
            for match in re.finditer(alias, folded):
                start, end = match.span()
                if _literal_boundary(folded, start, end, match[0]):
                    spans.append((start, end, document.id))
                    source_labels.add((start, end))
    targets: dict[tuple[int, int], set[str]] = {}
    for start, end, identity in spans:
        if not any(
            other_start <= start and end <= other_end and (other_start, other_end) != (start, end)
            for other_start, other_end, _ in spans
        ):
            targets.setdefault((start, end), set()).add(identity)
    matched_ids = set().union(*targets.values())
    found: list[Document] = []
    for document in documents:
        if document.id in matched_ids:
            found.append(document)
            matched_ids.remove(document.id)
    labels = source_labels.intersection(targets)
    return found, any(len(ids) > 1 for ids in targets.values()), _mask_labels(question, labels)


def _mask_labels(question: str, labels: set[tuple[int, int]]) -> str:
    if not labels:
        return question
    # Casefold can expand a character (e.g. ß). Keep the original casing of
    # sample names and conditions outside the references.
    offsets = [i for i, char in enumerate(question) for _ in char.casefold()] + [len(question)]
    chars = list(question)
    for start, end in labels:
        left, right = offsets[start], offsets[end]
        chars[left:right] = " " * (right - left)
    return "".join(chars)


def matching_documents(question: str, documents: list[Document]) -> list[Document]:
    return resolve_documents(question, documents)[0]
