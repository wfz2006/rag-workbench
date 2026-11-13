import csv
import io
import json
import zipfile
from types import SimpleNamespace

import pytest

from rag_workbench.application.material_analysis import METRICS, Quantity
from rag_workbench.application.material_export import export_analysis
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.materials import EvidenceBlock, EvidenceReference, MaterialRecord


def test_analysis_bundle_preserves_filtered_revision_and_supplement_locator():
    main = SimpleNamespace(id="a" * 64, name="paper.pdf", parent_id=None)
    supplement = SimpleNamespace(id="b" * 64, name="supplement.pdf", parent_id=main.id)
    block = EvidenceBlock(
        id="e1", document_id=supplement.id, text="Table S1: ﬁlm mobility\n1.5", page=4,
    )
    record = MaterialRecord(
        id="r1", document_id=main.id, material_name="=material", property_name="mobility",
        value_raw="1.5", status="approved", revision=3,
        evidence=[
            EvidenceReference(block_id=block.id, quote="film mobility 1.5", field="value_raw"),
        ],
    )
    repo = SimpleNamespace(
        documents=lambda: [main, supplement], blocks=lambda identity: [block]
        if identity == supplement.id else [],
    )
    rows = [SimpleNamespace(
        record=record, paper=main, document=main, metric=METRICS["mobility"],
        value=Quantity(METRICS["mobility"].unit, "scalar", value=1.5),
        contexts={}, comparison_key=None, comparison_label="比较口径待确认",
    )]
    settings = {"metric": "mobility", "axis": "anneal_temperature", "scale": "log"}
    plot = [{"record_id": "r1", "revision": 3, "x": 100, "y": 1.5}]
    with zipfile.ZipFile(io.BytesIO(export_analysis(repo, rows, settings, plot))) as archive:
        snapshot = json.loads(archive.read("analysis.json"))
        assert snapshot["settings"] == settings
        assert snapshot["observations"][0]["record"]["revision"] == 3
        assert snapshot["observations"][0]["analysis"]["quantity"]["value"] == 1.5
        assert snapshot["observations"][0]["evidence"][0]["source_name"] == "supplement.pdf"
        assert snapshot["observations"][0]["evidence"][0]["page"] == 4
        assert snapshot["observations"][0]["evidence"][0]["quote"] == "film mobility 1.5"
        assert snapshot["plot_rows"] == plot
        csv_row, = csv.DictReader(io.StringIO(archive.read("observations.csv").decode("utf-8-sig")))
        assert csv_row["material_name"] == "'=material"
        assert csv_row["analysis_value"] == "1.5"
        assert "value_normalized" not in csv_row
    with pytest.raises(AppError):
        export_analysis(repo, rows, settings, [{**plot[0], "revision": 2}])
