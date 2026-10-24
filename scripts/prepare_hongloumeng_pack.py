"""Build the reviewed Hongloumeng corpus from a local EPUB. The source is read-only."""

import argparse
import csv
import hashlib
import html
import json
import re
import shutil
import tempfile
import unicodedata
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree as ET

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "hongloumeng_epub"
BOOK_TITLE = "红楼梦"
BOOK_PUBLISHER = "人民文学出版社"
BOOK_EDITION = "2019年10月第77次印刷（2008年第3版）"
DIGITS = {
    "零": 0,
    "○": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
CN_NUM = r"[一二三四五六七八九十百零○〇]+"
CHAPTER_RE = re.compile(rf"^第({CN_NUM})回(?:(?:至|到)({CN_NUM})回)?")
NOTE_MARKERS = re.compile(r"\[\d+\]|〔[一二三四五六七八九十百零○〇]+〕")
PLAN_FIELDS = ["chapter_start", "chapter_end", "title", "source_href", "expected_heading"]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_chinese_number(text: str) -> int:
    value = text.strip().replace("○", "零").replace("〇", "零")
    if not value or any(char not in DIGITS and char not in "十百" for char in value):
        raise ValueError(f"Invalid Chinese number: {text!r}")
    if "百" in value:
        head, _, tail = value.partition("百")
        hundreds = _parse_small(head) if head else 1
        if not 1 <= hundreds <= 9:
            raise ValueError(f"Invalid hundreds digit: {text!r}")
        if tail.startswith("零"):
            tail = tail[1:]
        return hundreds * 100 + (_parse_small(tail) if tail else 0)
    if "十" in value:
        return _parse_small(value)
    number = 0
    for char in value:
        number = number * 10 + DIGITS[char]
    return number


def _parse_small(text: str) -> int:
    if not text:
        return 0
    if "十" in text:
        head, _, tail = text.partition("十")
        if head and head not in DIGITS:
            raise ValueError(f"Invalid tens digit: {text!r}")
        if len(tail) > 1 or (tail and tail not in DIGITS):
            raise ValueError(f"Invalid ones digit: {text!r}")
        return (DIGITS[head] if head else 1) * 10 + (DIGITS[tail] if tail else 0)
    if len(text) == 1 and text in DIGITS:
        return DIGITS[text]
    raise ValueError(f"Invalid Chinese number fragment: {text!r}")


def normalize_title(text: str) -> str:
    text = re.sub(r"<sup\b[^>]*>.*?</sup>", "", text, flags=re.S | re.I)
    text = re.sub(r"<img\b[^>]*>", "〔图〕", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\u3000", " ").replace("\xa0", " ").replace("■", "〔图〕")
    text = NOTE_MARKERS.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def title_key(text: str) -> str:
    text = normalize_title(text).replace("〔图〕", "")
    return re.sub(r"\s+", "", text)


def safe_href(value: str) -> str:
    value = value.split("#", 1)[0].strip()
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError(f"Unsafe EPUB href: {value!r}")
    return value


def read_source(manifest_path: Path, epub: Path) -> dict:
    rows = list(csv.DictReader(manifest_path.open(encoding="utf-8-sig", newline="")))
    selected = [row for row in rows if row.get("status") == "selected"]
    if len(selected) != 1 or selected[0].get("source_id") != SOURCE_ID:
        raise ValueError(f"Manifest must contain exactly one selected {SOURCE_ID} source")
    row = selected[0]
    if Path(row["input_path"]).resolve() != epub.resolve():
        raise ValueError("Manifest input_path does not match --epub")
    if sha256_file(epub) != row["input_sha256"] or row["original_sha256"] != row["input_sha256"]:
        raise ValueError("Source hash changed")
    return row


def parse_epub(epub: Path) -> dict:
    with zipfile.ZipFile(epub) as archive:
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfile = next(
            (
                element.attrib["full-path"]
                for element in container.iter()
                if local(element.tag) == "rootfile"
            ),
            None,
        )
        if not rootfile:
            raise ValueError("EPUB container has no rootfile")
        opf = ET.fromstring(archive.read(rootfile))
        metadata: dict[str, list[str]] = {}
        for element in opf.iter():
            key = local(element.tag)
            if key in {"title", "creator", "publisher", "date", "language", "identifier"}:
                value = (element.text or "").strip()
                if value:
                    metadata.setdefault(key, []).append(value)
        manifest = {}
        for element in opf.iter():
            if local(element.tag) != "item":
                continue
            item_id = element.attrib.get("id")
            href = element.attrib.get("href")
            if item_id and href:
                manifest[item_id] = {
                    "href": safe_href(href),
                    "media_type": element.attrib.get("media-type", ""),
                }
        spine = []
        for element in opf.iter():
            if local(element.tag) != "itemref":
                continue
            item = manifest.get(element.attrib.get("idref"))
            if item:
                spine.append(item)
        ncx_href = next(
            (
                item["href"]
                for item in manifest.values()
                if item["media_type"] == "application/x-dtbncx+xml"
            ),
            "toc.ncx",
        )
        ncx = ET.fromstring(archive.read(ncx_href))
        navpoints = []
        for element in ncx.iter():
            if local(element.tag) != "navPoint":
                continue
            label, src = "", ""
            for child in element.iter():
                if local(child.tag) == "text" and not label:
                    label = (child.text or "").strip()
                if local(child.tag) == "content" and not src:
                    src = child.attrib.get("src", "").strip()
            if label and src:
                navpoints.append({"label": label, "src": safe_href(src)})
        html_by_href = {}
        for item in spine:
            if item["media_type"] not in {"application/xhtml+xml", "text/html"}:
                continue
            html_by_href.setdefault(item["href"], archive.read(item["href"]))
    return {
        "metadata": metadata,
        "manifest": manifest,
        "spine": spine,
        "spine_hrefs": {item["href"] for item in spine},
        "navpoints": navpoints,
        "html_by_href": html_by_href,
    }


def derive_chapter_plan(
    navpoints: list[dict], spine_hrefs: set[str], *, expected_total: int | None = 120
) -> list[dict]:
    rows, seen = [], set()
    for navpoint in navpoints:
        title = normalize_title(navpoint["label"])
        match = CHAPTER_RE.match(title)
        if not match:
            continue
        start = parse_chinese_number(match.group(1))
        end = parse_chinese_number(match.group(2)) if match.group(2) else start
        href = safe_href(navpoint["src"])
        if href not in spine_hrefs:
            raise ValueError(f"Chapter href is not in EPUB spine: {href}")
        if href in seen:
            raise ValueError(f"Duplicate chapter href: {href}")
        seen.add(href)
        rows.append(
            {
                "chapter_start": start,
                "chapter_end": end,
                "title": title,
                "source_href": href,
                "expected_heading": title,
            }
        )
    if not rows:
        raise ValueError("No chapters found in EPUB navigation")
    expected = 1
    for row in rows:
        if row["chapter_start"] != expected:
            raise ValueError(
                f"Chapter plan is not contiguous at {row['title']!r}: "
                f"expected {expected}, got {row['chapter_start']}"
            )
        if row["chapter_end"] < row["chapter_start"]:
            raise ValueError(f"Invalid chapter range: {row['title']!r}")
        expected = row["chapter_end"] + 1
    if expected_total is not None and expected != expected_total + 1:
        raise ValueError(f"Chapter plan ends at {expected - 1}, expected {expected_total}")
    return rows


def read_chapter_plan(path: Path) -> list[dict]:
    rows = []
    for raw in csv.DictReader(path.open(encoding="utf-8-sig", newline="")):
        row = {key: (value or "").strip() for key, value in raw.items()}
        try:
            start, end = int(row["chapter_start"]), int(row["chapter_end"])
        except (KeyError, ValueError) as exc:
            raise ValueError(f"Invalid chapter row: {row}") from exc
        if start < 1 or end < start or not row["title"] or not row["source_href"]:
            raise ValueError(f"Missing chapter metadata: {row}")
        rows.append(
            {
                "chapter_start": start,
                "chapter_end": end,
                "title": row["title"],
                "source_href": safe_href(row["source_href"]),
                "expected_heading": row["expected_heading"] or row["title"],
            }
        )
    if not rows:
        raise ValueError("Empty chapters.csv")
    return rows


def compare_plan(planned: list[dict], derived: list[dict]) -> None:
    if len(planned) != len(derived):
        raise ValueError(f"chapters.csv has {len(planned)} rows, EPUB has {len(derived)}")
    for planned_row, derived_row in zip(planned, derived, strict=True):
        for key in PLAN_FIELDS:
            if planned_row[key] != derived_row[key]:
                raise ValueError(
                    f"chapters.csv differs from EPUB at chapter "
                    f"{derived_row['chapter_start']} ({key}): "
                    f"{planned_row[key]!r} != {derived_row[key]!r}"
                )


def extract_chapter(raw: str, expected_title: str) -> dict:
    headings = re.findall(r"<h1\b[^>]*>(.*?)</h1>", raw, flags=re.S | re.I)
    if not headings:
        raise ValueError(f"Chapter heading missing: {expected_title}")
    actual = normalize_title(headings[0])
    if title_key(actual) != title_key(expected_title):
        raise ValueError(f"Chapter heading mismatch: expected {expected_title!r}, got {actual!r}")
    paragraphs, notes, images = [], 0, 0
    for match in re.finditer(r"<p\b([^>]*)>(.*?)</p>", raw, flags=re.S | re.I):
        attrs, inner = match.group(1), match.group(2)
        class_match = re.search(r'class="([^"]*)"', attrs, flags=re.I)
        classes = class_match.group(1).split() if class_match else []
        if "note" in classes:
            notes += 1
            continue
        images += len(re.findall(r"<img\b", inner, flags=re.I))
        inner = re.sub(r"<sup\b[^>]*>.*?</sup>", "", inner, flags=re.S | re.I)
        inner = re.sub(r"<img\b[^>]*>", "〔图〕", inner, flags=re.I)
        inner = re.sub(r"<br\s*/?>", "\n", inner, flags=re.I)
        inner = re.sub(r"<[^>]+>", "", inner)
        text = html.unescape(inner)
        text = unicodedata.normalize("NFC", text).replace("\u3000", " ").replace("\xa0", " ")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n", text).strip()
        if text:
            paragraphs.append(text)
    if paragraphs and title_key(paragraphs[0]) == title_key(expected_title):
        paragraphs.pop(0)
    if not paragraphs:
        raise ValueError(f"Chapter has no narrative: {expected_title}")
    return {"paragraphs": paragraphs, "notes": notes, "images": images}


def slug(text: str, limit: int = 42) -> str:
    text = re.sub(r"[^\w\u3400-\u9fff○〇-]+", "_", text, flags=re.UNICODE)
    text = re.sub(r"_+", "_", text).strip("_")
    return text[:limit].rstrip("_") or "doc"


def chapter_filename(row: dict) -> str:
    start, end = row["chapter_start"], row["chapter_end"]
    span = f"{start:03d}" if start == end else f"{start:03d}-{end:03d}"
    return f"hlm_c{span}_{slug(row['title'])}.md"


def check_paths(epub: Path, corpus: Path, generated_manifest: Path, report: Path) -> None:
    if corpus.is_symlink() or corpus.is_junction():
        raise ValueError("Corpus path must not be a link or junction")
    if corpus == epub or epub in corpus.parents or corpus in epub.parents:
        raise ValueError("Source/corpus overlap")
    for path in (generated_manifest, report):
        if path in {epub, corpus} or corpus in path.parents or epub in path.parents:
            raise ValueError(f"Output overlaps source or corpus: {path}")
        if path.is_symlink() or path.is_junction():
            raise ValueError(f"Output must not be a link or junction: {path}")


def build_edition_doc(represented: int, notes: int, images: int) -> str:
    return (
        "# 版本与整理说明\n\n"
        "> 本文件为整理说明，不是《红楼梦》原文。\n\n"
        f"- 书名：{BOOK_TITLE}\n"
        "- 著者：曹雪芹\n"
        "- 续者：无名氏\n"
        "- 整理者：程伟元、高鹗\n"
        "- 校注：中国艺术研究院红楼梦研究所\n"
        f"- 出版：{BOOK_PUBLISHER}，{BOOK_EDITION}\n"
        "- 回数：120回。本书版权页署“曹雪芹著、无名氏续”；程伟元、高鹗为通行整理者。"
        "后40回的作者与整理情况在学界仍有讨论，回答时应区分“本书署名”和“学术推断”。\n"
        f"- 语料范围：收入120回正文；源 EPUB 将第十七、十八回合为一个文件，"
        f"因此语料为119个章节文档（覆盖{represented}回）。"
        "目录、版权页、封面、校注脚注和版式图片未入库。\n"
        f"- 校注脚注：共{notes}条，未入库；正文及回目标题中的版式缺字/字形图"
        f"以“〔图〕”标记，共{images}处。\n"
    )


def build(
    epub: Path,
    manifest_path: Path,
    chapters_path: Path,
    corpus: Path,
    generated_manifest: Path,
    report_path: Path,
    *,
    replace: bool,
) -> dict:
    epub, corpus = epub.resolve(), corpus.resolve()
    generated_manifest, report_path = generated_manifest.resolve(), report_path.resolve()
    check_paths(epub, corpus, generated_manifest, report_path)
    read_source(manifest_path, epub)
    if corpus.exists() and any(corpus.iterdir()) and not replace:
        raise ValueError("Corpus already exists with content; pass --replace after review")
    source_hash_before = sha256_file(epub)
    book = parse_epub(epub)
    derived = derive_chapter_plan(book["navpoints"], book["spine_hrefs"])
    planned = read_chapter_plan(chapters_path)
    compare_plan(planned, derived)

    pending: dict[str, bytes] = {}
    documents: list[dict] = []
    titles: set[str] = set()
    represented = notes = images = 0
    for row in planned:
        try:
            raw = book["html_by_href"][row["source_href"]].decode("utf-8")
        except KeyError as exc:
            raise ValueError(f"Chapter not found in EPUB spine: {row['source_href']}") from exc
        extracted = extract_chapter(raw, row["expected_heading"])
        key = title_key(row["title"])
        if key in titles:
            raise ValueError(f"Duplicate chapter title: {row['title']}")
        titles.add(key)
        span = (
            f"{row['chapter_start']:03d}"
            if row["chapter_start"] == row["chapter_end"]
            else f"{row['chapter_start']:03d}-{row['chapter_end']:03d}"
        )
        content = (
            f"# {row['title']}\n\n〔第{span}回〕\n\n" + "\n\n".join(extracted["paragraphs"]) + "\n"
        ).encode("utf-8")
        name = chapter_filename(row)
        if name in pending:
            raise ValueError(f"Duplicate output name: {name}")
        pending[name] = content
        documents.append(
            {
                "path": name,
                "sha256": sha256_bytes(content),
                "bytes": len(content),
                "chars": len(content.decode("utf-8")),
                "source_id": SOURCE_ID,
                "kind": "chapter",
                "chapter_start": row["chapter_start"],
                "chapter_end": row["chapter_end"],
                "title": row["title"],
                "source_href": row["source_href"],
            }
        )
        represented += row["chapter_end"] - row["chapter_start"] + 1
        notes += extracted["notes"]
        images += extracted["images"]
    if represented != 120:
        raise ValueError(f"Corpus covers {represented} chapters, expected 120")
    edition = build_edition_doc(represented, notes, images).encode("utf-8")
    pending["edition.md"] = edition
    documents.append(
        {
            "path": "edition.md",
            "sha256": sha256_bytes(edition),
            "bytes": len(edition),
            "chars": len(edition.decode("utf-8")),
            "source_id": SOURCE_ID,
            "kind": "edition",
            "chapter_start": 0,
            "chapter_end": 0,
            "title": "版本与整理说明",
            "source_href": "",
        }
    )
    if sha256_file(epub) != source_hash_before:
        raise ValueError("Source changed during build")

    corpus.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".hlm-staging-", dir=corpus.parent))
    backup = None
    try:
        for name, content in pending.items():
            (staging / name).write_bytes(content)
        if corpus.exists():
            if any(corpus.iterdir()):
                backup_dir = PROJECT_ROOT / "artifacts" / "hongloumeng" / "corpus-backups"
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
    excluded = [
        navpoint["label"]
        for navpoint in book["navpoints"]
        if not CHAPTER_RE.match(normalize_title(navpoint["label"]))
    ]
    report = {
        "source_id": SOURCE_ID,
        "source_path": str(epub),
        "source_sha256": source_hash_before,
        "source_metadata": book["metadata"],
        "source_documents": len(book["spine"]),
        "source_chapters": 120,
        "chapter_documents": len(planned),
        "edition_documents": 1,
        "documents": len(documents),
        "corpus_chars": sum(document["chars"] for document in documents),
        "notes_excluded": notes,
        "image_placeholders": images,
        "excluded_navpoints": excluded,
        "generated_at": datetime.now(UTC).isoformat(),
        "chapters_sha256": sha256_file(chapters_path),
        "generated_manifest_sha256": sha256_file(generated_manifest),
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def write_plan(epub: Path, manifest_path: Path, chapters_path: Path) -> list[dict]:
    read_source(manifest_path, epub)
    book = parse_epub(epub)
    rows = derive_chapter_plan(book["navpoints"], book["spine_hrefs"])
    chapters_path.parent.mkdir(parents=True, exist_ok=True)
    with chapters_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=PLAN_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row[key] for key in PLAN_FIELDS})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--epub", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--chapters", type=Path)
    parser.add_argument("--write-plan", type=Path)
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--generated-manifest", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    if args.write_plan:
        rows = write_plan(args.epub, args.manifest, args.write_plan)
        print(json.dumps({"chapter_documents": len(rows), "chapters": 120}, ensure_ascii=False))
        return
    if not (args.chapters and args.corpus and args.generated_manifest and args.report):
        parser.error("--chapters, --corpus, --generated-manifest and --report are required")
    report = build(
        args.epub,
        args.manifest,
        args.chapters,
        args.corpus,
        args.generated_manifest,
        args.report,
        replace=args.replace,
    )
    print(
        json.dumps(
            {
                "documents": report["documents"],
                "chapter_documents": report["chapter_documents"],
                "chars": report["corpus_chars"],
                "notes_excluded": report["notes_excluded"],
                "image_placeholders": report["image_placeholders"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
