"""Original-file integrity and actual local PDF page rendering."""

import builtins

import pytest
from PIL import Image

from rag_workbench.application.materials import MaterialsService
from rag_workbench.domain.errors import AppError
from rag_workbench.infrastructure.material_preview import (
    load_original,
    pdf_page_count,
    render_pdf_page,
)

from .test_material_literature import pdf_bytes


def test_original_read_checks_content_hash_and_missing_files(settings):
    service = MaterialsService(settings)
    data = pdf_bytes("Page one", "Page two")
    document = service.ingest("paper.pdf", data)
    assert load_original(service.repo, document) == data
    original = service.repo.originals / f"{document.id}{document.extension}"
    original.write_bytes(data + b"corrupt")
    with pytest.raises(AppError) as error:
        load_original(service.repo, document)
    assert error.value.code == "material_original_corrupt"
    original.unlink()
    with pytest.raises(AppError) as error:
        load_original(service.repo, document)
    assert error.value.code == "material_original_missing"


def test_renderer_returns_real_page_png_and_distinguishes_page_scale_and_document():
    import io

    pytest.importorskip("pypdfium2")
    first = pdf_bytes("ALPHA PAGE ONE", "BETA PAGE TWO")
    second = pdf_bytes("DIFFERENT PAGE")
    assert pdf_page_count(first) == 2
    one = render_pdf_page(first, 1)
    two = render_pdf_page(first, 2)
    large = render_pdf_page(first, 1, scale=1.5)
    other = render_pdf_page(second, 1)
    assert one.png.startswith(b"\x89PNG\r\n\x1a\n")
    assert one.png != two.png and one.png != other.png
    assert large.width > one.width
    with Image.open(io.BytesIO(one.png)) as image:
        assert image.size == (one.width, one.height)


@pytest.mark.parametrize("page,scale", [(0, 1), (3, 1), (1, 0), (1, float("nan"))])
def test_invalid_page_or_scale_is_local_error(page, scale):
    with pytest.raises(AppError):
        render_pdf_page(pdf_bytes("First", "Second"), page, scale=scale)


def test_missing_renderer_dependency_is_explicit(monkeypatch):
    original = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "pypdfium2":
            raise ModuleNotFoundError("test renderer absent")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(AppError) as error:
        render_pdf_page(pdf_bytes("Uncached dependency fixture"), 1)
    assert error.value.code == "material_preview_unavailable"


def test_corrupt_pdf_count_is_app_error():
    with pytest.raises(AppError) as error:
        pdf_page_count(b"%PDF- broken")
    assert error.value.code == "material_preview_pdf"


def test_huge_page_render_stays_inside_memory_budget():
    import io

    from reportlab.pdfgen import canvas

    from rag_workbench.infrastructure.material_preview import MAX_IMAGE_PIXELS, MAX_IMAGE_SIDE

    pytest.importorskip("pypdfium2")
    stream = io.BytesIO()
    pdf = canvas.Canvas(stream, pagesize=(10000, 8000))
    pdf.drawString(100, 100, "SYNTHETIC LARGE PAGE")
    pdf.save()
    rendered = render_pdf_page(stream.getvalue(), 1, scale=3)
    assert max(rendered.width, rendered.height) <= MAX_IMAGE_SIDE
    assert rendered.width * rendered.height <= MAX_IMAGE_PIXELS


def test_pdfium_open_render_and_close_are_serialized(monkeypatch):
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    pypdfium2 = pytest.importorskip("pypdfium2")

    active, maximum = 0, 0
    guard = threading.Lock()
    original = pypdfium2.PdfDocument

    def traced_document(*args, **kwargs):
        nonlocal active, maximum
        document = original(*args, **kwargs)
        close = document.close
        with guard:
            active += 1
            maximum = max(maximum, active)

        def closed():
            nonlocal active
            close()
            with guard:
                active -= 1

        document.close = closed
        time.sleep(.02)
        return document

    monkeypatch.setattr(pypdfium2, "PdfDocument", traced_document)
    files = [pdf_bytes(f"THREAD SAFETY {index}") for index in range(3)]
    with ThreadPoolExecutor(max_workers=3) as executor:
        list(executor.map(lambda data: render_pdf_page(data, 1), files))
    assert maximum == 1 and active == 0
