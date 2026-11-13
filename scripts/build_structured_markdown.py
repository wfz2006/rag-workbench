"""Convert a text-layer PDF into structured Markdown for grounded retrieval.

The desktop build chunks documents with a fixed 400-character window and 60
characters of overlap. Newlines are chunk boundaries, so rebuilding paragraphs
and section headings turns each chunk into a complete semantic unit instead of
a mid-sentence slice.

Usage:
    python scripts/build_structured_markdown.py INPUT.pdf OUTPUT.md \
        --title "..." --journal "..." --topic "..."
    python scripts/build_structured_markdown.py INPUT.pdf OUTPUT.md \
        --noise "^Received" --noise "^Accepted"
    python scripts/build_structured_markdown.py INPUT.pdf OUTPUT.md \
        --provenance OUTPUT.provenance.json

Only the PDF text layer is read. Scanned pages need OCR first.
The optional sidecar maps Markdown blocks to one-based page and text-layer line
numbers before cleaning. Manually supplied metadata has no source location.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import warnings
from collections import Counter
from dataclasses import dataclass
from io import BytesIO
from itertools import combinations
from pathlib import Path

from pypdf import PdfReader

LIGATURES = str.maketrans({"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"})
HEADING = re.compile(r"^\d+(?:\.\d+)*\.?\s+[A-Z][A-Za-z]")
BARE_PAGE_NUMBER = re.compile(r"^\s*\d{1,4}\s*$")
DOI_LINE = re.compile(r"(dx\.doi\.org|^\s*DOI[: ]|pubs\.acs\.org)", re.I)
LEADING_NOISE = re.compile(r"^(Published|Received|Accepted|Copyright)", re.I)
SENTENCE_END = re.compile(r"[.!?][\"')\]]?$")


@dataclass(frozen=True)
class SourceLine:
    text: str
    page: int
    number: int


@dataclass(frozen=True)
class MarkdownBlock:
    text: str
    source_lines: tuple[SourceLine, ...] = ()


def extract_lines(reader: PdfReader, patterns: list[re.Pattern[str]]) -> list[SourceLine | None]:
    """Keep text-layer line positions and mark page boundaries with None."""
    lines: list[SourceLine | None] = []
    for page_number, page in enumerate(reader.pages, 1):
        for line_number, raw in enumerate((page.extract_text() or "").splitlines(), 1):
            line = " ".join(raw.translate(LIGATURES).split())
            if not line or BARE_PAGE_NUMBER.match(line) or DOI_LINE.search(line):
                continue
            if LEADING_NOISE.search(line):
                continue
            if any(pattern.search(line) for pattern in patterns):
                continue
            lines.append(SourceLine(line, page_number, line_number))
        lines.append(None)
    return lines


def detect_running_heads(lines: list[SourceLine | None], page_count: int) -> set[str]:
    """Short lines repeated on most pages are running headers, not content."""
    counts = Counter(line.text for line in lines if line is not None and len(line.text) < 90)
    threshold = max(3, page_count // 2)
    return {line for line, count in counts.items() if count >= threshold}


def build_paragraphs(lines: list[SourceLine | None], min_chars: int) -> list[MarkdownBlock]:
    paragraphs: list[MarkdownBlock] = []
    buffer: list[SourceLine] = []

    def flush() -> None:
        if buffer:
            paragraphs.append(MarkdownBlock(" ".join(line.text for line in buffer), tuple(buffer)))
            buffer.clear()

    for line in [*lines, None]:
        if line is None:
            flush()
            continue
        if HEADING.match(line.text):
            flush()
            paragraphs.append(MarkdownBlock("## " + line.text, (line,)))
            continue
        buffer.append(line)
        if (
            SENTENCE_END.search(line.text)
            and len(" ".join(item.text for item in buffer)) >= min_chars
        ):
            flush()
    return paragraphs


def build_provenance(blocks: list[MarkdownBlock], source_hash: str) -> dict:
    locations = []
    start = 1
    for block in blocks:
        end = start + len(re.findall(r"\r\n|\r|\n", block.text))
        source = None
        if block.source_lines:
            lines = [line.number for line in block.source_lines]
            source = {
                "pdf_page": block.source_lines[0].page,
                "text_line_start": lines[0],
                "text_line_end": lines[-1],
                "text_lines": lines,
            }
        locations.append({"markdown_line_start": start, "markdown_line_end": end, "source": source})
        start = end + 2  # Markdown blocks are joined by two line breaks.
    return {"schema_version": 1, "source_sha256": source_hash, "blocks": locations}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="source PDF with a text layer")
    parser.add_argument("output", type=Path, help="destination Markdown file")
    parser.add_argument("--title", default="", help="document title for metadata")
    parser.add_argument("--journal", default="", help="journal / citation line")
    parser.add_argument("--topic", default="", help="one-line topic summary")
    parser.add_argument(
        "--noise", action="append", default=[],
        help="extra regex for lines to drop (repeatable)",
    )
    parser.add_argument("--min-paragraph-chars", type=int, default=200)
    parser.add_argument("--provenance", type=Path, help="optional JSON source map")
    args = parser.parse_args()

    paths = [args.input, args.output]
    if args.provenance is not None:
        paths.append(args.provenance)
    for left, right in combinations(paths, 2):
        if left.resolve() == right.resolve() or (
            left.exists() and right.exists() and left.samefile(right)
        ):
            parser.error("input, Markdown output and provenance output must use distinct files")

    logging.getLogger("pypdf").setLevel(logging.ERROR)
    warnings.filterwarnings("ignore")

    source_hash = ""
    if args.provenance is None:
        reader = PdfReader(str(args.input))
    else:
        source = BytesIO(args.input.read_bytes())
        source_hash = hashlib.sha256(source.getbuffer()).hexdigest()
        reader = PdfReader(source)
    patterns = [re.compile(expr, re.I) for expr in args.noise]
    lines = extract_lines(reader, patterns)
    heads = detect_running_heads(lines, len(reader.pages))
    lines = [line for line in lines if line is None or line.text not in heads]
    paragraphs = build_paragraphs(lines, args.min_paragraph_chars)

    header: list[str] = []
    if args.title:
        header += ["# " + args.title, "Title: " + args.title]
    if args.journal:
        header.append("Journal: " + args.journal)
    if args.topic:
        header.append("Topic: " + args.topic)

    blocks = [*(MarkdownBlock(item) for item in header), *paragraphs]
    content = "\n\n".join(block.text for block in blocks) + "\n"
    args.output.write_text(content, encoding="utf-8")
    if args.provenance is not None:
        provenance = build_provenance(blocks, source_hash)
        args.provenance.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    print(
        f"pages={len(reader.pages)} paragraphs={len(paragraphs)} "
        f"chars={len(content)} -> {args.output}"
    )


if __name__ == "__main__":
    main()
