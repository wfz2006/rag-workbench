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

Only the PDF text layer is read. Scanned pages need OCR first.
"""

from __future__ import annotations

import argparse
import logging
import re
import warnings
from collections import Counter
from pathlib import Path

from pypdf import PdfReader

LIGATURES = str.maketrans({"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"})
HEADING = re.compile(r"^\d+(?:\.\d+)*\.?\s+[A-Z][A-Za-z]")
BARE_PAGE_NUMBER = re.compile(r"^\s*\d{1,4}\s*$")
DOI_LINE = re.compile(r"(dx\.doi\.org|^\s*DOI[: ]|pubs\.acs\.org)", re.I)
LEADING_NOISE = re.compile(r"^(Published|Received|Accepted|Copyright)", re.I)
SENTENCE_END = re.compile(r"[.!?][\"')\]]?$")


def extract_lines(reader: PdfReader, patterns: list[re.Pattern[str]]) -> list[str]:
    """Read the text layer, drop noise, and mark page boundaries with ''."""
    lines: list[str] = []
    for page in reader.pages:
        for raw in (page.extract_text() or "").splitlines():
            line = " ".join(raw.translate(LIGATURES).split())
            if not line or BARE_PAGE_NUMBER.match(line) or DOI_LINE.search(line):
                continue
            if LEADING_NOISE.search(line):
                continue
            if any(pattern.search(line) for pattern in patterns):
                continue
            lines.append(line)
        lines.append("")
    return lines


def detect_running_heads(lines: list[str], page_count: int) -> set[str]:
    """Short lines repeated on most pages are running headers, not content."""
    counts = Counter(line for line in lines if line and len(line) < 90)
    threshold = max(3, page_count // 2)
    return {line for line, count in counts.items() if count >= threshold}


def build_paragraphs(lines: list[str], min_chars: int) -> list[str]:
    paragraphs: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            paragraphs.append(" ".join(buffer))
            buffer.clear()

    for line in [*lines, ""]:
        if not line:
            flush()
            continue
        if HEADING.match(line):
            flush()
            paragraphs.append("## " + line)
            continue
        buffer.append(line)
        if SENTENCE_END.search(line) and len(" ".join(buffer)) >= min_chars:
            flush()
    return paragraphs


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
    args = parser.parse_args()

    logging.getLogger("pypdf").setLevel(logging.ERROR)
    warnings.filterwarnings("ignore")

    reader = PdfReader(str(args.input))
    patterns = [re.compile(expr, re.I) for expr in args.noise]
    lines = extract_lines(reader, patterns)
    heads = detect_running_heads(lines, len(reader.pages))
    lines = [line for line in lines if line not in heads]
    paragraphs = build_paragraphs(lines, args.min_paragraph_chars)

    header: list[str] = []
    if args.title:
        header += ["# " + args.title, "Title: " + args.title]
    if args.journal:
        header.append("Journal: " + args.journal)
    if args.topic:
        header.append("Topic: " + args.topic)

    content = "\n\n".join(header + paragraphs) + "\n"
    args.output.write_text(content, encoding="utf-8")
    print(
        f"pages={len(reader.pages)} paragraphs={len(paragraphs)} "
        f"chars={len(content)} -> {args.output}"
    )


if __name__ == "__main__":
    main()
