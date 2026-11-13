import hashlib
import re

from rag_workbench.domain.models import Chunk, Section

PAGE_MARKER = re.compile(r"〔p(\d{4,6})〕")
_PARAGRAPH_END = re.compile(r"\n[ \t]*\n")
_SENTENCE_END = re.compile(r"""[。！？；]|[.!?;]["'”’)\]]*(?=\s)""")
_LINE_END = re.compile(r"\n")
_SPACE_END = re.compile(r"\s+")


def _inside_word(text: str, offset: int) -> bool:
    return 0 < offset < len(text) and all(
        char.isascii() and (char.isalnum() or char == "_") for char in text[offset - 1 : offset + 1]
    )


def _chunk_end(text: str, start: int, end: int) -> int:
    if end == len(text):
        return end
    midpoint = start + (end - start) // 2
    # The lookahead can see whitespace just past the window, without including it.
    for pattern in (_PARAGRAPH_END, _SENTENCE_END, _LINE_END):
        boundaries = [
            match.end()
            for match in pattern.finditer(text, midpoint, end + 1)
            if match.end() <= end
        ]
        if boundaries:
            return boundaries[-1]
    if _inside_word(text, end):
        boundaries = list(_SPACE_END.finditer(text, midpoint, end))
        if boundaries:
            return boundaries[-1].end()
    return end


def _next_start(text: str, earliest: int, desired: int, end: int) -> int:
    start = desired
    while start > earliest and _inside_word(text, start):
        start -= 1
    if _inside_word(text, start):
        start = desired
        while start < end and _inside_word(text, start):
            start += 1
    return start


def split_sections(
    document_id: str,
    name: str,
    sections: list[Section],
    size: int,
    overlap: int,
) -> list[Chunk]:
    """Deterministic character windows, preserving page and exact cleaned-text offsets."""
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("需要 size > overlap >= 0")
    chunks: list[Chunk] = []
    for section in sections:
        markers = [
            (match.start(), int(match.group(1))) for match in PAGE_MARKER.finditer(section.text)
        ]
        start = 0
        while start < len(section.text):
            end = _chunk_end(section.text, start, min(start + size, len(section.text)))
            text = section.text[start:end]
            if text.strip():
                identity = f"{document_id}:{section.page}:{start}:{size}:{overlap}:{text}"
                page = section.page
                if page is None and markers:
                    page = max((value for offset, value in markers if offset < end), default=None)
                chunks.append(
                    Chunk(
                        id=hashlib.sha256(identity.encode()).hexdigest(),
                        document_id=document_id,
                        document_name=name,
                        text=text,
                        ordinal=len(chunks),
                        page=page,
                        start=start,
                        end=end,
                    )
                )
            if end == len(section.text):
                break
            # Keep a minimum stride so near-size overlap cannot create one chunk per character.
            earliest = start + max(1, size // 4)
            start = (
                _next_start(section.text, earliest, max(earliest, end - overlap), end)
                if overlap else end
            )
    return chunks
