"""Build the reviewed Santi corpus from a local PDF. The source is read-only."""

import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

from pypdf import PdfReader

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "santi_pdf"
CJK = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
CJK_SPACE = re.compile(
    rf"(?<=[{CJK}]) (?=[{CJK}，。！？；：、“”‘’（）《》〈〉…—·])|"
    rf"(?<=[，。！？；：、“”‘’（）《》〈〉…—·]) (?=[{CJK}])"
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_relative(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not value:
        raise ValueError(f"Unsafe relative path: {value}")
    return path


def read_source(manifest_path: Path, pdf: Path) -> dict:
    rows = list(csv.DictReader(manifest_path.open(encoding="utf-8-sig", newline="")))
    selected = [row for row in rows if row["status"] == "selected"]
    if len(selected) != 1 or selected[0]["source_id"] != SOURCE_ID:
        raise ValueError("Manifest must contain exactly one selected santi_pdf source")
    row = selected[0]
    if Path(row["input_path"]).resolve() != pdf.resolve():
        raise ValueError("Manifest input_path does not match --pdf")
    if sha256_file(pdf) != row["input_sha256"] or row["original_sha256"] != row["input_sha256"]:
        raise ValueError("Source hash changed")
    return row


def read_segments(path: Path) -> list[dict]:
    segments, ids = [], set()
    for raw in csv.DictReader(path.open(encoding="utf-8-sig", newline="")):
        row = {key: value.strip() for key, value in raw.items()}
        if not re.fullmatch(r"[a-z0-9_]{1,40}", row["segment_id"]):
            raise ValueError(f"Invalid segment_id: {row['segment_id']}")
        if row["segment_id"] in ids:
            raise ValueError(f"Duplicate segment_id: {row['segment_id']}")
        ids.add(row["segment_id"])
        start, end, split = int(row["page_start"]), int(row["page_end"]), int(row["split_pages"])
        if start < 1 or end < start or split < 0:
            raise ValueError(f"Invalid page range: {row['segment_id']}")
        if not row["book"] or not row["title"] or not row["expected_heading"]:
            raise ValueError(f"Missing metadata: {row['segment_id']}")
        row.update(page_start=start, page_end=end, split_pages=split)
        segments.append(row)
    if not segments:
        raise ValueError("Empty segments.csv")
    return segments


def extract_pages(pdf: Path, expected_pages: int) -> tuple[list[str], str]:
    tool = shutil.which("pdftotext")
    if tool is None:
        raise RuntimeError("pdftotext is required (poppler)")
    completed = subprocess.run(
        [tool, "-enc", "UTF-8", str(pdf), "-"],
        capture_output=True,
        timeout=300,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.decode("utf-8", "replace") or "pdftotext failed")
    try:
        text = completed.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError("pdftotext did not return UTF-8") from exc
    pages = text.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    if len(pages) != expected_pages:
        raise RuntimeError(f"Extracted {len(pages)} pages, expected {expected_pages}")
    version = subprocess.run([tool, "-v"], capture_output=True, text=True, timeout=30, check=False)
    label = (version.stdout or version.stderr).splitlines()[0].strip()
    return pages, label


def clean_page(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = "".join(char for char in text if char in "\n\t" or unicodedata.category(char) != "Cc")
    lines = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t]+", " ", line).strip()
        if not line:
            continue
        line = CJK_SPACE.sub("", line)
        lines.append(line)
    return "\n".join(lines).strip()


def normalize_heading(text: str) -> str:
    return clean_page(text).splitlines()[0] if clean_page(text) else ""


def slug(text: str, limit: int = 36) -> str:
    text = text.replace("·", "_")
    text = re.sub(r"[^\w\u3400-\u9fff-]+", "_", text, flags=re.UNICODE)
    text = re.sub(r"_+", "_", text).strip("_")
    return text[:limit].rstrip("_") or "doc"


def page_ranges(start: int, end: int, split_pages: int) -> list[tuple[int, int]]:
    if split_pages <= 0 or end - start + 1 <= split_pages:
        return [(start, end)]
    ranges = []
    cursor = start
    while cursor <= end:
        stop = min(cursor + split_pages - 1, end)
        ranges.append((cursor, stop))
        cursor = stop + 1
    return ranges


def check_paths(pdf: Path, corpus: Path, generated_manifest: Path, report: Path) -> None:
    if corpus.is_symlink() or corpus.is_junction():
        raise ValueError("Corpus path must not be a link or junction")
    if corpus == pdf or pdf in corpus.parents or corpus in pdf.parents:
        raise ValueError("Source/corpus overlap")
    for path in (generated_manifest, report):
        if path in {pdf, corpus} or corpus in path.parents or pdf in path.parents:
            raise ValueError(f"Output overlaps source or corpus: {path}")
        if path.is_symlink() or path.is_junction():
            raise ValueError(f"Output must not be a link or junction: {path}")


def build(
    pdf: Path,
    manifest_path: Path,
    segments_path: Path,
    corpus: Path,
    generated_manifest: Path,
    report_path: Path,
    *,
    replace: bool,
) -> dict:
    pdf, corpus = pdf.resolve(), corpus.resolve()
    generated_manifest, report_path = generated_manifest.resolve(), report_path.resolve()
    check_paths(pdf, corpus, generated_manifest, report_path)
    source = read_source(manifest_path, pdf)
    source_hash_before = sha256_file(pdf)
    page_count = len(PdfReader(pdf).pages)
    if page_count != int(source["page_end"]):
        raise ValueError("Manifest page_end does not match PDF page count")
    segments = read_segments(segments_path)
    pages, tool_version = extract_pages(pdf, page_count)

    covered: dict[int, str] = {}
    documents: list[dict] = []
    warnings: list[str] = []
    pending: dict[Path, bytes] = {}

    for segment in segments:
        start, end = segment["page_start"], segment["page_end"]
        if start < 1 or end > page_count:
            raise ValueError(f"Segment outside PDF: {segment['segment_id']}")
        for page in range(start, end + 1):
            if page in covered:
                raise ValueError(
                    f"Page {page} covered by both {covered[page]} and {segment['segment_id']}"
                )
            covered[page] = segment["segment_id"]
        clean = {page: clean_page(pages[page - 1]) for page in range(start, end + 1)}
        if not clean[start]:
            raise ValueError(f"Empty start page {start} in {segment['segment_id']}")
        actual = normalize_heading(clean[start])
        expected = normalize_heading(segment["expected_heading"])
        if actual != expected:
            raise ValueError(
                f"Heading mismatch at page {start} ({segment['segment_id']}): "
                f"expected {expected!r}, got {actual!r}"
            )
        for index, (chunk_start, chunk_end) in enumerate(
            page_ranges(start, end, segment["split_pages"]), 1
        ):
            if not clean[chunk_start]:
                raise ValueError(f"Empty page {chunk_start} in {segment['segment_id']}")
            suffix = (
                f"p{chunk_start:04d}"
                if chunk_start == chunk_end
                else f"p{chunk_start:04d}-{chunk_end:04d}"
            )
            name = f"santi_{segment['segment_id']}_{slug(segment['title'])}_{suffix}.md"
            header = (
                f"{segment['book']} {segment['title']}（物理页 {chunk_start}-{chunk_end}）\n"
                "来源：三体全集 PDF；页首〔pNNNN〕为整理标记，不是原文。\n"
            )
            body = "\n\n".join(
                f"〔p{page:04d}〕\n{clean[page]}" for page in range(chunk_start, chunk_end + 1)
            )
            content = (header + "\n" + body + "\n").encode("utf-8")
            target = (corpus / name).resolve()
            if corpus not in target.parents:
                raise ValueError(f"Output escapes corpus: {name}")
            if target.name in pending:
                raise ValueError(f"Duplicate output name: {name}")
            pending[target.name] = content
            documents.append(
                {
                    "path": name,
                    "sha256": sha256_bytes(content),
                    "bytes": len(content),
                    "chars": len(content.decode("utf-8")),
                    "source_id": SOURCE_ID,
                    "book": segment["book"],
                    "kind": segment["kind"],
                    "title": segment["title"],
                    "page_start": chunk_start,
                    "page_end": chunk_end,
                    "part": index,
                }
            )
        short = [page for page in range(start, end + 1) if 0 < len(clean[page]) < 60]
        if short:
            warnings.append(f"{segment['segment_id']} short pages: {short}")
    if sha256_file(pdf) != source_hash_before:
        raise ValueError("Source changed during build")

    excluded_pages = sorted(set(range(1, page_count + 1)) - set(covered))
    corpus.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".santi-staging-", dir=corpus.parent))
    backup = None
    try:
        for name, content in pending.items():
            (staging / name).write_bytes(content)
        if corpus.exists():
            if any(corpus.iterdir()):
                if not replace:
                    raise ValueError(
                        "Corpus already exists with content; pass --replace after review"
                    )
                backup_dir = PROJECT_ROOT / "artifacts" / "santi" / "corpus-backups"
                backup_dir.mkdir(parents=True, exist_ok=True)
                backup = backup_dir / ("corpus-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S"))
                if backup.exists():
                    raise ValueError(f"Backup already exists: {backup}")
                corpus.rename(backup)
            else:
                corpus.rmdir()
        staging.rename(corpus)
    except Exception:
        if backup is not None and backup.exists() and not corpus.exists():
            backup.rename(corpus)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)

    generated_manifest.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    generated_manifest.write_text(
        "\n".join(json.dumps(document, ensure_ascii=False) for document in documents) + "\n",
        encoding="utf-8",
    )
    report = {
        "source_id": SOURCE_ID,
        "source_path": str(pdf),
        "source_sha256": source_hash_before,
        "source_pages": page_count,
        "source_chars": sum(len(page) for page in pages),
        "tool": tool_version,
        "segments": len(segments),
        "documents": len(documents),
        "corpus_chars": sum(document["chars"] for document in documents),
        "excluded_pages": excluded_pages,
        "warnings": warnings,
        "generated_at": datetime.now(UTC).isoformat(),
        "segments_sha256": sha256_file(segments_path),
        "generated_manifest_sha256": sha256_file(generated_manifest),
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--segments", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--generated-manifest", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    report = build(
        args.pdf,
        args.manifest,
        args.segments,
        args.corpus,
        args.generated_manifest,
        args.report,
        replace=args.replace,
    )
    print(json.dumps({"documents": report["documents"], "chars": report["corpus_chars"]}))


if __name__ == "__main__":
    main()
