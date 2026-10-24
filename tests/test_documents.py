import io

import pytest
from reportlab.pdfgen import canvas

from rag_workbench.config import Settings
from rag_workbench.domain.errors import AppError
from rag_workbench.infrastructure.chunking import split_sections
from rag_workbench.infrastructure.parsing import parse_document


def test_text_cleaning_and_unicode():
    sections = parse_document("材料.MD", "# 标题\r\n正文\x01。".encode(), Settings())
    assert sections[0].text == "# 标题\n正文。"


@pytest.mark.parametrize(
    "name,data",
    [
        ("empty.txt", b"  \r\n"),
        ("bad.pdf", b"not PDF"),
        ("binary.txt", b"\x00\xff\x00"),
        ("file.exe", b"hello"),
        ("fake.txt", b"%PDF-1.7 hello"),
        ("fake.md", b"PK\x03\x04zip"),
    ],
)
def test_invalid_documents_rejected(name, data):
    with pytest.raises(AppError):
        parse_document(name, data, Settings())


def test_size_limit():
    with pytest.raises(AppError, match="大小"):
        parse_document("a.txt", b"x" * (1024 * 1024 + 1), Settings(max_upload_mb=1))


def test_pdf_page_source_and_scanned_rejection():
    stream = io.BytesIO()
    pdf = canvas.Canvas(stream)
    pdf.drawString(40, 700, "Page one evidence")
    pdf.showPage()
    pdf.drawString(40, 700, "Page two evidence")
    pdf.save()
    pages = parse_document("source.pdf", stream.getvalue(), Settings())
    assert [(s.page, s.text) for s in pages] == [
        (1, "Page one evidence"),
        (2, "Page two evidence"),
    ]
    stream = io.BytesIO()
    pdf = canvas.Canvas(stream)
    pdf.showPage()
    pdf.save()
    with pytest.raises(AppError, match="OCR"):
        parse_document("scan.pdf", stream.getvalue(), Settings())


def test_chunk_boundaries_overlap_stability_and_coverage():
    sections = parse_document("a.txt", ("甲乙丙丁戊己庚辛壬癸" * 5).encode(), Settings())
    chunks = split_sections("a" * 64, "a.txt", sections, 20, 4)
    assert [c.start for c in chunks] == [0, 16, 32]
    assert [c.end for c in chunks] == [20, 36, 50]
    assert chunks[0].text[-4:] == chunks[1].text[:4]
    assert len({c.id for c in chunks}) == 3
    assert [c.id for c in chunks] == [
        c.id for c in split_sections("a" * 64, "a.txt", sections, 20, 4)
    ]


def test_chunk_invalid_configuration():
    with pytest.raises(ValueError):
        Settings(chunk_size=100, chunk_overlap=100)
    with pytest.raises(ValueError):
        split_sections("doc", "a", [], 0, 0)


def test_chunking_keeps_minimum_stride_for_near_size_overlap():
    sections = parse_document("a.txt", ("资料" * 500).encode(), Settings())
    chunks = split_sections("a" * 64, "a.txt", sections, 20, 19)
    assert chunks
    assert len(chunks) <= 250
