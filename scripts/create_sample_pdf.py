"""Optional development helper. Requires the dev extra; no business data."""

from pathlib import Path

from reportlab.pdfgen import canvas

target = Path(__file__).resolve().parents[1] / "examples" / "sample.pdf"
pdf = canvas.Canvas(str(target))
pdf.setTitle("Disposable PDF example")
pdf.drawString(60, 750, "Fictional development example")
pdf.drawString(60, 720, "Sample workspace theme: light or dark.")
pdf.drawString(60, 690, "This PDF is separate from the production knowledge base.")
pdf.save()
print(target)
