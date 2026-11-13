import io
import zipfile
from itertools import pairwise

import pytest
from reportlab.pdfgen import canvas

from rag_workbench.config import Settings
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.models import Section
from rag_workbench.infrastructure.chunking import split_sections
from rag_workbench.infrastructure.parsing import _pdf_page_text, clean_pdf_text, parse_document


def test_pdf_cleanup_keeps_visible_hyphens_and_scientific_notation():
    raw = (
        "eﬃcient\u200b ﬁlms\xa0on field-\neffect\n"
        "with 2.3 × 10⁻⁷ cm² V−1 s−1 and 10−23416.\n"
        "Downloaded from pubs.\u200bacs.\u200borg/paper?getftrtoken=opaque by Test University\n"
        "Use literal /C2 and sample PY3-\nOTS."
    )
    assert clean_pdf_text(raw) == (
        "efficient films on field-effect\n"
        "with 2.3 × 10⁻⁷ cm² V−1 s−1 and 10−23416.\n"
        "Use literal /C2 and sample PY3-OTS."
    )


def test_pdf_cleanup_joins_hyphenation_supported_by_the_same_page():
    text = "High mobility shows strong crys-\ntallinity. The crystallinity is reported.\n"
    text += "Field-\neffect mobility was measured."
    assert clean_pdf_text(text) == (
        "High mobility shows strong crystallinity. The crystallinity is reported.\n"
        "Field-effect mobility was measured."
    )


def test_pdf_private_glyphs_require_a_matching_embedded_font(monkeypatch):
    from hashlib import sha256

    from pypdf import PageObject
    from pypdf.generic import (
        ArrayObject,
        DecodedStreamObject,
        DictionaryObject,
        NameObject,
        NumberObject,
    )

    from rag_workbench.infrastructure import parsing

    embedded = DecodedStreamObject()
    embedded.set_data(b"fixture-font-program")
    symbol_font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Test+AdvP4C4E74"),
        NameObject("/Encoding"): DictionaryObject({
            NameObject("/Differences"): ArrayObject([NumberObject(65), NameObject("/C0")]),
        }),
        NameObject("/FontDescriptor"): DictionaryObject({NameObject("/FontFile3"): embedded}),
    })
    plain_font = DictionaryObject({
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    page = PageObject.create_blank_page(width=100, height=100)
    page[NameObject("/Resources")][NameObject("/Font")] = DictionaryObject({
        NameObject("/S"): symbol_font, NameObject("/P"): plain_font,
    })
    content = DecodedStreamObject()
    content.set_data(b"BT 20 20 Td /S 10 Tf (A) Tj /P 10 Tf (20 and literal /C0) Tj ET")
    page[NameObject("/Contents")] = content
    assert _pdf_page_text(page, {}) == "/C020 and literal /C0"
    monkeypatch.setitem(
        parsing._PDF_FONT_REPAIRS, sha256(embedded.get_data()).hexdigest(), {"/C0": "−"},
    )
    assert _pdf_page_text(page, {}) == "−20 and literal /C0"


def test_pdf_attested_span_repairs_keep_numeric_suffix_and_unknown_glyphs(monkeypatch):
    from hashlib import sha256

    from pypdf import PageObject
    from pypdf.generic import (
        ArrayObject,
        DecodedStreamObject,
        DictionaryObject,
        NameObject,
        NumberObject,
    )

    from rag_workbench.infrastructure import parsing

    embedded = DecodedStreamObject()
    embedded.set_data(b"fixture-symbol-font")
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Test+AdvP4C4E74"),
        NameObject("/Encoding"): DictionaryObject({
            NameObject("/Differences"): ArrayObject([
                NumberObject(65), NameObject("/C0"), NameObject("/C18"),
                NameObject("/C19"), NameObject("/C20"), NameObject("/onequarter"),
            ]),
        }),
        NameObject("/FontDescriptor"): DictionaryObject({NameObject("/FontFile3"): embedded}),
    })
    page = PageObject.create_blank_page(width=100, height=100)
    page[NameObject("/Resources")][NameObject("/Font")] = DictionaryObject({
        NameObject("/S"): font,
    })
    content = DecodedStreamObject()
    content.set_data(b"BT 20 20 Td /S 10 Tf (EA20 BC D A02) Tj ET")
    page[NameObject("/Contents")] = content
    assert _pdf_page_text(page, {}) == "¼/C020 /C18/C19 /C20 /C002"
    monkeypatch.setitem(
        parsing._PDF_FONT_REPAIRS, sha256(embedded.get_data()).hexdigest(),
        {"¼": "=", "/C0": "−", "/C18": "(", "/C19": ")"},
    )
    assert _pdf_page_text(page, {}) == "=−20 () /C20 −02"


def test_pdf_recovers_superscripts_from_raised_glyphs_not_flat_numbers():
    output = io.BytesIO()
    pdf = canvas.Canvas(output)
    text = pdf.beginText(40, 780)
    text.setFont("Helvetica", 10)
    text.textOut("flat 104; ratio 10")
    text.setFont("Helvetica", 7)
    text.setRise(4)
    text.textOut("4")
    text.setFont("Helvetica", 10)
    text.setRise(0)
    text.textOut("; unit cm")
    text.setFont("Helvetica", 7)
    text.setRise(4)
    text.textOut("2")
    text.setFont("Helvetica", 10)
    text.setRise(0)
    text.textOut(" V")
    text.setFont("Helvetica", 7)
    text.setRise(4)
    text.textOut("-")
    text.textOut("1")
    text.setFont("Helvetica", 10)
    text.setRise(0)
    text.textOut(" s")
    text.setFont("Helvetica", 7)
    text.setRise(4)
    text.textOut("-1")
    text.setFont("Helvetica", 10)
    text.setRise(0)
    text.textOut("; sample cm2; 10")
    text.setFont("Helvetica", 7)
    text.textOut("5")
    pdf.drawText(text)
    pdf.showPage()
    pdf.save()

    section, = parse_document("scientific.pdf", output.getvalue(), Settings(_env_file=None))
    assert section.text == "flat 104; ratio 10⁴; unit cm² V⁻¹ s⁻¹; sample cm2; 105"


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def make_docx(document_xml: str, extra: dict[str, str] | None = None) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
            'package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/>'
            "</Types>",
        )
        archive.writestr("word/document.xml", document_xml)
        for name, body in (extra or {}).items():
            archive.writestr(name, body)
    return stream.getvalue()


def make_epub(spine: list[tuple[str, str, str]]) -> bytes:
    manifest = "".join(
        f'<item id="{name}" href="{name}" media-type="application/xhtml+xml"'
        + (' properties="nav"' if "nav" in name else "")
        + "/>"
        for name, _, _ in spine
    )
    itemrefs = "".join(f'<itemref idref="{name}"/>' for name, _, _ in spine)
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
        f"<manifest>{manifest}</manifest><spine>{itemrefs}</spine></package>"
    )
    container = (
        '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:'
        'container" version="1.0"><rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", opf)
        for name, _, body in spine:
            archive.writestr(f"OEBPS/{name}", body)
    return stream.getvalue()


def test_docx_extracts_headings_paragraphs_and_tables():
    xml = (
        f'<?xml version="1.0"?><w:document xmlns:w="{W}"><w:body>'
        "<w:p><w:pPr><w:pStyle w:val=\"Heading1\"/></w:pPr>"
        "<w:r><w:t>第一章</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>正文甲</w:t></w:r></w:p>"
        "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>A</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>B</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
        "</w:body></w:document>"
    )
    text = parse_document("guide.docx", make_docx(xml), Settings())[0].text
    assert "# 第一章" in text
    assert "正文甲" in text
    assert "A | B" in text


def test_docx_extracts_content_controls():
    xml = (
        f'<?xml version="1.0"?><w:document xmlns:w="{W}"><w:body>'
        "<w:sdt><w:sdtContent><w:p><w:r><w:t>受控内容</w:t></w:r></w:p>"
        "</w:sdtContent></w:sdt></w:body></w:document>"
    )
    text = parse_document("control.docx", make_docx(xml), Settings())[0].text
    assert "受控内容" in text


def test_epub_follows_spine_order_and_skips_navigation():
    epub = make_epub(
        [
            ("ch1.xhtml", "", "<html><body><h1>第一章</h1><p>甲</p></body></html>"),
            ("nav.xhtml", "", "<html><body><nav>目录内容</nav></body></html>"),
            ("ch2.xhtml", "", "<html><body><h1>第二章</h1><p>乙</p></body></html>"),
        ]
    )
    sections = parse_document("book.epub", epub, Settings())
    text = "\n".join(section.text for section in sections)
    assert text.index("第一章") < text.index("第二章")
    assert "目录内容" not in text


def test_html_extracts_content_and_skips_scripts_nav_and_head():
    html = (
        "<html><head><title>页面标题</title><script>bad()</script></head><body>"
        "<nav>导航内容</nav><h1>正文标题</h1><p>Hello <b>world</b></p>"
        "<table><tr><td>A</td><td>B</td></tr></table></body></html>"
    ).encode()
    text = parse_document("page.html", html, Settings())[0].text
    assert "正文标题" in text
    assert "Hello world" in text
    assert "A | B" in text
    assert "bad()" not in text
    assert "导航内容" not in text


def test_zip_package_rejects_path_traversal():
    body = make_docx(
        f'<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>正文</w:t></w:r></w:p></w:body>'
        "</w:document>",
        {"../outside.txt": "escape"},
    )
    with pytest.raises(AppError) as caught:
        parse_document("guide.docx", body, Settings())
    assert caught.value.code == "unsafe_package"


def test_new_formats_reject_wrong_content_and_unsupported_doc():
    with pytest.raises(AppError):
        parse_document("guide.docx", b"not a zip", Settings())
    with pytest.raises(AppError):
        parse_document("book.epub", b"PK\x03\x04broken", Settings())
    with pytest.raises(AppError):
        parse_document("legacy.doc", b"old binary", Settings())


def test_knowledge_service_ingests_new_formats(knowledge):
    document_xml = (
        f'<w:document xmlns:w="{W}"><w:body>'
        "<w:p><w:r><w:t>保管期限为三年。</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    html = "<html><body><p>接口支持主题切换。</p></body></html>".encode()
    results = knowledge.ingest_many(
        [("guide.docx", make_docx(document_xml)), ("page.html", html)]
    )
    assert [document.status for document in results] == ["ready", "ready"]
    assert knowledge.stats()["documents"] == 2


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


def test_english_pdf_chunks_prefer_sentences_and_keep_word_boundaries():
    page = "We tested three films. Received material exhibited repeatable transistor mobility."
    chunks = split_sections("doc", "paper.pdf", [Section(text=page, page=7)], 36, 7)

    assert chunks[0].text.rstrip() == "We tested three films."
    assert chunks[-1].end == len(page)
    for chunk in chunks:
        assert chunk.page == 7
        assert chunk.text == page[chunk.start : chunk.end]
        for offset in (chunk.start, chunk.end):
            assert not (0 < offset < len(page) and page[offset - 1:offset + 1].isalpha())
    assert all(left.end >= right.start for left, right in pairwise(chunks))
