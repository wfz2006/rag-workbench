import hashlib
import io

import pytest
from pypdf import PdfWriter

from rag_workbench.application.digitized_curves import import_curve, parse_xy_csv
from rag_workbench.domain.digitized_curves import CurveSource
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.materials import EvidenceBlock, LiteratureDocument
from rag_workbench.infrastructure.material_repository import MaterialsRepository


def test_xy_import_preserves_order_and_optional_header():
    raw = b'\xef\xbb\xbf"Voltage (V)","Current (mA)"\r\n0,2e-3\r\n-0.1,-1\r\n0,4\r\n'
    assert parse_xy_csv(raw, has_header=True) == ((0., .002), (-.1, -1.), (0., 4.))
    assert parse_xy_csv(b"1,2\n\n3,4\n") == ((1., 2.), (3., 4.))


@pytest.mark.parametrize("raw", [b"1,2\n3,nan", b"1,2\n3,4,5", b'1,2\n"3,4', b"1,2\n3,1e999"])
def test_invalid_row_rejects_entire_csv(raw):
    with pytest.raises(AppError, match="第 2 行"):
        parse_xy_csv(raw)


def test_import_checks_pdf_page_is_idempotent_and_keeps_provenance(tmp_path):
    repo = MaterialsRepository(tmp_path)
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    original = io.BytesIO()
    writer.write(original)
    pdf = original.getvalue()
    identity = hashlib.sha256(pdf).hexdigest()
    repo.save_document(LiteratureDocument(
        id=identity, name="figure.pdf", size=len(pdf), extension=".pdf", parser="text",
    ), [EvidenceBlock(id="figure", document_id=identity, text="Fig. 3F", page=1)], pdf)
    source = CurveSource(document_id=identity, figure="Fig. 3F", page=1, series="0.2%",
                         x_name="Strain", x_unit="%", y_name="Stress", y_unit="kPa")
    raw = b"0,0\n20,92.6"
    with pytest.raises(AppError, match="页码"):
        import_curve(repo, source.model_copy(update={"page": 2}), raw, "curve.csv")
    assert repo.digitized_curves(identity) == []
    first = import_curve(repo, source, raw, "curve.csv")
    assert import_curve(repo, source, raw, "renamed.csv") == first
    reopened = MaterialsRepository(tmp_path)
    assert reopened.records() == []
    saved, = reopened.digitized_curves(identity)
    assert saved == first and saved.source == source
    assert saved.origin == "digitized" and saved.points[-1] == (20., 92.6)
