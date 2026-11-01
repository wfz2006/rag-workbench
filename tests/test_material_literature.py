"""Real text-layer PDF fixtures and isolated Docling contract tests, without models."""

import builtins
import hashlib
import io
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from reportlab.pdfgen import canvas

from rag_workbench.domain.errors import AppError
from rag_workbench.infrastructure.literature import parse_literature


def pdf_bytes(*pages: str) -> bytes:
    output = io.BytesIO()
    pdf = canvas.Canvas(output)
    for text in pages:
        if text:
            pdf.drawString(40, 780, text)
        else:
            # A visible graphical page has no text layer and needs OCR.
            pdf.rect(40, 100, 100, 100, fill=1)
        pdf.showPage()
    pdf.save()
    return output.getvalue()


def test_text_pdf_retains_both_pages_and_last_page_evidence(settings):
    data = pdf_bytes("SAMPLE A mobility 0.12", "LAST PAGE annealing at 120 C")
    parsed = parse_literature("paper.pdf", data, settings)
    assert parsed.parser == "text/pypdf"
    assert parsed.parsed_json is None
    assert [block.page for block in parsed.blocks] == [1, 2]
    assert "SAMPLE A mobility 0.12" in parsed.blocks[0].text
    assert "LAST PAGE annealing at 120 C" in parsed.blocks[-1].text
    assert all(block.document_id == hashlib.sha256(data).hexdigest() for block in parsed.blocks)
    assert len({block.id for block in parsed.blocks}) == 2
    assert [block.id for block in parse_literature("paper.pdf", data, settings).blocks] == [
        block.id for block in parsed.blocks
    ]


def test_scan_without_text_explicitly_requires_ocr(settings):
    with pytest.raises(AppError) as caught:
        parse_literature("scan.pdf", pdf_bytes(""), settings)
    assert caught.value.code == "ocr_required"


def test_docling_missing_dependency_does_not_download_or_fallback(settings, monkeypatch):
    original_import = builtins.__import__

    def without_docling(name, *args, **kwargs):
        if name == "docling.document_converter":
            raise ModuleNotFoundError("Docling deliberately absent")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_docling)
    with pytest.raises(AppError) as caught:
        parse_literature("scan.pdf", pdf_bytes(""), settings, parser_name="docling")
    assert caught.value.code == "docling_not_installed"


def test_docling_preserves_text_table_json_and_normalizes_coordinates(settings, monkeypatch):
    data = pdf_bytes("First source page", "Second source page")
    used_paths = []
    text_provenance = SimpleNamespace(
        page_no=1,
        bbox=SimpleNamespace(
            l=10, t=160, r=90, b=80, coord_origin=SimpleNamespace(value="BOTTOMLEFT"),
        ),
    )
    table_provenance = SimpleNamespace(
        page_no=2,
        bbox=SimpleNamespace(l=10, t=20, r=80, b=60, coord_origin="TOPLEFT"),
    )
    full_json = {
        "schema_name": "DoclingDocument",
        "texts": [{"text": "Sample A, annealed at 120 C", "prov": [{"page_no": 1}]}],
        "tables": [{"data": {"table_cells": [{"text": "0.12", "row": 1, "col": 1}]}}],
        "pictures": [{"caption": "器件结构", "self_ref": "#/pictures/0"}],
    }

    class Table:
        prov = [table_provenance]

        def export_to_dataframe(self):
            raise AssertionError("Table structure must not depend on a lossy dataframe export")

        def export_to_markdown(self, *, doc):
            assert doc is document
            return "| Sample | Mobility |\n| --- | --- |\n| A | 0.12 |"

    document = SimpleNamespace(
        pages={
            1: SimpleNamespace(size=SimpleNamespace(width=100, height=200)),
            2: SimpleNamespace(size=SimpleNamespace(width=200, height=100)),
        },
        iterate_items=lambda: iter([
            (SimpleNamespace(text="Sample A, annealed at 120 C", prov=[text_provenance]), 0),
            (Table(), 0),
            (SimpleNamespace(text="   ", prov=[]), 0),
        ]),
        export_to_dict=lambda: full_json,
    )

    class Converter:
        def convert(self, path):
            source = Path(path)
            assert source.read_bytes() == data
            used_paths.append(source)
            return SimpleNamespace(document=document)

    package = ModuleType("docling")
    package.__path__ = []
    converter_module = ModuleType("docling.document_converter")
    converter_module.DocumentConverter = Converter
    monkeypatch.setitem(sys.modules, "docling", package)
    monkeypatch.setitem(sys.modules, "docling.document_converter", converter_module)

    parsed = parse_literature("paper.pdf", data, settings, parser_name="docling")
    assert parsed.parser == "docling"
    assert [block.kind for block in parsed.blocks] == ["text", "table"]
    assert [block.page for block in parsed.blocks] == [1, 2]
    assert parsed.blocks[0].bbox == pytest.approx((0.1, 0.2, 0.9, 0.6))
    assert parsed.blocks[1].bbox == pytest.approx((0.05, 0.2, 0.4, 0.6))
    assert "| A | 0.12 |" in parsed.blocks[1].text
    assert json.loads(parsed.parsed_json) == full_json
    assert len(used_paths) == 1 and not used_paths[0].exists()


@pytest.mark.parametrize("name", ["paper.docx", "figure.png", "archive.zip"])
def test_material_parser_limits_supported_types(settings, name):
    with pytest.raises(AppError) as caught:
        parse_literature(name, b"not parsed", settings)
    assert caught.value.code == "material_file_type"


@pytest.mark.parametrize("name,parser_name", [("paper.pdf", "unknown"), ("paper.txt", "docling")])
def test_unsupported_parser_selection_is_explicit(settings, name, parser_name):
    with pytest.raises(AppError) as caught:
        parse_literature(name, b"plain text", settings, parser_name=parser_name)
    assert caught.value.code == "material_parser"


def test_docling_checks_page_limit_before_loading_models(settings, monkeypatch):
    original_import = builtins.__import__

    def reject_model_import(name, *args, **kwargs):
        if name == "docling.document_converter":
            raise AssertionError("Page limit must be checked before model loading")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_model_import)
    limited = settings.model_copy(update={"max_pdf_pages": 1})
    with pytest.raises(AppError) as caught:
        parse_literature("paper.pdf", pdf_bytes("page one", "page two"), limited, "docling")
    assert caught.value.code == "material_pdf_limits"


def test_large_text_is_segmented_without_dropping_tail(settings):
    text = "A" * 24010 + "LAST-SENTINEL"
    parsed = parse_literature("paper.md", text.encode(), settings)
    assert len(parsed.blocks) == 3
    assert "".join(block.text for block in parsed.blocks) == text
    assert parsed.blocks[-1].text.endswith("LAST-SENTINEL")
