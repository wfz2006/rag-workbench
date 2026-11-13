"""Read-only projection of reviewed experimental observations for analysis."""

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from rag_workbench.domain.errors import AppError
from rag_workbench.domain.materials import LiteratureDocument, MaterialRecord
from rag_workbench.infrastructure.material_repository import MaterialsRepository


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    unit: str


METRICS = {
    "mobility": Metric("mobility", "迁移率", "cm²·V⁻¹·s⁻¹"),
    "on_off_ratio": Metric("on_off_ratio", "开关比", "1"),
    "threshold_voltage": Metric("threshold_voltage", "阈值电压", "V"),
}
CONTEXT_AXES = {
    "anneal_temperature": ("退火温度", "°C"),
    "anneal_duration": ("退火时间", "min"),
    "film_thickness": ("薄膜厚度", "nm"),
    "measurement_temperature": ("测量温度", "°C"),
    "channel_length": ("沟道长度", "µm"),
    "channel_width": ("沟道宽度", "µm"),
}


@dataclass(frozen=True)
class Quantity:
    unit: str
    kind: Literal["scalar", "uncertainty", "range", "bound"]
    value: float | None = None
    low: float | None = None
    high: float | None = None
    error: float | None = None
    bound: str | None = None


@dataclass(frozen=True)
class Observation:
    record: MaterialRecord
    document: LiteratureDocument
    paper: LiteratureDocument
    metric: Metric | None
    value: Quantity | None
    contexts: dict[str, Quantity]
    comparison_key: tuple[str, ...] | None
    comparison_label: str


_METRIC_ALIASES = {
    "mobility": {
        "mobility", "field-effect mobility", "field effect mobility", "charge carrier mobility",
        "hole mobility", "electron mobility", "μ", "μsat", "μlin", "迁移率", "场效应迁移率",
        "空穴迁移率", "电子迁移率",
    },
    "on_off_ratio": {
        "on_off_ratio", "on/off ratio", "on-off ratio", "on/off current ratio",
        "current on/off ratio",
        "ion/ioff", "i_on/i_off", "开关比", "电流开关比",
    },
    "threshold_voltage": {
        "threshold_voltage", "threshold voltage", "vth", "v_th", "v t", "阈值电压",
    },
}
_SIGN_TRANSLATION = str.maketrans({"−": "-", "–": "-", "—": "-", "﹣": "-", "＋": "+"})
_SUPERSCRIPTS = "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻"
_EXP_TRANSLATION = str.maketrans(_SUPERSCRIPTS, "0123456789+-")
_DECIMAL = r"(?:\d+(?:\.\d*)?|\.\d+)"
_POWER = r"10\s*\^\s*[+-]?\d+"
_NUMBER = rf"[+-]?(?:{_DECIMAL}\s*[×xX*]\s*{_POWER}|{_POWER}|{_DECIMAL}(?:[eE][+-]?\d+)?)"
_PARTS = re.compile(rf"([+-]?)(?:({_DECIMAL})\s*[×xX*]\s*)?10\s*\^\s*([+-]?\d+)")
_SHARED_POWER = re.compile(rf"\((.+)\)\s*[×xX*]\s*({_POWER})")
_SCALED_UNIT = re.compile(rf"(.+?)\s*[×xX*]\s*({_POWER})")
_PREFIX_SCALED_UNIT = re.compile(rf"(?:[×xX*]\s*)?({_POWER})\s*(?:[×xX*]\s*)?(.+)")
_ATTACHED_UNIT_BOUNDARY = re.compile(rf"(?<=[0-9\]{_SUPERSCRIPTS}])(?=\s*[A-Za-z无])")


def _unit_key(unit: str | None) -> str:
    text = unicodedata.normalize("NFKC", unit or "").translate(_SIGN_TRANSLATION)
    text = re.sub(r"([Vs])\^(?=-1)", r"\1", text)
    return re.sub(r"[\s·⋅*]", "", text)


_MOBILITY_UNITS = {
    form.format(prefix=prefix): factor
    for prefix, factor in (("cm", 1.0), ("m", 1e4))
    for form in (
        "{prefix}2/Vs", "{prefix}^2/Vs", "{prefix}2/(Vs)", "{prefix}^2/(Vs)",
        "{prefix}2V-1s-1", "{prefix}^2V-1s-1", "{prefix}2/V/s", "{prefix}^2/V/s",
    )
}
_METRIC_UNITS = {
    "mobility": _MOBILITY_UNITS,
    "on_off_ratio": dict.fromkeys(("", "1", "-", "dimensionless", "unitless", "无量纲"), 1.0),
    "threshold_voltage": {"V": 1.0, "mV": 1e-3},
}
_LEGACY_SCALAR_UNITS = {
    **{unit: (factor, METRICS["mobility"].unit) for unit, factor in _MOBILITY_UNITS.items()},
    "V": (1.0, "V"), "mV": (0.001, "V"), "%": (1.0, "%"), "eV": (1.0, "eV"),
    "mA/cm2": (1.0, "mA/cm²"), "A/m2": (0.1, "mA/cm²"), "K": (1.0, "K"),
}
_TEMPERATURE = {"°C": (1.0, 0.0), "K": (1.0, -273.15)}
_TIME = {"s": (1 / 60, 0.0), "min": (1.0, 0.0), "h": (60.0, 0.0)}
_LENGTH_NM = {"Å": (0.1, 0.0), "nm": (1.0, 0.0), "μm": (1000.0, 0.0), "mm": (1e6, 0.0)}
_LENGTH_UM = {"Å": (1e-4, 0.0), "nm": (1e-3, 0.0), "μm": (1.0, 0.0), "mm": (1e3, 0.0)}
_CONTEXT_UNITS = {
    "anneal_temperature": _TEMPERATURE,
    "anneal_duration": _TIME,
    "film_thickness": _LENGTH_NM,
    "measurement_temperature": _TEMPERATURE,
    "channel_length": _LENGTH_UM,
    "channel_width": _LENGTH_UM,
}
_CONTEXT_VALUE = re.compile(r"(.+?)\s*([a-zA-Zµμ°ÅÅ℃][a-zA-Zµμ°ÅÅ℃ ]*)")


def _number(raw: str) -> float:
    if match := _PARTS.fullmatch(raw):
        sign, coefficient, exponent = match.groups()
        return float(f"{sign}{coefficient or 1}e{exponent}")
    return float(raw)


def _power_notation(raw: str) -> str:
    return re.sub(
        rf"10([{_SUPERSCRIPTS}]+)",
        lambda match: "10^" + match[1].translate(_EXP_TRANSLATION),
        raw.strip().translate(_SIGN_TRANSLATION),
    )


def _metric_factor(metric: Metric, raw_unit: str | None) -> float | None:
    units = _METRIC_UNITS[metric.key]
    if (factor := units.get(_unit_key(raw_unit))) is not None:
        return factor
    # Require a caret or actual superscript. PDF text may flatten a table header's exponent.
    scaled = _power_notation(raw_unit or "")
    if match := _SCALED_UNIT.fullmatch(scaled):
        unit, power = match.groups()
    elif match := _PREFIX_SCALED_UNIT.fullmatch(scaled):
        power, unit = match.groups()
    else:
        return None
    unit = unit.strip()
    if unit.startswith("(") and unit.endswith(")"):
        unit = unit[1:-1]
    factor = units.get(_unit_key(unit))
    if factor is None:
        return None
    factor *= _number(power)
    return factor if math.isfinite(factor) and factor > 0 else None


def _parse_quantity(
    raw: str, unit: str, factor: float, offset: float = 0.0, *, minimum: float | None = None,
) -> Quantity | None:
    # A base ten followed by a plain minus and integer may be a flattened exponent.
    if re.fullmatch(r"[+−-]?10\s*[-−]\s*\d+", raw.strip()):
        return None
    clean = _power_notation(raw)
    if match := _SHARED_POWER.fullmatch(clean):
        clean, power = match[1].strip(), match[2]
        factor *= _number(power)
        if not math.isfinite(factor) or factor <= 0:
            return None

    def convert(source: str) -> float:
        value = _number(source) * factor + offset
        if not math.isfinite(value) or minimum is not None and value < minimum:
            raise ValueError("Value outside physical or numeric bounds")
        return value

    try:
        if re.fullmatch(_NUMBER, clean):
            return Quantity(unit, "scalar", value=convert(clean))
        if match := re.fullmatch(rf"({_NUMBER})\s*(?:±|\+/-)\s*({_NUMBER})", clean):
            center, error = convert(match[1]), _number(match[2]) * factor
            low, high = center - error, center + error
            if error >= 0 and math.isfinite(low) and math.isfinite(high):
                return Quantity(unit, "uncertainty", value=center, low=low, high=high, error=error)
            return None
        if match := re.fullmatch(rf"\[\s*({_NUMBER})\s*,\s*({_NUMBER})\s*\]", clean):
            low, high = convert(match[1]), convert(match[2])
            return Quantity(unit, "range", low=low, high=high) if low <= high else None
        if match := re.fullmatch(rf"({_NUMBER})\s*(?:-|~|～|to|至)\s*({_NUMBER})", clean):
            low, high = sorted((convert(match[1]), convert(match[2])))
            return Quantity(unit, "range", low=low, high=high)
        if match := re.fullmatch(rf"(<=|>=|[<>≤≥])\s*({_NUMBER})", clean):
            bound = {"≤": "<=", "≥": ">="}.get(match[1], match[1])
            edge = convert(match[2])
            if bound.startswith("<"):
                return Quantity(unit, "bound", high=edge, bound=bound)
            return Quantity(unit, "bound", low=edge, bound=bound)
    except ValueError:
        return None
    return None


def _clean_text(value: str | None) -> str:
    return " ".join((value or "").split())


def _metric(record: MaterialRecord) -> Metric | None:
    if record.domain not in {"ofet", "general"}:
        return None
    name = _clean_text(unicodedata.normalize("NFKC", record.property_name)).casefold()
    name = re.sub(r"\s+in (?:the )?(?:linear|saturation)(?: regime)?$", "", name)
    return next((METRICS[key] for key, names in _METRIC_ALIASES.items() if name in names), None)


def _metric_quantity(record: MaterialRecord, metric: Metric) -> Quantity | None:
    raw_unit = _clean_text(record.unit_raw)
    factor = _metric_factor(metric, raw_unit)
    minimum = None if metric.key == "threshold_voltage" else 0.0
    if factor is not None:
        value = _parse_quantity(record.value_raw, metric.unit, factor, minimum=minimum)
        if value is not None:
            return value
    if raw_unit and factor is None:
        return None

    # Accept only suffixes with a known unit and a complete quantity before them.
    # If the unit field repeats the suffix, apply its scale once.
    for boundary in _ATTACHED_UNIT_BOUNDARY.finditer(record.value_raw):
        index = boundary.start()
        attached = _metric_factor(metric, record.value_raw[index:])
        if attached is not None and (not raw_unit or attached == factor):
            value = _parse_quantity(
                record.value_raw[:index], metric.unit, attached, minimum=minimum,
            )
            if value is not None:
                return value
    return None


def normalized_scalar(record: MaterialRecord) -> tuple[float | None, str | None]:
    metric = _metric(record)
    quantity = None
    if metric is not None:
        quantity = _metric_quantity(record, metric)
    elif conversion := _LEGACY_SCALAR_UNITS.get(_unit_key(record.unit_raw)):
        factor, unit = conversion
        quantity = _parse_quantity(record.value_raw, unit, factor)
    if quantity is not None and quantity.kind == "scalar":
        return quantity.value, quantity.unit
    return None, None


def _contexts(record: MaterialRecord) -> dict[str, Quantity]:
    values = {}
    for field, (_, unit) in CONTEXT_AXES.items():
        raw = getattr(record, field, None) or ""
        if not (match := _CONTEXT_VALUE.fullmatch(raw.strip())):
            continue
        conversion = _CONTEXT_UNITS[field].get(_unit_key(match[2]))
        if conversion is None:
            continue
        value = _parse_quantity(match[1], unit, *conversion, minimum=-273.15 if unit == "°C" else 0)
        if value is not None:
            values[field] = value
    return values


_ORIGIN_LABELS = {
    "measured": "实测", "calculated": "计算", "digitized": "图中读数", "cited": "转引",
}
_COMPARISON_FIELDS = {
    "carrier": "载流子", "regime": "工作区间", "statistic": "统计口径",
    "measurement_method": "测量方法", "measurement_environment": "测试环境",
    "measurement_temperature": "测试温度",
}
_ADDITIONAL_CONDITIONS = {
    "conditions": "其他条件", "device_structure": "器件结构", "electrode": "电极",
    "dielectric": "介电层", "channel_length": "沟道长度", "channel_width": "沟道宽度",
}
_QUANTITY_LABELS = {
    "scalar": "点值", "uncertainty": "报告值与误差", "range": "范围", "bound": "单侧界限",
}
_UNKNOWN = {
    "", "n/a", "na", "none", "unknown", "not available", "not reported", "not applicable",
    "-", "—", "未知", "未确认", "未报告", "不适用", "不详", "未提取",
}


def _comparison(
    record: MaterialRecord, metric: Metric | None, value: Quantity | None,
) -> tuple[tuple[str, ...] | None, str]:
    origin = getattr(record, "observation_origin", None)
    terms = tuple(_clean_text(getattr(record, field, None)) for field in _COMPARISON_FIELDS)
    if metric is None or value is None or origin not in _ORIGIN_LABELS or any(
        term.casefold() in _UNKNOWN for term in terms
    ):
        return None, "比较口径待确认"
    extra = tuple(_clean_text(getattr(record, field, None)) for field in _ADDITIONAL_CONDITIONS)
    key = (metric.key, origin, value.kind, value.bound or "", *terms, *extra)
    titles = (*_COMPARISON_FIELDS.values(), *_ADDITIONAL_CONDITIONS.values())
    conditions = (f"{title} {term}" for title, term in zip(titles, (*terms, *extra), strict=True)
                  if term)
    label = " · ".join((_ORIGIN_LABELS[origin], _QUANTITY_LABELS[value.kind], *conditions))
    return key, label


def analyze_record(
    record: MaterialRecord, document: LiteratureDocument, paper: LiteratureDocument | None = None,
) -> Observation:
    metric = _metric(record)
    value = _metric_quantity(record, metric) if metric is not None else None
    key, label = _comparison(record, metric, value)
    return Observation(
        record, document, paper or document, metric, value, _contexts(record), key, label,
    )


def load_observations(repo: MaterialsRepository) -> list[Observation]:
    records = [record for record in repo.records() if record.status == "approved"]
    documents = {document.id: document for document in repo.documents()}
    observations = []
    for record in records:
        document = documents.get(record.document_id)
        paper = documents.get(document.parent_id or document.id) if document else None
        if document is None or paper is None:
            raise AppError("material_analysis_source", "实验记录缺少所属论文或补充材料。")
        observations.append(analyze_record(record, document, paper))
    return observations
