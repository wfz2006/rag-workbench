import csv
import hashlib
import json

import pytest
from pydantic import ValidationError

from rag_workbench.application.packs import (
    PackBuilder,
    PackCatalog,
    PackSpec,
    load_pack_settings,
    pack_lint,
)
from rag_workbench.config import Settings
from rag_workbench.domain.errors import AppError

from .fakes import FakeEmbedder


def build_settings(tmp_path):
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "active",
        packs_dir=tmp_path / "packs",
        packs_data_dir=tmp_path / "data",
        chunk_size=100,
        chunk_overlap=10,
    )


def spec(**overrides):
    values = {
        "pack_id": "demo-pack",
        "title": "示例知识包",
        "description": "用于测试的本地资料包",
        "intended_use": "回答示例资料中的事实问题",
        "authority_scope": "仅限上传的两份示例资料",
        "language": "zh",
        "license_status": "internal_only",
        "chunk_size": 100,
        "chunk_overlap": 10,
        "top_k": 5,
        "similarity_threshold": 0.0,
    }
    values.update(overrides)
    return PackSpec(**values)


def read_manifest(pack_dir):
    with (pack_dir / "generated_manifest.jsonl").open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def test_build_creates_compliant_draft_pack_with_catalog_and_settings(tmp_path):
    settings = build_settings(tmp_path)
    builder = PackBuilder(settings, FakeEmbedder())
    result = builder.build(
        spec(),
        [
            ("guide.txt", "保管期限为三年。\n\n到期后移交档案室。".encode()),
            ("manual.md", "# 接口\n\n示例接口支持主题切换。".encode()),
        ],
    )

    assert result.pack_dir == (tmp_path / "packs" / "demo-pack").resolve()
    assert result.data_dir == (tmp_path / "data" / "demo-pack").resolve()
    assert [d.status for d in result.documents] == ["ready", "ready"]
    assert result.stats["documents"] == 2
    assert result.stats["chunks"] > 0
    assert result.stats["index_status"] == "ready"
    assert not result.lint.errors
    assert result.smoke["metrics"]["cases"] > 0
    assert result.smoke["metrics"]["document_hits"] == result.smoke["metrics"]["cases"]

    manifest = json.loads((result.pack_dir / "pack.json").read_text(encoding="utf-8"))
    assert manifest["pack_id"] == "demo-pack"
    assert manifest["status"] == "draft"
    assert manifest["license_status"] == "internal_only"
    assert manifest["distribution"] == "local_only"

    readme = (result.pack_dir / "README.md").read_text(encoding="utf-8")
    assert "自动草稿" in readme
    assert "未验收" in readme

    with (result.pack_dir / "source_manifest.csv").open(encoding="utf-8-sig", newline="") as stream:
        sources = list(csv.DictReader(stream))
    assert len(sources) == 2
    assert {row["license_status"] for row in sources} == {"internal_only"}
    for row in sources:
        original = result.pack_dir / row["original_path"]
        assert hashlib.sha256(original.read_bytes()).hexdigest() == row["original_sha256"]

    generated = read_manifest(result.pack_dir)
    assert len(generated) == 2
    assert {row["source_id"] for row in generated} == {row["source_id"] for row in sources}
    for row in generated:
        corpus = result.pack_dir / row["path"]
        assert corpus.exists()
        assert hashlib.sha256(corpus.read_bytes()).hexdigest() == row["sha256"]

    assert (result.pack_dir / "profile" / "profile.md").exists()
    assert (result.pack_dir / "review_notes.md").exists()
    assert (result.pack_dir / "eval" / "smoke.json").exists()

    catalog = PackCatalog(settings)
    entries = catalog.list()
    assert [entry.pack_id for entry in entries] == ["demo-pack"]
    assert entries[0].has_data
    assert catalog.get("demo-pack").title == "示例知识包"

    loaded = load_pack_settings("demo-pack", base=settings)
    assert loaded.data_dir == result.data_dir
    assert loaded.domain_profile_path == result.pack_dir / "profile" / "profile.md"
    assert loaded.allow_general is False

    again = pack_lint(result.pack_dir, result.data_dir)
    assert not again.errors


def test_build_deduplicates_content_and_reports_rejected_files(tmp_path):
    builder = PackBuilder(build_settings(tmp_path), FakeEmbedder())
    result = builder.build(
        spec(),
        [
            ("a.txt", "同一份资料。".encode()),
            ("copy.txt", "同一份资料。".encode()),
            ("bad.exe", b"binary"),
            ("empty.txt", b"   "),
        ],
    )
    assert len(result.documents) == 1
    assert len(result.failures) == 2
    assert {failure.error_code for failure in result.failures} == {
        "unsupported_type",
        "empty_document",
    }
    with (result.pack_dir / "source_manifest.csv").open(encoding="utf-8-sig", newline="") as stream:
        sources = list(csv.DictReader(stream))
    assert len(sources) == 1
    notes = (result.pack_dir / "review_notes.md").read_text(encoding="utf-8")
    assert "重复" in notes


def test_build_rejects_unsafe_or_existing_pack_id(tmp_path):
    settings = build_settings(tmp_path)
    builder = PackBuilder(settings, FakeEmbedder())
    with pytest.raises(ValidationError):
        spec(pack_id="../escape")
    unsafe = spec()
    unsafe.pack_id = "../escape"
    with pytest.raises(AppError) as caught:
        builder.build(unsafe, [("a.txt", b"data")])
    assert caught.value.code == "invalid_pack_id"

    builder.build(spec(), [("a.txt", "资料。".encode())])
    with pytest.raises(AppError) as caught:
        builder.build(spec(), [("b.txt", "另一份。".encode())])
    assert caught.value.code == "pack_exists"


def test_build_with_no_valid_sources_leaves_no_partial_pack(tmp_path):
    settings = build_settings(tmp_path)
    builder = PackBuilder(settings, FakeEmbedder())
    with pytest.raises(AppError) as caught:
        builder.build(spec(), [("bad.exe", b"binary")])
    assert caught.value.code == "no_valid_sources"
    assert not (tmp_path / "packs" / "demo-pack").exists()
    assert not (tmp_path / "data" / "demo-pack").exists()
    staging = tmp_path / "packs" / ".staging"
    assert not staging.exists() or not any(staging.iterdir())


def test_pack_lint_detects_tampered_corpus_source_and_readme(tmp_path):
    builder = PackBuilder(build_settings(tmp_path), FakeEmbedder())
    result = builder.build(spec(), [("a.txt", "资料正文。".encode())])
    corpus = next(result.pack_dir / row["path"] for row in read_manifest(result.pack_dir))
    corpus.write_text("被篡改的正文。", encoding="utf-8")
    lint = pack_lint(result.pack_dir, result.data_dir)
    assert lint.errors
    assert any("sha256" in check.message.lower() for check in lint.checks if check.status == "fail")

    builder = PackBuilder(build_settings(tmp_path / "second"), FakeEmbedder())
    result = builder.build(spec(), [("a.txt", "资料正文。".encode())])
    with (result.pack_dir / "source_manifest.csv").open(encoding="utf-8-sig", newline="") as stream:
        source = next(csv.DictReader(stream))
    (result.pack_dir / source["original_path"]).write_text("changed", encoding="utf-8")
    lint = pack_lint(result.pack_dir, result.data_dir)
    assert lint.errors

    builder = PackBuilder(build_settings(tmp_path / "third"), FakeEmbedder())
    result = builder.build(spec(), [("a.txt", "资料正文。".encode())])
    (result.pack_dir / "README.md").unlink()
    lint = pack_lint(result.pack_dir, result.data_dir)
    assert lint.errors


def test_pack_lint_rejects_secret_and_extra_corpus_file(tmp_path):
    builder = PackBuilder(build_settings(tmp_path), FakeEmbedder())
    result = builder.build(spec(), [("a.txt", "资料正文。".encode())])
    extra = result.pack_dir / "corpus" / "unlisted.md"
    extra.write_text("未登记正文", encoding="utf-8")
    lint = pack_lint(result.pack_dir, result.data_dir)
    assert any("未登记" in check.message for check in lint.checks if check.status == "fail")

    with (result.pack_dir / "README.md").open("a", encoding="utf-8") as stream:
        stream.write("\nsk-test-secret-value-1234567890\n")
    lint = pack_lint(
        result.pack_dir,
        result.data_dir,
        secret_values=["sk-test-secret-value-1234567890"],
    )
    assert any("密钥" in check.message for check in lint.checks if check.status == "fail")


def test_pack_storage_directories_are_not_pack_file_settings(tmp_path):
    from rag_workbench.config import load_file_settings

    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"packs_dir": "elsewhere"}), encoding="utf-8")
    with pytest.raises(AppError) as caught:
        load_file_settings(path, env_file=None)
    assert caught.value.code == "invalid_settings_file"
    assert not (tmp_path / "elsewhere").exists()


def test_catalog_ignores_invalid_manifests_and_directory_id_mismatch(tmp_path):
    settings = build_settings(tmp_path)
    root = tmp_path / "packs"
    root.mkdir()
    (root / "broken").mkdir()
    (root / "broken" / "pack.json").write_text("{broken", encoding="utf-8")
    (root / "renamed").mkdir()
    (root / "renamed" / "pack.json").write_text(
        json.dumps({"schema_version": 1, "pack_id": "different", "title": "outside",
                    "description": "", "intended_use": "", "authority_scope": "",
                    "language": "zh", "license_status": "unknown",
                    "distribution": "local_only", "status": "draft",
                    "settings_file": "settings.json", "profile": "profile/profile.md",
                    "source_manifest": "source_manifest.csv",
                    "generated_manifest": "generated_manifest.jsonl",
                    "review_notes": "review_notes.md", "eval_dir": "eval"}),
        encoding="utf-8",
    )

    catalog = PackCatalog(settings)
    assert catalog.list() == []
    assert catalog.warnings
