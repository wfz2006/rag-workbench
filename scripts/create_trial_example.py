"""Generate a clearly synthetic, two-page OFET PDF for offline trial acceptance."""

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]


def create_example(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(output), pagesize=A4, invariant=True)
    pdf.setTitle("SYNTHETIC OFET trial - not experimental data")
    pdf.setAuthor("RAG Workbench synthetic example")
    pages = [
        [
            "SYNTHETIC DATA - SOFTWARE DEMO ONLY",
            "Organic semiconductor device example / Page 1 of 2",
            "This is not a published paper and has no experimental validity.",
            "Material: TEST-OSC-A (fictional organic semiconductor). Sample: SYN-01.",
            "Material structure: hypothetical conjugated small molecule; no SMILES supplied.",
            "Device: bottom-gate/top-contact OFET; see Page 2 for preparation.",
            "Hole mobility in saturation: 0.42 cm^2 V^-1 s^-1 (mean of 6 devices).",
            "Threshold voltage: -18 V. On/off current ratio: 3.2 x 10^5.",
            "Measurement atmosphere: nitrogen. Temperature: 300 K. V_DS: -60 V.",
            "Mobility method: square-root transfer-curve fit in saturation.",
            "Values are made up to demonstrate linked records and evidence review.",
        ],
        [
            "SYNTHETIC DATA - SOFTWARE DEMO ONLY",
            "Preparation and measurement context / Page 2 of 2",
            "Sample SYN-01 uses material TEST-OSC-A from Page 1.",
            "Device stack: Si / SiO2 / PMMA / TEST-OSC-A / Au.",
            "Configuration: bottom gate and top source/drain contacts.",
            "Channel length: 50 um. Channel width: 1000 um.",
            "Solution: 8 mg/mL TEST-OSC-A in chlorobenzene, spin-coated at 2000 rpm.",
            "Annealing: 100 C for 30 min in nitrogen.",
            "Measurement: semiconductor parameter analyzer, nitrogen, 300 K.",
            "Transfer sweep: V_G from +20 V to -60 V at V_DS = -60 V.",
            "Mobility statistics: arithmetic mean of six fictional devices.",
            "No standard deviation or uncertainty is supplied; leave those fields empty.",
            "Do not infer a molecular formula, DOI, measurement error or real performance.",
        ],
    ]
    for lines in pages:
        pdf.setFillColorRGB(0.76, 0.35, 0.23)
        pdf.setFont("Helvetica-Bold", 15)
        pdf.drawString(42, 788, lines[0])
        pdf.setFillColorRGB(0.12, 0.12, 0.12)
        pdf.setFont("Helvetica-Bold", 12)
        pdf.drawString(42, 758, lines[1])
        pdf.setFont("Helvetica", 10)
        for i, line in enumerate(lines[2:]):
            pdf.drawString(42, 714 - i * 32, line)
        pdf.setFont("Helvetica-Oblique", 9)
        pdf.drawString(
            42, 44, "Fictional example. Not a scientific source. Do not use for prediction."
        )
        pdf.showPage()
    pdf.save()


if __name__ == "__main__":
    create_example(ROOT / "examples/materials_trial/synthetic-ofet.pdf")
