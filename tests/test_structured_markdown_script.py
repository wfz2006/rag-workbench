import hashlib
import json
import subprocess
import sys
from pathlib import Path

from reportlab.pdfgen import canvas

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_structured_markdown.py"


def _pdf(path):
    pdf = canvas.Canvas(str(path))
    for page in range(1, 4):
        for index, line in enumerate((
            "Running header", "123", f"{page} Results", f"Device {page} was annealed",
            "Received 2000", f"at {170 + 10 * page} C.",
        )):
            pdf.drawString(72, 720 - index * 16, line)
        pdf.showPage()
    pdf.save()


def _convert(source, target, *options):
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(source), str(target), *map(str, options)],
        capture_output=True, timeout=15,
    )


def test_provenance_maps_output_blocks_to_unfiltered_pdf_lines(tmp_path):
    source, plain, target, sidecar = (tmp_path / name for name in (
        "source.pdf", "plain.md", "mapped.md", "mapped.json",
    ))
    _pdf(source)
    options = ("--title", "Paper", "--min-paragraph-chars", "20")
    assert _convert(source, plain, *options).returncode == 0
    result = _convert(source, target, *options, "--provenance", sidecar)
    assert result.returncode == 0, result.stderr
    assert plain.read_bytes() == target.read_bytes()

    mapping = json.loads(sidecar.read_text(encoding="utf-8"))
    assert mapping["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    markdown = target.read_text(encoding="utf-8").splitlines()
    assert mapping["blocks"][0]["source"] is None
    assert mapping["blocks"][1]["source"] is None
    for page in range(1, 4):
        heading, paragraph = mapping["blocks"][2 * page:2 * page + 2]
        assert heading["source"] == {
            "pdf_page": page, "text_line_start": 3, "text_line_end": 3, "text_lines": [3],
        }
        assert paragraph["source"] == {
            "pdf_page": page, "text_line_start": 4, "text_line_end": 6, "text_lines": [4, 6],
        }
        assert markdown[heading["markdown_line_start"] - 1] == f"## {page} Results"
        assert markdown[paragraph["markdown_line_start"] - 1:paragraph["markdown_line_end"]] == [
            f"Device {page} was annealed at {170 + 10 * page} C.",
        ]


def test_provenance_refuses_shared_paths_and_source_alias(tmp_path):
    source, target, alias = (tmp_path / name for name in ("source.pdf", "target.md", "alias.json"))
    _pdf(source)
    alias.hardlink_to(source)
    original = source.read_bytes()
    for output, sidecar in ((source, alias), (target, alias), (target, target)):
        result = _convert(source, output, "--provenance", sidecar)
        assert result.returncode != 0
        assert source.read_bytes() == original
        assert not target.exists()
    assert _convert(source, source).returncode != 0
    assert source.read_bytes() == original
