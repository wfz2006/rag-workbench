import csv
import hashlib

import pytest

from scripts.prepare_lanqiao_pack import build_pack


def fixture_manifest(tmp_path, *, kind="lesson", text=None, **changes):
    source = tmp_path / "source"
    source.mkdir()
    content = text or "# 栈\n\n## 第 2 页\n栈只允许在一端操作。\n\n## 第 3 页\n后进先出。\n"
    original = source / "source.md"
    original.write_text(content, encoding="utf-8")
    row = dict(
        source_id="stack",
        kind=kind,
        topic="栈",
        title="栈",
        input_path="source.md",
        original_path=str(original),
        source_sha256=hashlib.sha256(original.read_bytes()).hexdigest(),
        original_sha256=hashlib.sha256(original.read_bytes()).hexdigest(),
        prepared_path="lessons",
        page_start="2",
        page_end="3",
        year="",
        group="",
        problem_id="",
        status="selected",
        notes="",
    )
    row.update(changes)
    manifest = tmp_path / "manifest.csv"
    with manifest.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=row)
        writer.writeheader()
        writer.writerow(row)
    return source, manifest, row


def test_pages_hashes_and_repeatable_build(tmp_path):
    source, manifest, _ = fixture_manifest(tmp_path)
    output = tmp_path / "pack"
    report = build_pack(source, manifest, output)
    assert len(report["documents"]) == 2
    path = output / report["documents"][0]["path"]
    assert "p002" in path.name
    assert "原件物理页：2" in path.read_text(encoding="utf-8")
    before = path.read_bytes()
    assert build_pack(source, manifest, output) == report
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "change",
    [
        {"input_path": "missing.md"},
        {"input_path": "../outside.md"},
        {"page_start": "99", "page_end": "99"},
        {"source_sha256": "0" * 64},
        {"prepared_path": "../escape"},
        {"source_id": "../escape"},
    ],
)
def test_invalid_source_fails_without_partial_output(tmp_path, change):
    source, manifest, _ = fixture_manifest(tmp_path, **change)
    with pytest.raises((ValueError, FileNotFoundError)):
        build_pack(source, manifest, tmp_path / "pack")
    assert not (tmp_path / "pack").exists()


def test_refuses_source_overlap_and_changed_output(tmp_path):
    source, manifest, _ = fixture_manifest(tmp_path)
    with pytest.raises(ValueError, match="overlap"):
        build_pack(source, manifest, source / "output")
    out = tmp_path / "pack"
    result = build_pack(source, manifest, out)
    target = out / result["documents"][0]["path"]
    target.write_text("manual edit", encoding="utf-8")
    with pytest.raises(ValueError, match="differs"):
        build_pack(source, manifest, out)
    assert target.read_text() == "manual edit"


@pytest.mark.parametrize("report_target", ["manifest", "source"])
def test_report_cannot_overwrite_inputs(tmp_path, report_target):
    source, manifest, _ = fixture_manifest(tmp_path)
    report = manifest if report_target == "manifest" else source / "source.md"
    original = report.read_bytes()
    with pytest.raises(ValueError, match="report"):
        build_pack(source, manifest, tmp_path / "pack", report_path=report)
    assert report.read_bytes() == original
    assert not (tmp_path / "pack").exists()


def test_code_operators_and_fences_are_preserved(tmp_path):
    source, manifest, _ = fixture_manifest(
        tmp_path,
        kind="example",
        text="int pop() { return a[--n] <= 1 ? n++ : 0; }",
        prepared_path="examples",
        page_start="",
        page_end="",
    )
    report = build_pack(source, manifest, tmp_path / "pack")
    text = (tmp_path / "pack" / report["documents"][0]["path"]).read_text(encoding="utf-8")
    assert "```cpp" in text and "a[--n] <= 1 ? n++ : 0" in text
    assert "未编译" in text


def test_editorial_note_is_labelled_and_does_not_rewrite_code(tmp_path):
    source, manifest, _ = fixture_manifest(
        tmp_path,
        kind="example",
        text="int top() { return stk[n]; }",
        prepared_path="examples",
        page_start="",
        page_end="",
        editorial_note="top返回当前栈顶stk[n]。",
    )
    report = build_pack(source, manifest, tmp_path / "pack")
    text = (tmp_path / "pack" / report["documents"][0]["path"]).read_text(encoding="utf-8")
    assert "整理说明（根据源码逐句整理，非课件原文）" in text
    assert "top返回当前栈顶stk[n]。" in text
    assert "int top() { return stk[n]; }" in text


def test_duplicate_selection_rejected_and_excluded_not_imported(tmp_path):
    source, manifest, row = fixture_manifest(tmp_path)
    with manifest.open("a", encoding="utf-8", newline="") as f:
        csv.DictWriter(f, fieldnames=row).writerow(row)
    with pytest.raises(ValueError, match="duplicate"):
        build_pack(source, manifest, tmp_path / "pack")
    row["status"] = "excluded"
    with manifest.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=row)
        writer.writeheader()
        writer.writerow(row)
    assert build_pack(source, manifest, tmp_path / "pack")["documents"] == []


def test_problem_extraction_and_answer_separation(tmp_path):
    original = tmp_path / "problem.txt"
    original.write_text("test", encoding="utf-8")
    text = (
        f"# 汇编\n\n## 题目\n\n原始来源：`{original}`\n\n输入 n，输出 n。\n"
        "//******答案\nreturn n;\n\n---\n\n## 另一题\n不能混进来"
    )
    source, manifest, _ = fixture_manifest(
        tmp_path,
        kind="problem",
        text=text,
        original_path=str(original),
        original_sha256=hashlib.sha256(original.read_bytes()).hexdigest(),
        prepared_path="problems",
        page_start="",
        page_end="",
        cut_before="//******答案",
    )
    report = build_pack(source, manifest, tmp_path / "pack")
    output = (tmp_path / "pack" / report["documents"][0]["path"]).read_text(encoding="utf-8")
    assert "输入 n，输出 n" in output
    assert "return n" not in output and "不能混进来" not in output
