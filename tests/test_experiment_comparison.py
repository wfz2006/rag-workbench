from dataclasses import replace

import pytest

from rag_workbench.application.experiment_comparison import compare_experiment
from rag_workbench.application.material_analysis import analyze_record
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.materials import LiteratureDocument, MaterialRecord

PAPER = LiteratureDocument(id="a" * 64, name="paper.pdf", extension=".pdf", size=1, parser="text")


def observation(identity, **changes):
    fields = dict(
        id=identity, document_id=PAPER.id, material_name="P3HT", property_name="mobility",
        value_raw="1.0", unit_raw="cm²/Vs", status="approved", observation_origin="measured",
        measurement_method="OFET", statistic="mean", carrier="hole", regime="saturation",
        process="spin coating", anneal_temperature="300 K", anneal_duration="30 min",
    )
    return analyze_record(MaterialRecord(**(fields | changes)), PAPER)


def test_uses_common_units_and_preserves_confounds():
    left = observation("baseline")
    right = observation(
        "treated", value_raw="2e-4", unit_raw="m²/Vs", anneal_temperature="40 °C",
        anneal_duration="1 h", process="blade coating",
    )
    result = compare_experiment(left, right)
    assert result.difference == pytest.approx(1)
    assert result.percent == pytest.approx(100)
    assert {item.field for item in result.conditions if item.state == "different"} == {
        "process", "anneal_temperature", "anneal_duration",
    }


@pytest.mark.parametrize("changes", [
    {"value_raw": "≤1.3"}, {"unit_raw": "s"}, {"statistic": "maximum"},
    {"measurement_method": None}, {"observation_origin": "digitized"},
    {"property_name": "threshold voltage", "unit_raw": "V"},
])
def test_incompatible_values_are_display_only(changes):
    result = compare_experiment(observation("baseline"), observation("treated", **changes))
    assert result.difference is None
    assert result.reason


def test_hidden_or_cross_paper_pairs_are_rejected_and_zero_has_no_percentage():
    base = observation("baseline", value_raw="0")
    treated = observation("treated")
    assert compare_experiment(base, treated).percent is None
    for other in (
        base,
        replace(treated, paper=PAPER.model_copy(update={"id": "b" * 64})),
        observation("other", status="candidate"),
    ):
        with pytest.raises(AppError):
            compare_experiment(base, other)


def test_supplement_and_equivalent_condition_units_preserve_a_same_paper_pair():
    supplement = LiteratureDocument(
        id="b" * 64, name="SI.pdf", extension=".pdf", size=1, parser="text",
        role="supplement", parent_id=PAPER.id,
    )
    baseline = observation("baseline")
    treatment = observation(
        "treated", document_id=supplement.id, anneal_temperature="26.85 °C",
        anneal_duration="0.5 h", value_raw="1.2",
    )
    treatment = analyze_record(treatment.record, supplement, PAPER)
    result = compare_experiment(baseline, treatment)
    by_field = {item.field: item.state for item in result.conditions}
    assert by_field["anneal_temperature"] == by_field["anneal_duration"] == "same"
    assert by_field["measurement_environment"] == "unknown"
    assert result.difference == pytest.approx(0.2)
