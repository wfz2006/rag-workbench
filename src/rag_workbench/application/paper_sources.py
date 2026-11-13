"""Build bounded, page-local evidence for questions about identified documents."""

import hashlib
import re
from dataclasses import dataclass, field

from rag_workbench.domain.models import Chunk, Document, RetrievalResult, Section

PAPER_BUDGET = 18_000
_PREFIX = r"^[ \t]*[■▪●•*]?[ \t]*(?:\d+(?:\.\d+)*\.?[ \t]+)?"
_SUFFIX = r"[ \t]*(?:$|[:：.。](?=$|\s))"
REFERENCES = re.compile(
    _PREFIX + r"(?:references(?: and notes)?|notes and references|bibliography|参考文献)" + _SUFFIX,
    re.I | re.M,
)
SECTION_HEADINGS = (
    re.compile(_PREFIX + r"(?:experimental(?: section| methods| details)?|methods|"
               r"materials and methods|实验方法|实验部分)" + _SUFFIX, re.I | re.M),
    re.compile(_PREFIX + r"(?:results(?: and discussion)?|discussion|结果与讨论)"
               + _SUFFIX, re.I | re.M),
    re.compile(_PREFIX + r"(?:conclusions?(?: and outlook)?|summary and outlook|结论|总结)"
               + _SUFFIX, re.I | re.M),
)
_INLINE_CONCLUSION = re.compile(r"^[ \t]*In (?:summary|conclusion),[ \t]+", re.I | re.M)


@dataclass
class PaperEvidence:
    candidates: list[RetrievalResult] = field(default_factory=list)
    answer: str | None = None
    reason: str = ""


def page_source(
    document: Document, page: int | None, text: str, *, start: int = 0,
    method: str = "paper", ordinal: int = 0,
) -> RetrievalResult:
    identity = hashlib.sha256(f"{method}:{document.id}:{page}:{start}:{text}".encode()).hexdigest()
    return RetrievalResult(
        chunk=Chunk(
            id=identity, document_id=document.id, document_name=document.name, text=text,
            ordinal=ordinal, page=page, start=start, end=start + len(text),
        ),
        score=0.0, passed_threshold=False, retrieval_method=method,
        original_page=document.source_page,
    )


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            first, last = merged[-1]
            merged[-1] = (first, max(last, end))
        else:
            merged.append((start, end))
    return merged


def paper_passages(
    document: Document, sections: list[Section], *, budget: int = PAPER_BUDGET
) -> list[RetrievalResult]:
    pages: list[tuple[int | None, str]] = []
    for index, section in enumerate(sections):
        text = section.text
        references = REFERENCES.search(text) if index >= len(sections) // 3 else None
        if references:
            text = text[:references.start()]
        if text.strip():
            pages.append((section.page, text))
        if references:
            break
    if len(pages) <= 10 and sum(len(text) for _, text in pages) <= budget:
        return [
            page_source(document, page, text, ordinal=i) for i, (page, text) in enumerate(pages)
        ]
    if not pages:
        return []

    excerpts: dict[int, list[tuple[int, int]]] = {}

    def add(index: int, start: int, length: int) -> None:
        text = pages[index][1]
        end = min(len(text), start + length)
        if end < len(text):
            line_end = text.rfind("\n", start + length // 2, end)
            if line_end > start:
                end = line_end + 1
        if start < end:
            excerpts.setdefault(index, []).append((start, end))

    # Keep front matter, major sections and representative body pages in a shared budget.
    add(0, 0, budget // 5)
    for section_number, pattern in enumerate(SECTION_HEADINGS):
        for index, (_, text) in enumerate(pages):
            match = pattern.search(text)
            if match is None and section_number == 2 and index >= len(pages) // 2:
                match = _INLINE_CONCLUSION.search(text)
            if match:
                add(index, match.start(), budget // 8)
                break

    # Sample the late body for papers whose methods or conclusion are inline.
    body = [i for i, (_, text) in enumerate(pages) if len(text.strip()) >= 150]
    if not body:
        body = list(range(len(pages)))
    middle = body[len(body) // 2]
    late_middle = body[(len(body) - 1) * 2 // 3]
    sampled = list(dict.fromkeys((late_middle, middle, body[-1])))
    for index in sampled:
        text = pages[index][1]
        size = budget // 9
        start = max(0, (len(text) - size) // 2) if index != body[-1] else max(0, len(text) - size)
        if start and (line_start := text.rfind("\n", max(0, start - 150), start)) >= 0:
            start = line_start + 1
        add(index, start, size)

    if len(pages) == 1:
        # A long text note has no page breaks to sample across.
        add(0, max(0, (len(pages[0][1]) - budget // 12) // 2), budget // 12)
    elif 1 not in excerpts:
        add(1, 0, budget // 12)

    excerpts = {index: _merge_ranges(ranges) for index, ranges in excerpts.items()}
    available = budget - sum(end - start for ranges in excerpts.values() for start, end in ranges)
    # Fill selected pages in full when the remaining budget allows it. This keeps
    # nearby experimental details and their context together in a citation.
    for index in dict.fromkeys((0, *sampled)):
        length = len(pages[index][1])
        additional = length - sum(end - start for start, end in excerpts.get(index, ()))
        if len(pages) > 1 and additional <= available:
            excerpts[index] = [(0, length)]
            available -= additional

    sources = []
    for index, ranges in sorted(excerpts.items()):
        page, text = pages[index]
        sources.extend(
            page_source(document, page, text[start:end], start=start, ordinal=i)
            for i, (start, end) in enumerate(ranges, len(sources))
        )
    return sources
