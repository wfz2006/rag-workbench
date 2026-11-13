import pytest

from rag_workbench.application.material_analysis import (
    analyze_record,
    load_observations,
)
from rag_workbench.application.materials import MaterialsService
from rag_workbench.domain.materials import LiteratureDocument, MaterialRecord

PAPER = LiteratureDocument(id="a" * 64, name="Paper.pdf", extension=".pdf", size=1, parser="text")
SUPPLEMENT = LiteratureDocument(
    id="b" * 64, name="SI.pdf", extension=".pdf", size=1, parser="text", role="supplement",
    parent_id=PAPER.id,
)


def record(**changes):
    values = dict(
        id="ofet-1", document_id=PAPER.id, domain="ofet", material_name="P3HT",
        property_name="mobility", value_raw="1.5", unit_raw="cm²·V⁻¹·s⁻¹", status="approved",
    )
    return MaterialRecord(**(values | changes))


@pytest.mark.parametrize(("raw", "unit", "expected"), [
    ("1.2 × 10⁻⁵", "m² V⁻¹ s⁻¹", 0.12),
    ("1.2e−1", "cm2/Vs", 0.12),
    ("1.2 × 10^-1", "cm^2/(V s)", 0.12),
    ("120", "(cm2/Vs) × 10⁻³", 0.12),
])
def test_mobility_normalizes_only_explicit_units(raw, unit, expected):
    row = analyze_record(record(value_raw=raw, unit_raw=unit), PAPER)
    assert row.metric.key == "mobility"
    assert row.value.kind == "scalar"
    assert row.value.value == pytest.approx(expected)
    assert row.value.unit == "cm²·V⁻¹·s⁻¹"


@pytest.mark.parametrize(("raw", "unit"), [
    ("0.3", "V"), ("0.3", None), ("0.3", "CM2/VS"), ("2e999", "cm2/Vs"),
    ("estimated 0.3", "cm2/Vs"), ("−2", "cm2/Vs"), ("[2, 1]", "cm2/Vs"),
    ("20 ± 3.8", "(cm2/Vs) × 10−4"),
    ("10−4", "cm2/Vs"),
])
def test_invalid_observation_preserves_raw_values(raw, unit):
    row = analyze_record(record(value_raw=raw, unit_raw=unit), PAPER)
    assert row.metric.key == "mobility"
    assert row.record.value_raw == raw
    assert row.value is None


def test_interval_and_bound_have_no_point_and_uncertainty_keeps_error():
    range_ = analyze_record(record(value_raw="[1, 2]"), PAPER).value
    assert (range_.kind, range_.low, range_.high, range_.value) == ("range", 1, 2, None)
    limit = analyze_record(record(value_raw="≤ 0.2"), PAPER).value
    assert (limit.kind, limit.low, limit.high, limit.value, limit.bound) == (
        "bound", None, 0.2, None, "<=",
    )
    error = analyze_record(record(value_raw="1.2 ± 0.2", unit_raw="m2/Vs"), PAPER).value
    assert (error.kind, error.value, error.error, error.low, error.high) == (
        "uncertainty", 12000, 2000, 10000, 14000,
    )


def test_on_off_ratio_and_negative_threshold():
    ratio = analyze_record(
        record(property_name="on/off ratio", value_raw="3.2 x 10^5", unit_raw=None), PAPER,
    ).value
    assert ratio.value == pytest.approx(320000)
    assert ratio.unit == "1"
    threshold = analyze_record(
        record(property_name="threshold voltage", value_raw="−850", unit_raw="mV"), PAPER,
    ).value
    assert threshold.value == pytest.approx(-0.85)
    assert threshold.unit == "V"


def test_published_descending_range_and_shared_uncertainty_exponent():
    threshold = analyze_record(
        record(property_name="V T", value_raw="−20–−24", unit_raw="V"), PAPER,
    ).value
    assert (threshold.kind, threshold.low, threshold.high, threshold.value) == (
        "range", -24, -20, None,
    )
    mobility = analyze_record(record(value_raw="(20 ± 3.8) × 10⁻⁴"), PAPER).value
    assert mobility.kind == "uncertainty"
    assert mobility.value == pytest.approx(0.002)
    assert mobility.error == pytest.approx(0.00038)


def test_approved_records_join_supplement_and_own_paper_without_persisting():
    class Repo:
        def records(self):
            return [
                record(document_id=SUPPLEMENT.id, revision=3),
                record(id="candidate", status="candidate"),
                record(id="unknown", property_name="other value"),
            ]

        def documents(self):
            return [PAPER, SUPPLEMENT]

    reviewed, unknown = load_observations(Repo())
    assert (reviewed.record.id, reviewed.record.revision) == ("ofet-1", 3)
    assert reviewed.document == SUPPLEMENT
    assert reviewed.paper == PAPER
    assert unknown.metric is None
    assert unknown.value is None
    assert reviewed.comparison_key is None
    assert reviewed.comparison_label == "比较口径待确认"


def test_comparison_requires_origin_and_distinguishes_methods_and_regimes():
    context = dict(
        carrier="hole", regime="saturation", statistic="mean", measurement_method="DC transfer",
        measurement_environment="nitrogen", measurement_temperature="25 °C",
    )
    unknown = analyze_record(record(**context), PAPER)
    measured = analyze_record(record(**context, observation_origin="measured"), PAPER)
    calculated = analyze_record(record(**context, observation_origin="calculated"), PAPER)
    linear = analyze_record(
        record(**(context | {"regime": "linear"}), observation_origin="measured"), PAPER,
    )
    assert unknown.comparison_key is None
    assert measured.comparison_key
    assert len({measured.comparison_key, calculated.comparison_key, linear.comparison_key}) == 3
    other = analyze_record(record(
        **context, observation_origin="measured", conditions="gate bias 10⁶ V", value_raw="1–2",
    ), PAPER)
    assert other.comparison_key != measured.comparison_key
    assert "gate bias 10⁶ V" in other.comparison_label
    assert "范围" in other.comparison_label


def test_explicit_context_units_and_temperature_offset():
    row = analyze_record(record(
        anneal_temperature="400 ± 2 K", anneal_duration="1 h", film_thickness="0.1 μm",
        measurement_temperature="25", channel_length="25 nm", channel_width="1 mm",
    ), PAPER)
    assert row.contexts["anneal_temperature"].value == pytest.approx(126.85)
    assert row.contexts["anneal_temperature"].error == 2
    assert row.contexts["anneal_duration"].value == 60
    assert row.contexts["film_thickness"].value == 100
    assert row.contexts["channel_length"].value == pytest.approx(0.025)
    assert row.contexts["channel_width"].value == 1000
    assert "measurement_temperature" not in row.contexts


@pytest.mark.parametrize(("property_name", "value", "unit", "expected", "canonical"), [
    ("on_off_ratio", "2.1 × 10⁴", "1", 21000.0, "1"),
    ("threshold_voltage", "−90", "V", -90.0, "V"),
    ("mobility", "0.01", "cm2/(Vs)", 0.01, "cm²·V⁻¹·s⁻¹"),
    ("mobility", "7.7", "cm² V⁻¹ s⁻¹", 7.7, "cm²·V⁻¹·s⁻¹"),
    ("mobility", "6.1", "cm^2/(V·s)", 6.1, "cm²·V⁻¹·s⁻¹"),
    ("mobility", "2.3 × 10⁻⁷", "cm²/(V·s)", 2.3e-7, "cm²·V⁻¹·s⁻¹"),
    ("mobility", "20", "× 10⁻⁴ cm² V⁻¹ s⁻¹", 0.002, "cm²·V⁻¹·s⁻¹"),
])
def test_general_record_scalar_normalization_agrees_with_analysis(
    property_name, value, unit, expected, canonical,
):
    row = record(domain="general", property_name=property_name, value_raw=value, unit_raw=unit)
    quantity = analyze_record(row, PAPER).value
    saved = MaterialsService._normalized(row)
    assert quantity.value == saved.value_normalized == pytest.approx(expected)
    assert quantity.unit == saved.unit_normalized == canonical
    assert (saved.value_raw, saved.unit_raw) == (value, unit)


def test_general_record_scaled_uncertainty_is_not_saved_as_scalar():
    row = record(domain="general", value_raw="20 ± 3.8", unit_raw="10^-4 cm2/Vs")
    quantity = analyze_record(row, PAPER).value
    assert quantity.kind == "uncertainty"
    assert quantity.value == pytest.approx(0.002)
    assert quantity.error == pytest.approx(0.00038)
    assert MaterialsService._normalized(row).value_normalized is None


def test_standard_threshold_key_supports_ranges():
    row = record(
        domain="general", property_name="threshold_voltage", value_raw="−20–−24", unit_raw="V",
    )
    quantity = analyze_record(row, PAPER).value
    assert (quantity.kind, quantity.low, quantity.high) == ("range", -24.0, -20.0)
    assert MaterialsService._normalized(row).value_normalized is None


@pytest.mark.parametrize(("property_name", "raw", "unit", "expected", "canonical"), [
    ("threshold_voltage", "−90 V", None, -90.0, "V"),
    ("threshold_voltage", "−90V", "V", -90.0, "V"),
    ("mobility", "7.7 cm² V⁻¹ s⁻¹", None, 7.7, "cm²·V⁻¹·s⁻¹"),
    ("mobility", "6.1 cm^2/(V·s)", None, 6.1, "cm²·V⁻¹·s⁻¹"),
    ("mobility", "2.3 × 10⁻⁷ cm²/(V·s)", None, 2.3e-7, "cm²·V⁻¹·s⁻¹"),
    ("mobility", "7.7 cm 2 V−1 s−1", "cm²/Vs", 7.7, "cm²·V⁻¹·s⁻¹"),
])
def test_explicit_unit_in_raw_value_is_shared_by_analysis_and_persistence(
    property_name, raw, unit, expected, canonical,
):
    row = record(domain="general", property_name=property_name, value_raw=raw, unit_raw=unit)
    observation = analyze_record(row, PAPER)
    saved = MaterialsService._normalized(row)
    assert observation.value is not None
    assert observation.value.value == saved.value_normalized == pytest.approx(expected)
    assert observation.value.unit == saved.unit_normalized == canonical
    assert (saved.value_raw, saved.unit_raw) == (raw, unit)


@pytest.mark.parametrize(("raw", "unit"), [
    ("7.7 cm²/Vs", "m²/Vs"),
    ("7.7 cm²/Vs", "× 10⁻⁴ cm²/Vs"),
    ("7.7 cm²/Vs", "mV"),
    ("7.7 V cm²/Vs", None),
    ("10−4 cm²/Vs", None),
])
def test_ambiguous_attached_units_remain_unparsed(raw, unit):
    row = record(value_raw=raw, unit_raw=unit)
    assert analyze_record(row, PAPER).value is None
    assert MaterialsService._normalized(row).value_normalized is None


def test_attached_unit_preserves_uncertainty_without_creating_scalar():
    row = record(value_raw="20 ± 3.8 cm²/Vs", unit_raw=None)
    quantity = analyze_record(row, PAPER).value
    assert quantity is not None
    assert (quantity.kind, quantity.value, quantity.error) == ("uncertainty", 20.0, 3.8)
    assert MaterialsService._normalized(row).value_normalized is None
