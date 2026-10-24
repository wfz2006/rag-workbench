"""Build an explicitly reviewed local corpus. Never discovers or deletes source data."""

import argparse
import csv
import hashlib
import json
import re
import unicodedata
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_text(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding).replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Unrecognized encoding: {path.name}")


def _safe_relative(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not value:
        raise ValueError("Expected a safe relative path")
    return path


def _normalize_radicals(text: str) -> str:
    # PDF compatibility radicals are letters, not arithmetic or code punctuation.
    return "".join(
        unicodedata.normalize("NFKC", c) if 0x2F00 <= ord(c) <= 0x2FD5 else c for c in text
    )


def _units(row: dict, text: str) -> list[tuple[int | None, str]]:
    if row["kind"] == "lesson":
        pages = {
            int(n): body.strip().removesuffix("---").strip()
            for n, body in re.findall(
                r"^## 第 (\d+) 页\s*\n(.*?)(?=^## 第 \d+ 页|\Z)", text, re.M | re.S
            )
        }
        start, end = int(row["page_start"]), int(row["page_end"])
        if start < 1 or end < start or any(p not in pages for p in range(start, end + 1)):
            raise ValueError("Missing or invalid source page")
        units = [(p, pages[p]) for p in range(start, end + 1)]
    elif row["kind"] == "problem":
        marker = f"原始来源：`{row['original_path']}`"
        if text.count(marker) != 1:
            raise ValueError("Problem source marker missing or ambiguous")
        body = text.split(marker, 1)[1].split("\n## ", 1)[0].strip()
        body = body.removesuffix("---").strip()
        units = [(None, body)]
    elif row["kind"] == "example":
        units = [(None, text.strip())]
    else:
        raise ValueError("Unknown corpus kind")
    output = []
    for page, body in units:
        for field, after in (("start_marker", True), ("cut_before", False)):
            marker = row.get(field, "")
            if marker:
                if marker not in body:
                    raise ValueError(f"Missing reviewed {field}")
                body = body[body.index(marker) :] if after else body[: body.index(marker)]
        body = _normalize_radicals(body).strip()
        if not body or "本页未提取到可搜索文本" in body:
            raise ValueError("Empty or unextractable reviewed unit")
        output.append((page, body))
    return output


def build_pack(
    source_root: Path, manifest_path: Path, output_root: Path, *, report_path: Path | None = None
) -> dict:
    source_root, output_root = source_root.resolve(), output_root.resolve()
    if (
        source_root == output_root
        or source_root in output_root.parents
        or output_root in source_root.parents
    ):
        raise ValueError("Source/output overlap")
    report = report_path.resolve() if report_path else None
    if report is not None and (
        report == manifest_path.resolve()
        or report in (source_root, output_root)
        or source_root in report.parents
        or output_root in report.parents
    ):
        raise ValueError("Unsafe report output overlaps inputs or corpus")
    rows = list(csv.DictReader(manifest_path.open(encoding="utf-8-sig", newline="")))
    pending, seen_ids, seen_units = {}, set(), set()
    documents = []
    for row in rows:
        if row["status"] != "selected":
            continue
        identity = row["source_id"]
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", identity):
            raise ValueError("Invalid source_id")
        if identity in seen_ids:
            raise ValueError("duplicate source_id")
        seen_ids.add(identity)
        source = (source_root / _safe_relative(row["input_path"])).resolve()
        if source_root not in source.parents:
            raise ValueError("Input escapes source root")
        if sha256(source) != row["source_sha256"]:
            raise ValueError("Source hash changed")
        original = Path(row["original_path"]).resolve()
        if report in {source, original}:
            raise ValueError("Unsafe report output overlaps source original")
        if sha256(original) != row["original_sha256"]:
            raise ValueError("Original hash changed")
        if original == output_root or output_root in original.parents:
            raise ValueError("Original/output overlap")
        folder = _safe_relative(row["prepared_path"])
        expected = {"lesson": "lessons", "problem": "problems", "example": "examples"}
        if str(folder) != expected.get(row["kind"]):
            raise ValueError("Folder is not a corpus whitelist member")
        for page, body in _units(row, read_text(source)):
            key = (str(source), page, row.get("start_marker", ""), row.get("cut_before", ""))
            if row["kind"] == "problem":
                key = (str(original), None, "", "")
            if key in seen_units:
                raise ValueError("duplicate source unit")
            seen_units.add(key)
            title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", row["title"])[:70]
            suffix = f"_p{page:03d}" if page is not None else ""
            relative = folder / f"{identity}_{title}{suffix}.md"
            target = (output_root / relative).resolve()
            if output_root not in target.parents or target in pending:
                raise ValueError("Output escapes corpus or collides")
            header = f"# {row['title']}\n\n来源标识：{identity}\n原件：{original.name}\n"
            if page is not None:
                header += f"原件物理页：{page}\n"
            if row["kind"] == "problem":
                header += (
                    f"来源目录标注：{row.get('edition', '')} {row['group']}；"
                    f"题号：{row['problem_id']}\n"
                )
            if row["kind"] == "example":
                header += "验证状态：原资料参考源码，未编译、未验证为标准答案。\n"
                if row.get("editorial_note"):
                    header += (
                        "\n整理说明（根据源码逐句整理，非课件原文）："
                        + row["editorial_note"]
                        + "\n"
                    )
                body = f"```cpp\n{body}\n```"
            content = (header + "\n" + body + "\n").encode("utf-8")
            if target.exists() and target.read_bytes() != content:
                raise ValueError(f"Existing output differs: {relative}")
            pending[target] = content
            documents.append(
                {
                    "source_id": identity,
                    "path": relative.as_posix(),
                    "page": page,
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            )
    # Validate the complete plan before writing a single file.
    for target, data in pending.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return {"sources": len(seen_ids), "documents": documents}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = build_pack(args.source_root, args.manifest, args.output, report_path=args.report)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"sources": report["sources"], "documents": len(report["documents"])}))


if __name__ == "__main__":
    main()
