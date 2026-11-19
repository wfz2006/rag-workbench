"""Descriptive comparisons between two reviewed observations from the same paper."""

import math
import unicodedata
from dataclasses import dataclass
from typing import Literal

from rag_workbench.application.material_analysis import Observation, normalized_scalar
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.material_terms import standard_material_term
from rag_workbench.domain.materials import STRUCTURED_FIELDS

CONDITIONS = (
    "material_name", "material_structure", "device_structure", "process", "conditions",
    "observation_origin", "measurement_method", "statistic", "carrier", "regime",
    *STRUCTURED_FIELDS,
)
UNKNOWN = {
    "", "n/a", "na", "-", "—", "unknown", "not reported", "not applicable",
    "未报告", "未知", "未核对", "不适用", "未提取", "尚未提取",
}
AVAILABILITY = {
    "not_applicable": "不适用", "not_reported": "原文未报告", "not_extracted": "尚未提取",
}


@dataclass(frozen=True)
class Condition:
    field: str
    baseline: str
    treatment: str
    state: Literal["same", "different", "unknown"]


@dataclass(frozen=True)
class ExperimentComparison:
    conditions: tuple[Condition, ...]
    reason: str | None = None
    baseline: float | None = None
    treatment: float | None = None
    difference: float | None = None
    unit: str | None = None
    percent: float | None = None


def _clean(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _condition(field: str, baseline: Observation, treatment: Observation) -> Condition:
    left, right = baseline.record, treatment.record
    left_value, right_value = getattr(left, field) or "", getattr(right, field) or ""
    left_avail = left.field_availability.get(field)
    right_avail = right.field_availability.get(field)
    left_context, right_context = baseline.contexts.get(field), treatment.contexts.get(field)
    left_key, right_key = _clean(left_value), _clean(right_value)
    if left_avail == right_avail == "not_applicable":
        state = "same"
    elif left_key.casefold() in UNKNOWN or right_key.casefold() in UNKNOWN:
        state = "unknown"
    else:
        if left_context and right_context and left_context.kind == right_context.kind == "scalar":
            same = math.isclose(left_context.value, right_context.value, rel_tol=1e-9, abs_tol=1e-9)
        else:
            same = ((standard_material_term(field, left_key) or left_key)
                    == (standard_material_term(field, right_key) or right_key))
        state = "same" if same else "different"
    return Condition(
        field, left_value or AVAILABILITY.get(left_avail, "未记录"),
        right_value or AVAILABILITY.get(right_avail, "未记录"), state,
    )


def compare_experiment(baseline: Observation, treatment: Observation) -> ExperimentComparison:
    left, right = baseline.record, treatment.record
    if (baseline.paper.id != treatment.paper.id or left.id == right.id
            or left.status != "approved" or right.status != "approved"):
        raise AppError("experiment_pair", "请选择同一篇论文中的两条不同的已审核记录。")
    conditions = tuple(_condition(field, baseline, treatment) for field in CONDITIONS)
    if baseline.metric or treatment.metric:
        same_metric = baseline.metric == treatment.metric
    else:
        same_metric = _clean(left.property_name) == _clean(right.property_name)
    if not same_metric or left.domain != right.domain:
        return ExperimentComparison(conditions, "两组性能指标或实验类别不同。")
    a, a_unit = normalized_scalar(left)
    b, b_unit = normalized_scalar(right)
    if a is None or b is None or a_unit != b_unit:
        return ExperimentComparison(conditions, "需要同一单位下的两个可换算点值。")
    for item in conditions:
        if (item.field in {"observation_origin", "measurement_method", "statistic"}
                and item.state != "same"):
            return ExperimentComparison(
                conditions, "观测来源、测量方法或统计口径不同或待核实。",
            )
        if item.field in {"carrier", "regime"} and item.state == "different":
            return ExperimentComparison(conditions, "载流子类型或工作区间不同。")
    if left.observation_origin == "cited":
        return ExperimentComparison(conditions, "转引数据需要回到原研究确认实验分组。")
    delta = b - a
    if not math.isfinite(delta):
        return ExperimentComparison(conditions, "差值超过可表示范围。")
    # Ratios around zero or a negative baseline can invert the usual interpretation.
    percent = delta / a * 100 if a > 0 else None
    if percent is not None and not math.isfinite(percent):
        percent = None
    return ExperimentComparison(conditions, baseline=a, treatment=b, difference=delta,
                                unit=a_unit, percent=percent)
