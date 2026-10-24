import hashlib
import re

from rag_workbench.domain.models import Chunk, Section

PAGE_MARKER = re.compile(r"〔p(\d{4,6})〕")


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
            end = min(start + size, len(section.text))
            if end < len(section.text):
                boundaries = list(
                    re.finditer(r"\n|[。！？；]", section.text[start + size // 2 : end])
                )
                if boundaries:
                    end = start + size // 2 + boundaries[-1].end()
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
            start = max(start + 1, end - overlap)
    return chunks
