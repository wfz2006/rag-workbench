from contextlib import closing
from io import BytesIO

import pytest
from reportlab.pdfgen import canvas

from rag_workbench.application.pdf_table_lookup import (
    _candidate_table,
    _choose_table,
    _Field,
    _page_tables,
    _Table,
    lookup_pdf_table,
    table_electrode_conditions,
    table_lookup_requested,
    table_sample,
    table_samples,
    without_table_temperatures,
)


@pytest.mark.parametrize("question", [
    "Table 2: What is the average saturated mobility of TCNBT Th IDT without Au?",
    "Table 2 中没有 Au 电极的 TCNBT Th IDT 的平均饱和迁移率是多少？",
])
def test_excluded_electrode_never_becomes_a_positive_filter(question):
    assert not table_lookup_requested(question)

pdfium = pytest.importorskip("pypdfium2")


def ruled_table(
    *, temperature="Substrate temperature (°C)", duplicate=False, numeric_footnote=False,
    table_number=1,
):
    stream = BytesIO()
    pdf = canvas.Canvas(stream, pagesize=(600, 800))
    pdf.setFont("Helvetica", 10)
    pdf.drawString(50, 747, f"Table {table_number}. Device performance.")
    for y in (732, 704, 590):
        pdf.line(50, y, 520, y)
    for x, label in zip(
        (54, 145, 310, 445),
        ("Films", temperature, "Mobility (cm2/(Vs))", "VT(V)"), strict=True,
    ):
        pdf.drawString(x, 715, label)
    rows = [
        ("D42-Bare", "25", "0.09", "-14"),
        ("", "60", "0.06", "-10"),
        ("D42-OTS", "25", "2.8 × 10", "-90"),
        ("", "60", "3.5 × 10", "-8"),
        ("", "100", "0.02", "-2"),
    ]
    if duplicate:
        rows[-1] = ("", "60", "0.02", "-2")
    if numeric_footnote:
        rows[-1] = ("", "100", "0.25", "-2")
    for index, row in enumerate(rows):
        y = 690 - 18 * index
        for x, item in zip((54, 145, 310, 445), row, strict=True):
            pdf.drawString(x, y, item)
        if index in (2, 3):
            pdf.setFont("Helvetica", 7)
            x = 310 + pdf.stringWidth(row[2], "Helvetica", 10)
            pdf.drawString(x, y + 4, "-7" if index == 2 else "-3")
            pdf.setFont("Helvetica", 10)
        if numeric_footnote and index == 4:
            x = 310 + pdf.stringWidth("0.25", "Helvetica", 10)
            pdf.setFont("Helvetica", 7)
            pdf.drawString(x, y + 4, "1")
            pdf.setFont("Helvetica", 10)
    # Text in a neighbouring column or outside the bottom rule must never fill a table cell.
    pdf.drawString(531, 654, "123")
    pdf.drawString(50, 560, "D42-OTS 100 0.99")
    if numeric_footnote:
        pdf.drawString(50, 576, "1 Average from three devices.")
    pdf.save()
    return stream.getvalue()


QUESTION = "请你告诉我D42-OTS在25、60、100℃下的迁移率分别是多少"


def test_read_merged_sample_and_visual_exponents():
    assert table_lookup_requested(QUESTION)
    assert table_sample(QUESTION) == "D42-OTS"
    result = lookup_pdf_table(ruled_table(), QUESTION, max_pages=10)
    assert (result.page, result.table_label) == (1, "Table 1")
    assert "D42-OTS" in result.text and "Substrate temperature" in result.text
    assert "25 °C | 2.8 × 10⁻⁷" in result.answer
    assert "60 °C | 3.5 × 10⁻³" in result.answer
    assert "100 °C | 0.02" in result.answer
    assert "0.09" not in result.answer and "0.99" not in result.answer


@pytest.mark.parametrize("question", [
    "D42-BARE在25、60、100℃下的迁移率是多少",
    "D42-OTS-X在25、60、100℃下的迁移率是多少",
    "D42-OTS和D99-X在60℃下的迁移率分别是多少",
    "D42-OTS在退火温度100℃下的迁移率是多少",
    "D42-OTS在25至100℃下的迁移率是多少",
    "D42-OTS在60℃下的最高迁移率是多少",
    "根据Table 2告诉我D42-OTS在60℃下的迁移率",
])
def test_declines_wrong_sample_missing_temperature_or_table(question):
    assert lookup_pdf_table(ruled_table(), question, max_pages=10) is None


def test_declines_conflicting_rows_and_annealing_header():
    assert lookup_pdf_table(ruled_table(duplicate=True), QUESTION, max_pages=10) is None
    assert lookup_pdf_table(
        ruled_table(temperature="Annealing temperature (°C)"), QUESTION, max_pages=10,
    ) is None


def test_distinguishes_table_lookup_from_explanatory_question():
    assert not table_lookup_requested("D42-OTS的迁移率为什么随沉积温度提高？")
    assert table_lookup_requested("根据 Table 1 回答不同温度下的迁移率")
    assert table_lookup_requested("提取 D42-OTS 在不同温度下的迁移率")
    assert table_sample("D42‑OTS 在60℃下的迁移率是多少") == "D42-OTS"


@pytest.mark.parametrize(("question", "value"), [
    ("D42-OTS 的迁移率", "3.5 × 10⁻³"),
    ("D42-OTS threshold voltage", "−90"),
    ("D42-OTS 在 60 度下的迁移率", "60 °C | 3.5 × 10⁻³"),
])
def test_short_sample_metric_queries(question, value):
    result = lookup_pdf_table(ruled_table(), question, max_pages=1)
    assert result is not None and value in result.answer


def test_chinese_degree_list_selects_only_exact_conditions():
    result = lookup_pdf_table(
        ruled_table(), "D42-OTS 在 25、60 度下的迁移率分别是多少", max_pages=1,
    )
    assert result is not None
    assert "25 °C | 2.8 × 10⁻⁷" in result.answer
    assert "60 °C | 3.5 × 10⁻³" in result.answer
    assert "100 °C" not in result.answer


def test_mask_only_literal_temperature_spans_for_followup():
    result = without_table_temperatures("P2 退火 25、60 度 1 小时，以及 Au/150 °C 的迁移率")
    assert result.split() == ["P2", "退火", "1", "小时,以及", "Au/150", "°C", "的迁移率"]


def test_supplementary_table_number_is_not_a_sample_name():
    assert table_samples("Table S1 里 P2 的迁移率是多少？") == ("P2",)


@pytest.mark.parametrize("temperatures", ["60 and 100 °C", "60 °C and 100 °C", "60, 100 °C"])
def test_english_temperature_list_includes_both_temperatures(temperatures):
    result = lookup_pdf_table(
        ruled_table(), f"What is the mobility of D42-OTS at {temperatures}?", max_pages=1,
    )
    assert result is not None
    assert "60 °C | 3.5 × 10⁻³" in result.answer
    assert "100 °C | 0.02" in result.answer
    assert "25 °C" not in result.answer


def test_english_temperature_list_with_serial_comma():
    result = lookup_pdf_table(
        ruled_table(), "What is the mobility of D42-OTS at 25, 60, and 100 °C?", max_pages=1,
    )
    assert result is not None
    assert all(f"{temperature} °C |" in result.answer for temperature in (25, 60, 100))


@pytest.mark.parametrize("question", [
    "What is the mobility of D42-OTS above 60 °C?",
    "What is the mobility of D42-OTS at around 60 °C?",
    "What is the mobility of D42-OTS at 60 °C or higher?",
    "What is the mobility of D42-OTS between 60 and 100 °C?",
    "D42-OTS 在不低于60℃的迁移率是多少？",
    "What is the mobility of D42-OTS in Table 1 at 77 K?",
    "What is the mobility of D42-OTS in Table 1 at 100 °F?",
    "What is the mobility of D42-OTS at 60 °C and 77 K?",
    "What is the mobility of D42-OTS in Table 1 at room temperature?",
    "What is the mobility of D42-OTS at 60 °C with a drain voltage of 20 V?",
    "What is the mobility of D42-OTS at 60 °C at 20 V?",
    "What is the mobility of D42-OTS at 60 °C after 2 seconds?",
    "Table 1 中 D42-OTS 用氯苯制膜且在 60℃ 的迁移率是多少？",
    "What is the mobility of D42-OTS at 60 °C with chlorobenzene as the solvent in Table 1?",
    "What is the mobility of D42-OTS at 60 °C using chloroform in Table 1?",
    "What is the mobility and subthreshold swing of D42-OTS at 60 °C?",
    "What is the mobility and contact resistance of D42-OTS at 60 °C?",
    "D42-OTS 在60℃的迁移率和开启电压分别是多少？",
    "Table 1 中除了 D42-OTS 的其他样品迁移率是多少？",
    "D42-OTS 的 60℃ 和 100℃ 迁移率相差多少？",
    "D42-OTS 在 25 到 100 度的迁移率",
    "D42-OTS 在 60 度以上的迁移率",
    "D42-OTS 在华氏 60 度的迁移率",
    "D42-OTS 在夹角 60 度的迁移率",
    "without D42-OTS mobility",
    "D42-Bare 而不是 D42-OTS 的迁移率",
    "How does D42-OTS mobility depend on film processing?",
])
def test_declines_questions_with_unresolved_conditions_or_metrics(question):
    assert lookup_pdf_table(ruled_table(), question, max_pages=1) is None


def test_raised_footnote_digit_is_not_part_of_a_decimal():
    result = lookup_pdf_table(
        ruled_table(numeric_footnote=True), "D42-OTS 在100℃下的迁移率是多少？", max_pages=1,
    )
    assert result is None


def qualified_table():
    stream = BytesIO()
    pdf = canvas.Canvas(stream, pagesize=(600, 800))
    pdf.setFont("Helvetica", 10)
    pdf.line(45, 761, 515, 761)
    pdf.drawString(46, 744, "Table 4. Transistor performance by annealing temperature.")
    pdf.line(45, 631, 515, 631)
    for x, label in zip((46, 151, 268, 366, 463), (
        "OSC", "Annealing temp. (°C)", "Linear mobility", "Saturated mobility", "Ion/off",
    ), strict=True):
        pdf.drawString(x, 715, label)
    for x in (268, 366):
        pdf.drawString(x, 704, "(cm2 V-1 s-1)")
    for y, row in zip((687, 670, 653), (
        ("para-IDT(QNCN)2", "80", "0.031", "0.074", "8.8"),
        ("meta-IDT(QNCN)2", "80", "0.021", "0.043", "6.2"),
        ("meta-IDT(QNCN)2", "100", "0.037", "0.076", "8.0"),
    ), strict=True):
        for x, item in zip((46, 151, 268, 366, 463), row, strict=True):
            pdf.drawString(x, y, item)
    pdf.save()
    return stream.getvalue()


def test_multiple_metrics_share_the_same_explicit_table_rows():
    question = "D42-OTS 在 25、60、100℃ 的迁移率和阈值电压分别是多少"
    result = lookup_pdf_table(ruled_table(), question, max_pages=1)
    assert result is not None
    assert "60 °C | 3.5 × 10⁻³ | −8" in result.answer


def test_compare_explicit_samples_at_the_same_temperature():
    question = "D42-OTS 和 D42-Bare 在 60℃ 的迁移率分别是多少"
    result = lookup_pdf_table(ruled_table(), question, max_pages=1)
    assert result is not None
    assert "D42-OTS | 60 °C | 3.5 × 10⁻³" in result.answer
    assert "D42-Bare | 60 °C | 0.06" in result.answer


def test_caption_between_rules_and_multiline_qualified_headers():
    question = (
        "对比 Table 4 para-IDT(QNCN)2 与 meta-IDT(QNCN)2 在 80℃ 退火的线性和饱和迁移率及开关比"
    )
    assert table_sample("para-IDT(QNCN)2 在 80℃退火的线性迁移率") == "para-IDT(QNCN)2"
    result = lookup_pdf_table(qualified_table(), question, max_pages=1)
    assert result is not None
    assert (result.page, result.table_label) == (1, "Table 4")
    assert "80 °C | 0.031 | 0.074 | 8.8" in result.answer
    assert "80 °C | 0.021 | 0.043 | 6.2" in result.answer
    assert "0.037" not in result.answer


@pytest.mark.parametrize("question", [
    "Table 4 中 meta-IDT(QNCN)2 在 80℃ 衬底温度下的线性迁移率是多少？",
    "Table 4 中 meta-IDT(QNCN)2 在 80℃ 测量的线性迁移率是多少？",
    "Table 4 中 meta-IDT(QNCN)2 在 80℃ 退火的最高线性迁移率是多少？",
    "Table 4 中 meta-IDT(QNCN)2 在 80℃ 退火的电子线性迁移率是多少？",
    "Table 4 中 meta-IDT(QNCN)2 在 80℃ 退火 2 小时的线性迁移率是多少？",
    "Table 4 中 meta-IDT(QNCN)2 在 80℃ 退火的非饱和区迁移率是多少？",
    "What is the non-saturated mobility of meta-IDT(QNCN)2 annealed at 80 °C in Table 4?",
    "Table 4 中 meta-IDT(QNCN)2 不在 80℃ 退火的饱和迁移率是多少？",
    "What is the saturated mobility of meta-IDT(QNCN)2 not annealed at 80 °C in Table 4?",
])
def test_lookup_requires_explicit_matching_qualifiers(question):
    assert lookup_pdf_table(qualified_table(), question, max_pages=1) is None


def test_recovers_split_digits_and_does_not_infer_maximum():
    stream = BytesIO()
    pdf = canvas.Canvas(stream, pagesize=(600, 800))
    pdf.setFont("Helvetica", 10)
    pdf.drawString(50, 747, "Table 2. Device data.")
    for y in (732, 704, 650):
        pdf.line(50, y, 450, y)
    for x, text in ((55, "Sample"), (147, "Linear mobility (cm2/Vs)"),
                    (320, "Saturated mobility (cm2/Vs)")):
        pdf.drawString(x, 718, text)
    for y, sample, maximum in ((687, "P1", "0.25"), (671, "P2", "0.36")):
        pdf.drawString(55, y, sample)
        pdf.drawString(147, y, "0.1")
        pdf.drawString(162, y, "1")
        pdf.drawString(320, y, maximum)
    pdf.save()
    data = stream.getvalue()
    result = lookup_pdf_table(data, "P1 在 Table 2 的线性和饱和迁移率是多少", max_pages=1)
    assert result is not None and "| P1 | 0.11 | 0.25 |" in result.answer
    assert lookup_pdf_table(data, "P1 在 Table 2 的最高线性迁移率是多少", max_pages=1) is None


def solvent_free_table():
    """A three-page paper whose only table has no solvent column."""
    stream = BytesIO()
    pdf = canvas.Canvas(stream, pagesize=(600, 800))
    pdf.setFont("Helvetica", 10)
    pdf.drawString(50, 740, "Supporting discussion without tables.")
    pdf.showPage()
    pdf.drawString(50, 740, "Further discussion of film processing.")
    pdf.showPage()
    pdf.drawString(50, 747, "Table 1. Device performance.")
    for y in (732, 704, 590):
        pdf.line(50, y, 520, y)
    for x, label in zip(
        (54, 145, 310, 445),
        ("Films", "Substrate temperature (°C)", "Mobility (cm2/(Vs))", "VT(V)"),
        strict=True,
    ):
        pdf.drawString(x, 715, label)
    for index, row in enumerate((("PY3-OTS", "25", "2.8 × 10", "-20"),
                                 ("PY3-OTS", "60", "2.3 × 10", "-8"),
                                 ("PY3-OTS", "100", "0.02", "-2"))):
        y = 690 - 18 * index
        for x, item in zip((54, 145, 310, 445), row, strict=True):
            pdf.drawString(x, y, item)
        pdf.setFont("Helvetica", 7)
        x = 310 + pdf.stringWidth(row[2], "Helvetica", 10)
        pdf.drawString(x, y + 4, "-3")
        pdf.setFont("Helvetica", 10)
    pdf.save()
    return stream.getvalue()


@pytest.mark.parametrize(("preposition", "medium"), [
    ("in", "DMF"), ("in", "acetonitrile"), ("under", "nitrogen"), ("in", "o-xylene"),
])
def test_unverifiable_explicit_condition_declines(preposition, medium):
    question = (
        f"What is the mobility of PY3-OTS at 60 °C {preposition} {medium} "
        "in Table 1 of 薄膜文献1.pdf?"
    )
    assert table_lookup_requested(question) is False
    assert lookup_pdf_table(solvent_free_table(), question, max_pages=3) is None


@pytest.mark.parametrize("question", [
    "表1里 PY3-OTS 在乙腈中 60℃ 的迁移率是多少？",
    "表1里 PY3-OTS 于乙腈中 60℃ 的迁移率是多少？",
    "表1里 PY3-OTS 在 DMF 中 60℃ 的迁移率是多少？",
    "表1里 PY3-OTS 在未知介质X中 60℃ 的迁移率是多少？",
    "表1里 PY3-OTS 在含有 DMF 的溶液中 60℃ 的迁移率是多少？",
    "What is the mobility of PY3-OTS at 60 °C in a solution of DMF in Table 1?",
    "What is the mobility of PY3-OTS at 60 °C using a solution containing DMF in Table 1?",
    "What is the mobility of PY3-OTS at 60 °C in a solution consisting of DMF in Table 1?",
])
def test_unverified_condition_in_compound_medium_expression_declines(question):
    assert lookup_pdf_table(solvent_free_table(), question, max_pages=3) is None


@pytest.mark.parametrize("question", [
    "What is the mobility of PY3-OTS at 60 °C in Table 1 of 薄膜文献1.pdf?",
    "表1里 PY3-OTS 在这张表中记录的 60℃ 迁移率是多少？",
    "在原文中 Table 1 里 PY3-OTS 在 60℃ 下的迁移率是多少？",
])
def test_solvent_free_question_still_reads_page_three(question):
    result = lookup_pdf_table(
        solvent_free_table(),
        question,
        max_pages=3,
    )
    assert result is not None
    assert (result.page, result.table_label) == (3, "Table 1")
    assert "2.3 × 10⁻³" in result.answer


@pytest.mark.parametrize("media", ["CB and DMF", "CB, DCB, and DMF", "CB, DMF"])
def test_known_solvent_does_not_hide_unverified_coordinated_medium(media):
    data = condition_table(
        ("Polymers (solvents)", "Maximum hole mobility (cm2/Vs)", "VT(V)"),
        (("P1 (CB)", "0.086", "-31"), ("P1 (DCB)", "0.074", "-30")),
    )
    control = lookup_pdf_table(data, "Table 2: highest mobility of P1 in CB", max_pages=1)
    assert control is not None and "0.086" in control.answer
    assert lookup_pdf_table(
        data, f"Table 2: highest mobility of P1 in a solution of {media}", max_pages=1,
    ) is None


def condition_table(headers, rows, *, caption="Table 2. Device data.", repeat=False):
    stream = BytesIO()
    pdf = canvas.Canvas(stream, pagesize=(600, 800))
    pdf.setFont("Helvetica", 10)
    for offset in (0, 340) if repeat else (0,):
        pdf.line(45, 765 - offset, 525, 765 - offset)
        pdf.line(45, 764.7 - offset, 525, 764.7 - offset)
        pdf.drawString(47, 747 - offset, caption)
        for y in (730, 695, 530):
            pdf.line(45, y - offset, 525, y - offset)
        positions = range(679, 679 - len(rows) * 17, -17)
        for y, row in ((714, headers), *zip(positions, rows, strict=True)):
            for x, value in zip((48, 225, 440), row, strict=True):
                pdf.drawString(x, y - offset, value)
    pdf.save()
    return stream.getvalue()


def electrode_table(*, repeat=False):
    return condition_table(
        ("TCNBT Th IDT", "Electron saturated average mobility (cm2/Vs)", "VT(V)"),
        (
            ("Au/as-cast", "0.001", "-20"),
            ("Au/120 °C", "0.07", "-21"),
            ("Au/150 °C", "0.64 ± 0.21", "-22"),
            ("Ag/150 °C", "0.83 ± 0.27", "-23"),
            ("Ag/200 °C", "0.95", "-24"),
        ),
        caption="Table 2. Device data for different electrodes.",
        repeat=repeat,
    )


@pytest.mark.parametrize(("label", "expected"), [
    ("Au/150", "Au/150 °C | 0.64 ± 0.21"),
    ("Ag/150 °C", "Ag/150 °C | 0.83 ± 0.27"),
    ("TCNBT-Th-IDT 在 Ag/150 °C", "Ag/150 °C | 0.83 ± 0.27"),
    ("Au/as-cast", "Au/as-cast | 0.001"),
])
def test_explicit_composite_electrode_labels(label, expected):
    question = f"列出 Table 2 中 {label} 的饱和电子平均迁移率"
    result = lookup_pdf_table(electrode_table(), question, max_pages=1)
    assert result is not None
    assert expected in result.answer
    assert "退火温度" not in result.answer


def test_composite_electrode_lookup_requires_unique_verified_table():
    question = "Au/150 °C 的饱和电子平均迁移率"
    result = lookup_pdf_table(electrode_table(), question, max_pages=1)
    assert result is not None and "Au/150 °C | 0.64 ± 0.21" in result.answer
    assert "Ag/150" not in result.answer
    assert lookup_pdf_table(electrode_table(repeat=True), question, max_pages=1) is None


@pytest.mark.parametrize("question", [
    "Au/150 °C 和 Ag/175 °C 的饱和电子平均迁移率",
    "without Au/150 °C, saturated electron average mobility",
    "Au/150 °C 以上的饱和电子平均迁移率",
    "Au/150 °C 和 Ag/150 °C 的测量饱和电子平均迁移率",
])
def test_implicit_table_does_not_relax_composite_conditions(question):
    assert lookup_pdf_table(electrode_table(), question, max_pages=1) is None


def test_sample_hints_keep_compound_and_remove_composite_conditions():
    question = "Table 2 中 TCNBT-Th-IDT 在 Ag/150 °C 和 Au/as-cast 下的迁移率"
    assert table_samples(question) == ("TCNBT-Th-IDT",)
    assert table_electrode_conditions(question) == ("Ag/150 °C", "Au/as-cast")
    assert table_samples("Table 2 中 Au/150、Ag/150 °C 和 Au/as-cast 的迁移率") == ()


@pytest.mark.parametrize(("labels", "expected"), [
    ("Au/150 °C 和 Ag/150 °C", ["Au/150 °C", "Ag/150 °C"]),
    ("Au/150 °C 和 Ag/200 °C", ["Au/150 °C", "Ag/200 °C"]),
    ("Au/as-cast 和 Ag/150 °C", ["Au/as-cast", "Ag/150 °C"]),
])
def test_composite_electrode_pairs_select_exactly_two_rows(labels, expected):
    question = f"对比 Table 2 中 {labels} 的饱和电子平均迁移率"
    result = lookup_pdf_table(electrode_table(), question, max_pages=1)
    assert result is not None
    rows = result.answer.splitlines()[4:]
    assert [row.split("|")[1].strip() for row in rows] == expected


@pytest.mark.parametrize("labels", [
    "Au/150 °C 和 Ag/175 °C",
    "Au/150 °C 和 Ag/150 °C 的退火",
    "Au/150 °C 和 Ag/150 °C 的测量",
])
def test_unproven_electrode_conditions_decline(labels):
    question = f"列出 Table 2 中 {labels} 的饱和电子平均迁移率"
    assert lookup_pdf_table(electrode_table(), question, max_pages=1) is None


@pytest.mark.parametrize(("labels", "expected"), [
    ("P1 (CB) 和 P1 (DCB)", ["P1 (CB)", "P1 (DCB)"]),
    ("P1 (CB) 和 P2 (DCB)", ["P1 (CB)", "P2 (DCB)"]),
    ("P1/CB 和 P2/DCB", ["P1 (CB)", "P2 (DCB)"]),
    ("P1 在 CB 和 DCB", ["P1 (CB)", "P1 (DCB)"]),
    ("P1 在 CB 中", ["P1 (CB)"]),
    ("P1 在 CB 溶液中", ["P1 (CB)"]),
    ("P1 in a solution of DCB", ["P1 (DCB)"]),
])
def test_multi_solvent_conditions_keep_explicit_sample_pairs(labels, expected):
    data = condition_table(
        ("Polymers (solvents)", "Maximum hole mobility (cm2/Vs)", "VT(V)"),
        (("P1 (CB)", "0.086", "-31"), ("P1 (DCB)", "0.074", "-30"),
         ("P2 (CB)", "0.211", "-34"), ("P2 (DCB)", "0.167", "-30")),
    )
    question = f"列出 Table 2 中 {labels} 的最高迁移率和阈值电压"
    result = lookup_pdf_table(data, question, max_pages=1)
    assert result is not None
    rows = result.answer.splitlines()[4:]
    assert [row.split("|")[1].strip() for row in rows] == expected
    assert lookup_pdf_table(
        data, "列出 Table 2 中 P1 (CB) 和 P2 (1-CN) 的最高迁移率", max_pages=1,
    ) is None
    assert lookup_pdf_table(
        data, "列出 Table 2 中 P1 (CB) 和 P2 的最高迁移率", max_pages=1,
    ) is None


@pytest.mark.parametrize("condition", [
    "P1 不使用 CB 的",
    "P1 不是 CB 制膜的",
    "P1 非 CB 的",
    "P1 without CB",
    "P1 not in CB",
])
def test_negated_solvent_does_not_select_the_excluded_row(condition):
    data = condition_table(
        ("Polymers (solvents)", "Hole mobility (cm2/Vs)", "VT(V)"),
        (("P1 (CB)", "0.086", "-31"), ("P1 (DCB)", "0.074", "-30")),
    )
    assert lookup_pdf_table(data, f"Table 2: {condition} mobility", max_pages=1) is None


def test_repeated_rules_share_only_one_caption_but_separate_tables_stay_distinct():
    data = electrode_table(repeat=True)
    with pdfium.PdfDocument(data) as pdf, closing(pdf[0]) as page:
        tables = _page_tables(page, pdfium.raw, "Table 2")
    assert len(tables) == 2
    assert lookup_pdf_table(
        data, "Table 2 中 TCNBT-Th-IDT 在所有条件下的饱和电子平均迁移率", max_pages=1,
    ) is None


def test_competing_rule_starts_cannot_hide_conflicting_observations():
    def table(rows, *, complete):
        return _Table(
            "Table 2", "Table 2. Device data.", ("Films", "Mobility (cm2/Vs)"),
            (_Field(0, "sample"), _Field(1, "mobility", "cm²/(V·s)")), rows, complete,
        )

    full = table((("P1", "0.08"), ("P2", "0.17")), complete=True)
    partial = table((("P1", "0.08"),), complete=False)
    conflict = table((("P1", "0.10"),), complete=False)
    assert _choose_table([partial, full]) == full
    assert not _choose_table([conflict, full]).rows


def test_all_conditions_requires_a_complete_table():
    table = _Table(
        "Table 2", "Table 2. Device data.", ("TCNBT Th IDT", "Mobility (cm2/Vs)"),
        (_Field(0, "sample"), _Field(1, "mobility", "cm²/(V·s)")),
        (("Au/as-cast", "0.001"), ("Ag/150 °C", "0.83")), False,
    )
    assert _candidate_table(table, "TCNBT-Th-IDT 在所有条件下的迁移率是多少", [], 1) is None


def test_explicit_temperatures_follow_their_named_samples():
    result = lookup_pdf_table(
        ruled_table(),
        "对比 Table 1 中 D42-Bare 在 25℃ 和 D42-OTS 在 100℃ 的迁移率",
        max_pages=1,
    )
    assert result is not None
    assert "D42-Bare | 25 °C | 0.09" in result.answer
    assert "D42-OTS | 100 °C | 0.02" in result.answer
    assert "2.8 × 10⁻⁷" not in result.answer


@pytest.mark.parametrize("question", ["Table 2 中 P1 的数据", "P1's data in Table 2"])
def test_sample_data_returns_supported_columns_and_names_omissions(question):
    data = condition_table(
        ("Sample", "Hole mobility (cm2/Vs)", "Contact resistance"),
        (("P1", "0.086", "150"), ("P2", "0.211", "325")),
    )
    result = lookup_pdf_table(data, question, max_pages=1)
    assert result is not None
    assert "| P1 | 0.086 |" in result.answer
    assert "Contact resistance" in result.answer and "未提取" in result.answer
    assert "150" not in result.answer and "0.211" not in result.answer


def test_sample_data_does_not_drop_missing_supported_value():
    data = condition_table(
        ("Sample", "Hole mobility (cm2/Vs)", "VT(V)"),
        (("P1", "0.086", ""), ("P2", "0.211", "-7")),
    )
    assert lookup_pdf_table(data, "Table 2 中 P1 的数据", max_pages=1) is None


@pytest.mark.parametrize("question", [
    "D42-OTS 的数据",
    "Table 1 中 D42-OTS 的晶体厚度数据",
    "Table 1 中 D42-OTS 的数据来源",
    "Table 1 中不含 D42-OTS 的数据",
    "D42-OTS 的数据见 Table 1 还是 Table 2",
])
def test_sample_data_requires_explicit_table_and_generic_data_request(question):
    assert lookup_pdf_table(ruled_table(), question, max_pages=1) is None
