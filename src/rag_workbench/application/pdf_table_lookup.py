"""Conservative local lookup of explicit observations in ruled digital PDF tables."""

import bisect
import math
import re
import unicodedata
from contextlib import closing
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise
from statistics import median

import numpy as np

from rag_workbench.infrastructure.material_preview import _PDFIUM_LOCK

_NUMBER = r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)"
_CELSIUS = r"(?:°\s*C|degrees?\s+C(?:elsius)?|C(?:elsius)?|(?:摄氏)?度)(?![A-Za-z])"
_TEMP_LIST = re.compile(
    rf"(?<![A-Za-z\d.])({_NUMBER}(?:\s*(?:,\s+and\b|[、,，/和及与&]|\band\b)\s*{_NUMBER})*)"
    rf"\s*{_CELSIUS}", re.I,
)
_DURATION = re.compile(
    rf"(?<![A-Za-z\d.])({_NUMBER})\s*(h(?:ours?)?\b|min(?:utes?)?\b|小时|分钟)", re.I,
)
_TABLE = re.compile(r"(?<![A-Za-z])table\s*(s?\d+)(?![A-Za-z0-9])|表\s*(s?\d+)", re.I)
_FILE_OR_DOI = re.compile(r"[^\s/\\]+\.(?:pdf|txt)\b|10\.\d{4,9}/[\w./-]+", re.I)
_DATA_WORD = re.compile(r"数据|数值|\b(?:data|values)\b", re.I)
_DATA_SYNTAX = re.compile(
    r"请问?|帮我|给我|列出|看看?|读取|提取|给出|显示|查看|数据|数值|配比|样品|表中|里|中|的|在|下"
    r"|所有|全部"
    r"|\b(?:please|what|are|is|give|show|me|list|read|report|all|the|data|values|"
    r"for|from|of|in|at|on|sample)\b|['’]s\b|[\s,、，:：。.!?？！()]",
    re.I,
)
_IDENTIFIER = re.compile(
    r"(?<![A-Za-z0-9_.-])(?:[A-Za-z][A-Za-z0-9()\[\]/_-]*|\d{1,3}%)(?![A-Za-z0-9_.-])",
)
_METRICS = {
    "mobility": re.compile(r"迁移率|\bmobilit(?:y|ies)\b", re.I),
    "threshold": re.compile(r"阈值电压|\b(?:threshold voltage|Vth)\b", re.I),
    "ratio": re.compile(r"开关比|\bon\s*[/\-:]\s*off\b", re.I),
}
_LABELS = {"mobility": "迁移率", "threshold": "阈值电压", "ratio": "开关比"}
_TEMP_KINDS = {
    "annealing": (r"退火|\banneal", "退火温度"),
    "substrate": (r"基[底板片]|衬底|\bsubstrate|\bdeposition", "衬底温度"),
    "measurement": (
        r"测试温度|测量温度|(?:°\s*C|degrees?\s+C)\s*(?:测试|测量)|"
        r"\b(?:measured|tested)\s+at\b|\b(?:measurement|test) temp",
        "测量温度",
    ),
}
_REGIMES = {
    "linear": (r"线性|\blinear\b", "线性"),
    "saturated": (r"饱和|\bsaturat(?:ed|ion)\b", "饱和"),
}
_CARRIERS = {
    "hole": (r"空穴|\bholes?\b|\bp-channel\b", "空穴"),
    "electron": (r"电子|\belectrons?\b|\bn-channel\b", "电子"),
}
_STATISTICS = {
    "average": (r"平均|\b(?:average|mean|avg)\b", "平均"),
    "maximum": (r"最高|最大|峰值|\b(?:max(?:imum)?|highest|peak)\b", "最高"),
}
_MINUSES = str.maketrans({"−": "-", "–": "-", "—": "-", "‐": "-", "‑": "-"})
_SUPERSCRIPT = str.maketrans("0123456789+-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻")
_UNICODE_EXPONENT = r"[⁺⁻]?[⁰¹²³⁴⁵⁶⁷⁸⁹]+"
_AS_CAST = re.compile(r"未退火|无退火|未经退火|as[- ]cast|unannealed|without annealing", re.I)
_ELECTRODE = re.compile(r"(?<![A-Za-z])(?:Au|Ag)(?![A-Za-z])|金电极|银电极")
_ELECTRODE_LABEL = re.compile(
    rf"(?<![A-Za-z\d/])(?P<metal>Au|Ag)\s*/\s*"
    rf"(?:(?P<temp>{_NUMBER})(?:\s*{_CELSIUS})?|(?P<cast>as[- ]cast))"
    r"(?![A-Za-z\d/]|\.\d|\s*[°度])",
    re.I,
)
_ELECTRODE_ROW = re.compile(
    rf"(Au|Ag)\s*/\s*({_NUMBER}\s*°\s*C|as[- ]cast)", re.I,
)
_SOLVENT = re.compile(r"(?<![A-Za-z0-9])(?:DCB|CB|1\s*-\s*CN)(?![A-Za-z0-9])")
_SOLVENT_SLASH = re.compile(rf"/(?=\s*{_SOLVENT.pattern})")
_ARCHITECTURE = re.compile(r"(?<![A-Za-z])(?:BG|TG)\s*[/–-]\s*(?:BC|TC)(?![A-Za-z])", re.I)
_FOOTNOTE = re.compile(
    r"^(?:[a-z]\s+(?:anneal|average|spin|calculated)|note[:.]|[a-z]Spin)", re.I,
)
_RELATIVE = re.compile(
    r"[<>≤≥≈~]|不[高低少多小大]于|[高低少多小大]于|超过|至少|至多|以下|以上|左右|约[为等于]?"
    r"|(?:范围|区间|介于)|除了?|除外|排除|不含|不包括|而非|而不是|其他(?:样品|材料|聚合物|器件)"
    r"|相差|差值|差异|比值|几倍|多少倍|增(?:长|加|幅)|降低|提升|提高|下降"
    r"|\b(?:above|below|around|approximately|between|excluding|exclude|except"
    r"|at least|at most|less than|more than|greater than|no lower than|no higher than"
    r"|or higher|or lower|or above|or below|rather than|difference"
    r"|increase|decrease|fold|times higher)\b",
    re.I,
)
_EXCLUDED = (
    r"(?:\b(?:without|no|not(?:\s+using)?)\s+|没有\s*|未使用\s*|不使用\s*|不含\s*|无\s*)"
    r"(?:(?:an?|the)\s+)?"
)
_NEGATED_ELECTRODE = re.compile(rf"{_EXCLUDED}(?:{_ELECTRODE.pattern})", re.I)
_UNSUPPORTED_REQUEST = re.compile(
    r"开启电压|开启电流|亚阈值(?:摆幅|斜率)|接触电阻|漏极(?:电压|电流)|栅极(?:电压|电流)"
    r"|偏置|室温|常温|沟道(?:长度|宽度)|可靠性因子|功率转换效率|填充因子|华氏|开尔文|角度|夹角|度角"
    r"|\b(?:subthreshold (?:swing|slope)|contact resistance|turn[- ]on (?:voltage|current)"
    r"|(?:drain|gate|source)[- ](?:bias|voltage|current)|bias(?:ed)?|room temperature"
    r"|ambient temperature|channel (?:length|width)|reliability factor|power conversion efficiency"
    r"|fill factor)\b", re.I,
)


@dataclass(frozen=True)
class TableLookup:
    page: int
    text: str
    answer: str
    table_label: str


@dataclass(frozen=True)
class TableLookupAmbiguity:
    locations: tuple[tuple[str, int], ...]
    measurements: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Glyph:
    text: str
    left: float
    bottom: float
    right: float
    top: float

    @property
    def y(self):
        return (self.bottom + self.top) / 2

    @property
    def height(self):
        return self.top - self.bottom


@dataclass(frozen=True)
class _Rule:
    left: float
    right: float
    y: float
    boundaries: tuple[float, ...] = ()


@dataclass(frozen=True)
class _Field:
    index: int
    kind: str
    unit: str = ""
    regime: str = ""
    carrier: str = ""
    statistic: str = ""
    inline_maximum: bool = False


@dataclass(frozen=True)
class _Table:
    label: str
    caption: str
    headers: tuple[str, ...]
    fields: tuple[_Field, ...]
    rows: tuple[tuple[str, ...], ...]
    complete: bool


def _normalize(text):
    return unicodedata.normalize("NFKC", text).translate(_MINUSES)


def table_sample(question: str) -> str | None:
    """Return an explicit identifier hint for indexed-file selection."""
    candidates = table_samples(question)
    return candidates[0] if len(candidates) == 1 else None


def table_samples(question: str) -> tuple[str, ...]:
    """Return explicit sample identifiers in the order they appear."""
    text = _FILE_OR_DOI.sub(" ", _normalize(question))
    text = _ELECTRODE_LABEL.sub(" ", _TABLE.sub(" ", text))
    text = _SOLVENT_SLASH.sub(" ", text)
    return tuple(dict.fromkeys(
        _sample_token(item) for item in _IDENTIFIER.findall(text)
        if _SOLVENT.fullmatch(item) is None
        and (
            item.endswith("%") or any(c.isdigit() for c in item)
            or "-" in item and item.isupper()
            or item.count("-") >= 2 and sum(segment.isupper() for segment in item.split("-")) >= 2
        )
    ))


def _sample_token(item):
    while item.endswith(")") and item.count(")") > item.count("("):
        item = item[:-1]
    while item.endswith("]") and item.count("]") > item.count("["):
        item = item[:-1]
    return item


def _requested_metrics(question):
    return [key for key, pattern in _METRICS.items() if pattern.search(question)]


def _sample_data_requested(question, samples):
    if not samples or not _TABLE.search(question):
        return False
    text = _TABLE.sub(" ", _FILE_OR_DOI.sub(" ", question))
    text = _TEMP_LIST.sub(" ", _ELECTRODE_LABEL.sub(" ", text))
    for sample in sorted(samples, key=len, reverse=True):
        text = re.sub(_sample_pattern(sample), " ", text, flags=re.I)
    return _DATA_WORD.search(text) is not None and not _DATA_SYNTAX.sub("", text)


def _unsupported_request(question):
    text = _TABLE.sub(" ", _FILE_OR_DOI.sub(" ", question))
    if _RELATIVE.search(text) or _NEGATED_ELECTRODE.search(text) or _UNSUPPORTED_REQUEST.search(
        text
    ) or re.search(
        rf"{_NUMBER}\s*(?:{_CELSIUS}\s*)?(?:-|至|到|\bto\b)\s*{_NUMBER}\s*{_CELSIUS}"
        rf"|\b(?:under|over|about)\s*{_NUMBER}\s*{_CELSIUS}",
        text, re.I,
    ):
        return True
    if re.search(
        rf"{_NUMBER}\s*(?:度\s*)?(?:°\s*[FK]|kelvins?|fahrenheit|[FK])(?![A-Za-z])", text, re.I,
    ):
        return True
    text = _ELECTRODE_LABEL.sub(" ", text)
    text = _DURATION.sub(" ", _TEMP_LIST.sub(" ", text))
    # Standalone quantities require a supported unit and a row-level match.
    text = _IDENTIFIER.sub(" ", text)
    return bool(re.search(r"\d", text))


def table_lookup_requested(question: str) -> bool:
    normalized = _normalize(question)
    if re.search(
        r"最低|最小|为什么|为何|原因|解释|机理|机制|预测|趋势|如何(?:变化|改变|影响)|"
        r"\b(?:minimum|lowest|why|explain|mechanism|predict|trend"
        r"|how (?:do|does|did|can|is|are))\b",
        normalized, re.I,
    ) or _unsupported_request(normalized):
        return False
    samples = table_samples(normalized)
    if any(re.search(rf"{_EXCLUDED}{_sample_pattern(sample)}", normalized, re.I)
           for sample in samples):
        return False
    if not _requested_metrics(normalized):
        return _sample_data_requested(normalized, samples)
    numeric_intent = re.search(
        r"多少|分别|数值|列出|提取|读取|给出|汇总|数据|对比|比较|"
        r"\b(?:what|list|extract|give|report|values|how much|compare)\b", normalized, re.I,
    )
    return bool(
        samples or numeric_intent or _TEMP_LIST.search(normalized) or _TABLE.search(normalized)
        or _ELECTRODE_LABEL.search(normalized),
    )


def _literal_temperature_spans(normalized):
    def mask(match):
        return "\0" * len(match[0])

    # Keep offsets, and prevent numbers on either side from merging into a list.
    return _TEMP_LIST.finditer(_ELECTRODE_LABEL.sub(mask, _FILE_OR_DOI.sub(mask, normalized)))


def table_temperatures(question: str) -> tuple[Decimal, ...]:
    """Extract literal Celsius temperatures outside composite electrode labels."""
    return tuple(dict.fromkeys(
        Decimal(value) for match in _literal_temperature_spans(_normalize(question))
        for value in re.findall(_NUMBER, match[1])
    ))


def without_table_temperatures(question: str) -> str:
    """Mask literal Celsius expressions while preserving other conditions."""
    normalized = _normalize(question)
    parts = []
    end = 0
    for match in _literal_temperature_spans(normalized):
        parts.extend((normalized[end:match.start()], " "))
        end = match.end()
    return "".join((*parts, normalized[end:]))


def _symbols_from_shape(page, glyph):
    """Recover a tiny unmapped operator only from its actual printed shape."""
    width, height = page.get_size()
    if not (
        0 < glyph.right - glyph.left <= 12 and 0 < glyph.height <= 12
        and 0 < glyph.left < glyph.right < width and 0 < glyph.bottom < glyph.top < height
    ):
        return "�"
    crop = (glyph.left, glyph.bottom, width - glyph.right, height - glyph.top)
    with (
        closing(page.render(scale=12, crop=crop, may_draw_forms=False)) as bitmap,
        closing(bitmap.to_pil()) as image,
        closing(image.convert("L")) as gray,
    ):
        yy, xx = np.where(np.asarray(gray) < 145)
    if len(xx) < 8:
        return "�"
    w, h = int(np.ptp(xx)) + 1, int(np.ptp(yy)) + 1
    if w >= 4 * h and len(xx) >= w * h * 0.68:
        return "−"
    if w < 8 or h < 8 or not 0.65 <= w / h <= 1.55:
        return "�"
    x = (xx - xx.min()) / (w - 1)
    y = (yy - yy.min()) / (h - 1)
    horizontal = [i for i in range(h) if (yy == i + yy.min()).sum() >= .78 * w]
    if (horizontal and horizontal[0] < h * .58 and horizontal[-1] > h * .72
            and horizontal[-1] - horizontal[0] > h * .35
            and np.mean(abs(x[y < .2] - .5) < .20) > .9):
        return "±"
    if (
        np.mean(np.minimum(abs(x - y), abs(x + y - 1)) <= 0.19) >= 0.93
        and all(np.any(x_edge & y_edge) for x_edge in (x < .26, x > .74)
                for y_edge in (y < .26, y > .74))
    ):
        return "×"
    radius = ((x - 0.5)**2 + (y - 0.5)**2)**0.5
    if (
        glyph.height < 3.5 and np.min(radius) > 0.18 and np.max(radius) < 0.68
        and all(np.any(mask) for mask in (x < .15, x > .85, y < .15, y > .85))
    ):
        return "°"
    return "�"


def _glyphs(text_page, raw):
    glyphs = []
    for index in range(text_page.count_chars()):
        code = raw.FPDFText_GetUnicode(text_page, index)
        if code > 0x10ffff:
            continue
        char = chr(code)
        if char.isspace():
            continue
        left, bottom, right, top = text_page.get_charbox(index)
        if not (left < right and bottom < top
                and all(map(math.isfinite, (left, bottom, right, top)))):
            continue
        glyphs.append(_Glyph(char, left, bottom, right, top))
    return glyphs


def _lines(glyphs):
    if not glyphs:
        return []
    height = median(g.height for g in glyphs)
    gap = max(1.6, height * .6)
    lines = []
    centers = []
    small = []
    for glyph in sorted(glyphs, key=lambda g: -g.y):
        if glyph.height < .91 * height:
            small.append(glyph)
        elif not lines or centers[-1] - glyph.y > gap:
            lines.append([glyph])
            centers.append(glyph.y)
        else:
            lines[-1].append(glyph)
            centers[-1] += (glyph.y - centers[-1]) / len(lines[-1])
    for glyph in small:
        position = bisect.bisect_left(centers, -glyph.y, key=lambda y: -y)
        near = range(max(0, position - 1), min(position + 1, len(centers)))
        index = min(near, key=lambda i: abs(glyph.y - centers[i]), default=None)
        if index is not None and abs(glyph.y - centers[index]) <= height * 1.15:
            lines[index].append(glyph)
    return [sorted(line, key=lambda g: g.left) for line in lines]


def _ordered_text(glyphs, symbol):
    if not glyphs:
        return ""
    gap = max(.8, median(g.height for g in glyphs) * .27)
    parts = [symbol(glyphs[0])]
    for left, right in pairwise(glyphs):
        if right.left - left.right > gap:
            parts.append(" ")
        parts.append(symbol(right))
    return "".join(parts)


def _rules(page, raw):
    raw_rules = []
    for path in page.get_objects(filter=[raw.FPDF_PAGEOBJ_PATH], max_depth=2):
        left, bottom, right, top = path.get_bounds()
        if right - left >= 20 and 0 <= top - bottom <= 2.7:
            raw_rules.append(_Rule(left, right, (bottom + top) / 2))
    groups = []
    for rule in sorted(raw_rules, key=lambda r: -r.y):
        if not groups or abs(groups[-1][0].y - rule.y) > .55:
            groups.append([rule])
        else:
            groups[-1].append(rule)
    for group in groups:
        joined = []
        for rule in sorted(group, key=lambda r: r.left):
            if joined and abs(joined[-1].right - rule.left) < 2:
                prev = joined.pop()
                boundary = (prev.right + rule.left) / 2
                joined.append(_Rule(prev.left, rule.right, prev.y, (*prev.boundaries, boundary)))
            else:
                joined.append(rule)
        yield from (rule for rule in joined if rule.right - rule.left >= 100)


def _split_cells(glyphs, bounds):
    cells = [[] for _ in range(len(bounds) + 1)]
    for glyph in glyphs:
        index = bisect.bisect_right(bounds, (glyph.left + glyph.right) / 2)
        cells[index].append(glyph)
    return cells


_SAMPLE_HEADER = re.compile(
    r"(?:films?|samples?|devices?|compounds?|osc|polymers?(?:\(solvents?\))?|materials?|样品|器件|聚合物)"
)
_MOBILITY_UNIT = re.compile(r"(c?m)\^?2(?:/\(?vs\)?|v\^?-1s\^?-1)")
_NUMBER_TERM = rf"(?:{_NUMBER}(?:[eE][+-]?\d+)?(?:×10{_UNICODE_EXPONENT})?|10{_UNICODE_EXPONENT})"
_MEASUREMENT = re.compile(
    rf"(?P<main>[≈~<>≤≥]?{_NUMBER_TERM})"
    rf"(?:(?P<operator>[±~−])(?P<second>{_NUMBER_TERM})|\(±(?P<error>{_NUMBER_TERM})\))?"
    rf"(?:,?\((?P<maximum>{_NUMBER_TERM})\))?"
)
_SAMPLE_WORD = r"[A-Za-z][A-Za-z0-9()\[\]/_-]*|\d{1,3}%"
_ROW_ANNEALING = re.compile(rf"(?P<temp>{_NUMBER})\s*°\s*C", re.I)


def _compact(text):
    return re.sub(r"[\s·⋅*_]", "", _normalize(text)).casefold()


def _choices(patterns, text):
    return {key for key, (pattern, _) in patterns.items() if re.search(pattern, text, re.I)}


def _field(index, header):
    value = _compact(header)
    if _SAMPLE_HEADER.fullmatch(value):
        return _Field(index, "sample")
    if re.fullmatch(r"(?:ts|substrate(?:temp\.?|temperature)|衬底温度|基底温度)\(?°c\)?", value):
        return _Field(index, "substrate", "°C")
    if re.fullmatch(r"(?:taa?|anneal(?:ing)?(?:temp\.?|temperature)|退火温度)\(?°c(/h)?\)?", value):
        return _Field(index, "annealing", "°C/h" if "/h" in value else "°C")
    if re.fullmatch(
        r"(?:(?:measurement|test)(?:temp\.?|temperature)|测量温度|测试温度)\(?°c\)?", value,
    ):
        return _Field(index, "measurement", "°C")
    if re.fullmatch(
        r"(?:average|mean|avg)?(?:vt|vth|thresholdvoltage|阈值电压)[,(\[]?v[)\]]?", value,
    ):
        statistic = "average" if value.startswith(("average", "mean", "avg")) else ""
        return _Field(index, "threshold", "V", statistic=statistic)
    if re.fullmatch(r"i?d?on[:/-](?:i?d?)?off(?:current)?(?:ratio)?|开关比", value):
        return _Field(index, "ratio")
    unit = _MOBILITY_UNIT.search(value)
    if not unit or not re.fullmatch(r"[)\]]*", value[unit.end():]):
        return None
    label = value[:unit.start()]
    if not re.fullmatch(
        r"(?:[a-zμ,.()]+|迁移率)[(\[]?", label,
    ) or not re.search(r"mobility|^mavg|μ|mfe|迁移率", label):
        return None
    regime = "linear" if "linear" in label or "lin." in label else ""
    if "saturat" in label or "sat." in label:
        if regime:
            return None
        regime = "saturated"
    carrier = "hole" if "μh" in label or "hole" in label else ""
    if "μe" in label or "electron" in label:
        if carrier:
            return None
        carrier = "electron"
    inline_maximum = bool(re.search(r"\([^)]*[mμ](?:fe|sat)[^)]*max", label))
    statistic = ""
    if re.search(r"avg|ave|average|mean", label) or inline_maximum:
        statistic = "average"
    elif "max" in label:
        statistic = "maximum"
    return _Field(
        index, "mobility", f"{unit[1]}²/(V·s)", regime, carrier, statistic, inline_maximum,
    )


def _numeric(glyphs, symbol):
    if not glyphs:
        return None
    chars = [symbol(g) for g in glyphs]
    value = "".join(chars)
    if value.casefold() in {"nofet", "n/a", "na", "nr", "-", "–", "—"}:
        return {"nofet": "NO FET", "nr": "NR"}.get(value.casefold(), value)
    printed = _printed_exponents(glyphs, chars)
    if printed is None:
        return None
    value = printed.translate(str.maketrans({"–": "−", "∼": "~"}))
    value = value.replace("x", "×").replace("X", "×").replace("*", "×")
    value = re.sub(r"(?<![\d⁰¹²³⁴⁵⁶⁷⁸⁹])−", "-", value)
    if not _MEASUREMENT.fullmatch(value):
        return None
    return value


def _printed_exponents(glyphs, chars):
    value = "".join(chars)
    if len(value) != len(glyphs):
        return None
    exponent_digits = set()
    for match in reversed(list(re.finditer(r"10([+−-]?\d+)", value))):
        start, end = match.span(1)
        base = glyphs[start - 2:start]
        height = median(g.height for g in base)
        baseline = median(g.bottom for g in base)
        count = 0
        for glyph in glyphs[start:end]:
            if glyph.bottom <= baseline + height * .22 or glyph.height >= height * .96:
                break
            count += 1
        if count:
            exponent_digits.update(range(start, start + count))
            exponent = value[start:start + count].translate(_MINUSES).translate(_SUPERSCRIPT)
            value = value[:start] + exponent + value[start + count:]
    digits = [
        i for i, char in enumerate(chars) if char in "0123456789" and i not in exponent_digits
    ]
    if digits:
        height = max(glyphs[i].height for i in digits)
        baseline = median(glyphs[i].bottom for i in digits if glyphs[i].height > height * .9)
        if any(
            glyphs[i].height < height * .96 and glyphs[i].bottom > baseline + height * .22
            for i in digits
        ):
            return None
    return re.sub(r"10\^([+-]?\d+)", lambda m: "10" + m[1].translate(_SUPERSCRIPT), value)


def _formatted_number(value):
    return (
        value.replace("×", " × ").replace("±", " ± ").replace("-", "−")
        .replace("~", "–").replace(",", ", ").replace("( ± ", "(± ")
    )


def _within(glyphs, left, right, bottom, top):
    return [g for g in glyphs if left <= (g.left + g.right) / 2 <= right and bottom < g.y < top]


def _center(line):
    return median(g.y for g in line)


def _caption_line(line, symbol):
    text = _ordered_text(line, symbol)
    match = _TABLE.match(text)
    if match is None:
        return None
    suffix = text[match.end():].lstrip(" .:")
    if re.match(r"[,)]|(?:shows?|reports?|summariz\w*|this|above|below)\b", suffix, re.I):
        return None
    return f"Table {(match[1] or match[2]).upper()}", text


def _header_blocks(line):
    threshold = max(4.6, median(g.height for g in line) * .8)
    blocks = [[line[0]]]
    for a, b in pairwise(line):
        if b.left - a.right > threshold:
            blocks.append([])
        blocks[-1].append(b)
    return blocks


def _column_bounds(line, header, body, rule, symbol):
    blocks = _header_blocks(line)
    # Some tables leave the sample column unlabelled. Require multiple rows
    # with text in the same uncovered area before creating a column for it.
    if blocks[0][0].left - rule.left > 25:
        edge = blocks[0][0].left - 4
        names = [[g for g in row if g.right < edge] for row in body[:5]]
        names = [name for name in names if len(name) >= 2 and name[0].text.isalpha()]
        if len(names) >= 2:
            blocks.insert(0, max(names, key=lambda name: name[-1].right))
    intervals = [(a[-1].right, b[0].left) for a, b in pairwise(blocks)]
    body = body[:10]
    for i, row in enumerate(body):
        if _FOOTNOTE.match(_ordered_text(row, symbol)):
            body = body[:i]
            break
    bounds = []
    for left, right in intervals:
        gaps = [(left, right)]
        # Body runs define the data gutters. Header text can extend into
        # them, so use it to refine boundaries only after finding a gutter.
        for glyph in (g for row in body for g in row):
            if glyph.left >= right or glyph.right <= left:
                continue
            gaps = [
                part for start, end in gaps
                for part in ((start, min(end, glyph.left)), (max(start, glyph.right), end))
                if part[1] - part[0] >= .7
            ]
        if not gaps:
            return ()
        for block in (block for line in header for block in _header_blocks(line)):
            narrowed = [
                part for start, end in gaps
                for part in ((start, min(end, block[0].left)), (max(start, block[-1].right), end))
                if part[1] - part[0] >= .7
            ]
            if narrowed:
                gaps = narrowed
        start, end = max(gaps, key=lambda interval: interval[1] - interval[0])
        bounds.append((start + end) / 2)
    return tuple(bounds)


def _headers(lines, bounds, symbol):
    columns = [[] for _ in range(len(bounds) + 1)]
    for line in lines:
        for block in _header_blocks(line):
            left = bisect.bisect_right(bounds, block[0].left)
            right = bisect.bisect_left(bounds, block[-1].right)
            if left == right:
                columns[left].append(_ordered_text(block, symbol))
    return tuple(" ".join(column) for column in columns)


def _numeric_gap(glyphs):
    for a, b in pairwise(glyphs):
        if (
            (a.text.isdigit() or a.text == ".") and (b.text.isdigit() or b.text == ".")
            and b.left - a.right > max(1.5, .72 * max(a.height, b.height))
        ):
            return True
    return False


def _render_cell(glyphs, field, symbol):
    if not glyphs:
        return ""
    if field and field.kind in _METRICS:
        return "" if _numeric_gap(glyphs) else (_numeric(glyphs, symbol) or "")
    text = _ordered_text(glyphs, symbol)
    if field and field.kind == "sample" and re.match(r"\s*(?:Au|Ag)\s*/", text, re.I):
        numeric = [g for g in glyphs if g.text in "0123456789."]
        if numeric and not _numeric_gap(numeric) and _numeric(numeric, symbol) is not None:
            text = re.sub(r"(?<=[\d.])\s+(?=[\d.])", "", text)
    if field and field.kind in _TEMP_KINDS and re.fullmatch(r"[\d./+−\s-]+", text):
        return re.sub(r"\s+", "", text).translate(_MINUSES)
    return text


def _parse_rows(lines, bounds, fields, symbol, *, bounded):
    sample = next(f.index for f in fields if f.kind == "sample")
    temp = next((f.index for f in fields if f.kind in _TEMP_KINDS), None)
    metric_indices = [f.index for f in fields if f.kind in _METRICS]
    by_index = {f.index: f for f in fields}
    rows = []
    y_values = []
    complete = bounded
    for line in lines[:120]:
        y = _center(line)
        if y_values and y_values[-1] - y > max(25, 3.2 * median(g.height for g in line)):
            break
        if rows and _FOOTNOTE.match(_ordered_text(line, symbol)):
            break
        crossings = [
            i for i, bound in enumerate(bounds)
            if any(g.left + .3 < bound < g.right - .3 for g in line)
        ]
        # A full-width line crossing several separators cannot be a table row.
        if len(crossings) >= 2:
            if not rows:
                return (), False
            raw_text = _ordered_text(line, symbol)
            if re.match(r"\s*\d+\s+[A-Za-z]", raw_text):
                complete = False
            break
        cells = _split_cells(line, bounds)
        row = [_render_cell(cell, by_index.get(i), symbol) for i, cell in enumerate(cells)]
        if any(sample in (i, i + 1) for i in crossings):
            return (), False
        for i in crossings:
            for j in (i, i + 1):
                if j in by_index:
                    row[j] = ""
        name = row[sample]
        has_metric = any(row[i] for i in metric_indices)
        if name and not has_metric and rows and re.match(
            r"after annealing|annealing at|without annealing|annealing\b", name, re.I,
        ) and all(not cell for i, cell in enumerate(cells) if i != sample):
            rows[-1][sample] += " " + name
        elif name and has_metric:
            rows.append(row)
        elif not name and has_metric and rows and temp is not None and row[temp]:
            row[sample] = rows[-1][sample]
            rows.append(row)
        elif name:
            if not rows:
                return (), False
            complete = False
            if not re.match(_SAMPLE_WORD, name):
                break
            rows.append(row)
        elif has_metric:
            complete = False
        y_values.append(y)
    return tuple(tuple(row) for row in rows), complete and len(lines) <= 120


def _parse_table(label, caption, header, body, rule, divider, symbol, *, bounded):
    if not body or not header:
        return None
    layouts = [divider.boundaries] if divider and divider.boundaries else []
    layouts.extend(_column_bounds(line, header, body, rule, symbol) for line in reversed(header))
    tables = []
    for bounds in dict.fromkeys(layouts):
        if not 1 <= len(bounds) <= 20:
            continue
        headers = _headers(header, bounds, symbol)
        fields = tuple(field for i, text in enumerate(headers) if (field := _field(i, text)))
        if not any(f.kind in _METRICS for f in fields):
            continue
        if not any(f.kind == "sample" for f in fields) and (
            not headers[0] or re.fullmatch(
                rf"(?:{_SAMPLE_WORD})(?:\s+[A-Za-z][A-Za-z0-9]*){{1,4}}", headers[0],
            )
        ):
            fields = (_Field(0, "sample"), *fields)
        if (
            sum(f.kind == "sample" for f in fields) != 1
            or len({f.index for f in fields}) != len(fields)
        ):
            continue
        rows, complete = _parse_rows(body, bounds, fields, symbol, bounded=bounded)
        if rows:
            tables.append(_Table(label, caption, headers, fields, rows, complete))
    return max(tables, key=lambda t: (len(t.fields), len(t.rows), t.complete), default=None)


def _matching_span(a, b):
    return abs(a.left - b.left) <= 2 and abs(a.right - b.right) <= 2


def _table_rank(table):
    return (table.complete, len(table.rows), len(table.fields))


def _table_observations(table):
    sample = next(f.index for f in table.fields if f.kind == "sample")
    conditions = tuple(f for f in table.fields if f.kind in _TEMP_KINDS)
    observations = {}
    for row in table.rows:
        condition = tuple((f.kind, row[f.index]) for f in conditions)
        for f in table.fields:
            if f.kind in _METRICS and row[f.index]:
                key = (row[sample], condition, f.kind, f.regime, f.carrier, f.statistic)
                observations.setdefault(key, set()).add((f.unit, row[f.index]))
    return observations


def _choose_table(tables):
    best = max(tables, key=_table_rank)
    verified = _table_observations(best)
    for table in tables:
        observations = _table_observations(table)
        shared = verified.keys() & observations.keys()
        conflict = any(verified[key] != observations[key] for key in shared)
        if conflict or (_table_rank(table) == _table_rank(best) and verified != observations):
            # Keep the caption so a competing interpretation cannot be mistaken
            # for a different, uniquely matching table during document lookup.
            return _Table(best.label, best.caption, best.headers, best.fields, (), False)
    return best


def _page_tables(page, raw, table_label):
    with closing(page.get_textpage()) as text_page:
        if text_page.count_chars() > 50_000 or not _TABLE.search(text_page.get_text_bounded()):
            return []
        glyphs = _glyphs(text_page, raw)
    symbols = {}

    def symbol(glyph):
        # Some publisher fonts map the round degree glyph to a digit or the
        # plus-minus glyph to a parenthesis. Their ink bounds distinguish
        # them from ordinary glyphs with the same decoded character.
        suspect = (
            glyph.text == "1" and 1.3 < glyph.height < 3.5
            and .9 <= (glyph.right - glyph.left) / glyph.height <= 1.1
            or glyph.text == "(" and 4 < glyph.height < 8
            and .8 <= (glyph.right - glyph.left) / glyph.height <= 1.2
        )
        if unicodedata.category(glyph.text) not in {"Cc", "Co"} and not suspect:
            return glyph.text
        if glyph not in symbols:
            symbol_found = _symbols_from_shape(page, glyph) if len(symbols) < 200 else "�"
            symbols[glyph] = glyph.text if suspect and symbol_found == "�" else symbol_found
        return symbols[glyph]

    def region_lines(rule, bottom, top):
        return _lines(_within(glyphs, rule.left - 1, rule.right + 1, bottom, top))

    results = {}
    rules = list(_rules(page, raw))
    for top in rules:
        above = region_lines(top, top.y, top.y + 58)
        below = region_lines(top, max(0, top.y - 65), top.y)
        caption = None
        caption_line = None
        above_title = []
        if not below:
            continue
        inside = _caption_line(below[0], symbol) if top.y - _center(below[0]) <= 24 else None
        if inside:
            caption = inside
            caption_line = below[0]
        else:
            for i in reversed(range(len(above))):
                line = above[i]
                if (found := _caption_line(line, symbol)) and top.y - _center(line) >= -55:
                    caption = found
                    caption_line = line
                    above_title = [found[1]]
                    for continuation in above[i + 1:]:
                        if _center(line) - _center(continuation) > 14:
                            break
                        above_title.append(_ordered_text(continuation, symbol))
                        line = continuation
                    break
        if not caption or table_label is not None and caption[0] != table_label:
            continue
        matched = [rule for rule in rules if rule.y < top.y - 3 and _matching_span(rule, top)]
        end = matched[0].y if matched else max(0, top.y - 145)
        bounded = bool(matched)
        divider = None
        if matched and top.y - matched[0].y <= 60:
            divider = matched[0]
            end = matched[1].y if len(matched) > 1 else max(0, divider.y - 140)
            bounded = len(matched) > 1
        lines = region_lines(top, end, top.y)
        title_lines = []
        if inside:
            while lines and (
                _caption_line(lines[0], symbol)
                or _center(below[0]) - _center(lines[0]) <= 14
                and not any(_field(0, _ordered_text(block, symbol))
                            for block in _header_blocks(lines[0]))
            ):
                title_lines.append(_ordered_text(lines.pop(0), symbol))
        else:
            title_lines = above_title
        if not lines:
            continue
        for index, line in enumerate(lines):
            if _caption_line(line, symbol):
                lines = lines[:index]
                break
        candidates = (
            [sum(_center(line) > divider.y for line in lines)] if divider
            else range(1, min(6, len(lines)))
        )
        bounded |= any(_FOOTNOTE.match(_ordered_text(line, symbol)) for line in lines)
        tables = [
            result for i in candidates
            if (result := _parse_table(
                caption[0], "\n".join(title_lines), lines[:i], lines[i:], top, divider, symbol,
                bounded=bounded,
            ))
        ]
        if tables:
            # Use the caption's printed position, since multiple physical
            # tables can have the same number and identical caption text.
            anchor = (caption[0], caption_line[0], caption_line[1])
            results.setdefault(anchor, []).extend(tables)
    return [_choose_table(tables) for tables in results.values()]


def _name_key(text):
    return re.sub(r"[\s-]+", "", _normalize(text)).casefold()


def _sample_pattern(sample):
    pieces = re.split(r"[-\s]+", _normalize(sample))
    pattern = r"[\s-]*".join(re.escape(piece) for piece in pieces)
    return rf"(?<![A-Za-z0-9_/-]){pattern}(?![A-Za-z0-9_-]|/(?!\s*{_SOLVENT.pattern}))"


def _electrode_row(text):
    match = _ELECTRODE_ROW.fullmatch(text.strip())
    if not match:
        return None
    metal, value = match.groups()
    if _AS_CAST.search(value):
        return metal.title(), None
    printed = re.sub(r"\s+", "", value)
    return metal.title(), Decimal(printed[:-2])


def _row_name(row, header, electrode_label):
    if electrode_label is not None:
        return header
    match = re.match(_SAMPLE_WORD, _SOLVENT_SLASH.sub(" ", row.strip()))
    if match:
        return match[0]
    return ""


def _condition_label(text):
    label = _electrode_row(text)
    return text if label is None else _format_electrode(*label)


def _format_electrode(metal, temp):
    return f"{metal}/as-cast" if temp is None else f"{metal}/{temp} °C"


def _condition_value(field, value):
    if not value:
        return None
    if field.unit == "°C/h":
        value = value.split("/", 1)[0]
    return Decimal(value) if re.fullmatch(_NUMBER, value) else None


def _electrodes(text):
    return {{"金电极": "Au", "银电极": "Ag"}.get(item, item) for item in _ELECTRODE.findall(text)}


def _solvents(text):
    return {item.replace(" ", "") for item in _SOLVENT.findall(text)}


def _architectures(text):
    return {re.sub(r"\s|[/–-]", "", item).upper() for item in _ARCHITECTURE.findall(text)}


def _minutes(text):
    return {
        Decimal(number) * (60 if unit.lower().startswith(("h", "小")) else 1)
        for number, unit in _DURATION.findall(text)
    }


def _explicit_temperature_kind(question, has_temperatures):
    kinds = _choices(_TEMP_KINDS, question)
    if has_temperatures and re.search(
        rf"(?:测得|测试|测量)\s*(?:温度|为|在)|{_CELSIUS}\s*(?:的\s*)?(?:测量|测试|测得)",
        question, re.I,
    ):
        kinds.add("measurement")
    return kinds


def _explicit_pairs(question, named, parse_option):
    if not named:
        return None
    pattern = "|".join(_sample_pattern(name) for name in sorted(named, key=len, reverse=True))
    mentions = list(re.finditer(pattern, question, re.I))
    keys = {_name_key(name): name for name in named}
    pairs = {
        (keys[_name_key(match[0])], option)
        for match in mentions
        if (option := parse_option(question[match.end():])) is not None
    }
    return pairs or None


def _solvent_pairs(question, named, solvents):
    def parse(text):
        match = re.match(
            rf"\s*(?:\(\s*({_SOLVENT.pattern})\s*\)|/\s*({_SOLVENT.pattern}))", text,
        )
        return (match[1] or match[2]).replace(" ", "") if match else None

    pairs = _explicit_pairs(question, named, parse)
    if pairs:
        return pairs if (
            {name for name, _ in pairs} == named and {solvent for _, solvent in pairs} == solvents
        ) else None
    if len(named) == 1:
        return {(name, solvent) for name in named for solvent in solvents}
    return None


def _temperature_pairs(question, named, temps):
    def parse(text):
        match = re.match(r"\s*(?:在|\bat\b|@)\s*", text, re.I)
        label = _TEMP_LIST.match(text, match.end()) if match else None
        values = re.findall(_NUMBER, label[1]) if label else []
        return Decimal(values[0]) if len(values) == 1 else None

    pairs = _explicit_pairs(question, named, parse)
    if pairs and len(temps) > 1 and len(named) > 1:
        return pairs if (
            {name for name, _ in pairs} == named and {temp for _, temp in pairs} == set(temps)
        ) else None
    if len(named) == 1 or len(temps) == 1:
        return {(name, temp) for name in named for temp in temps}
    return None


def _electrode_pairs(question):
    return tuple(dict.fromkeys(
        (match["metal"].title(), Decimal(match["temp"]) if match["temp"] is not None else None)
        for match in _ELECTRODE_LABEL.finditer(question)
    ))


def table_electrode_conditions(question: str) -> tuple[str, ...]:
    """Return composite electrode labels as discovery hints, separate from sample names."""
    return tuple(_format_electrode(*pair) for pair in _electrode_pairs(_normalize(question)))


def _select_rows(table, question, temps):
    sample = next(f for f in table.fields if f.kind == "sample")
    header = table.headers[sample.index]
    labels = [_electrode_row(row[sample.index]) for row in table.rows]
    # A column made entirely of composite labels names the material in its header.
    electrode_labels = bool(labels) and all(label is not None for label in labels)
    labels = labels if electrode_labels else [None] * len(labels)
    names = [_row_name(row[sample.index], header, label)
             for row, label in zip(table.rows, labels, strict=True)]
    electrode_pairs = set(_electrode_pairs(question))
    qualifier = _ELECTRODE_LABEL.sub(" ", question)
    explicitly_all = bool(re.search(
        r"各(?:聚合物|样品|材料|化合物|器件)|所有|\ball\b", question, re.I,
    ))
    if explicitly_all and not table.complete:
        return None
    named = {name for name in names if name and re.search(_sample_pattern(name), question, re.I)}
    requested = {_name_key(name) for name in table_samples(question)}
    solvents = _solvents(qualifier)
    requested -= {_name_key(solvent) for solvent in solvents}
    if requested and not requested.issubset({_name_key(name) for name in named}):
        return None
    if not named:
        if electrode_pairs and electrode_labels:
            named = {header}
        elif explicitly_all and _TABLE.search(question) and table.complete:
            named = {name for name in names if name}
        else:
            return None
    kinds = _explicit_temperature_kind(question, bool(temps or electrode_pairs))
    if len(kinds) > 1:
        return None
    kind = next(iter(kinds), "")
    conditions = [f for f in table.fields if f.kind in _TEMP_KINDS]
    if len(conditions) > 1:
        conditions = [f for f in conditions if f.kind == kind]
    if len(conditions) > 1:
        return None
    field = conditions[0] if conditions else None
    if field and kind and field.kind != kind:
        return None
    if field and temps and not kind and field.kind != "substrate":
        return None
    as_cast = bool(_AS_CAST.search(qualifier))
    if as_cast and temps:
        return None
    if (kind or temps) and not field and kind not in {"annealing", ""}:
        return None
    electrodes = _electrodes(qualifier)
    architectures = _architectures(qualifier)
    minutes = _minutes(qualifier)
    if len(architectures) > 1 or len(minutes) > 1:
        return None
    if electrode_pairs and (
        not electrode_labels or field or temps or electrodes or as_cast or solvents
    ):
        return None
    if len(electrodes) > 1 and len(named) > 1:
        return None
    solvent_pairs = _solvent_pairs(qualifier, named, solvents) if solvents else set()
    temp_pairs = _temperature_pairs(qualifier, named, temps) if temps else set()
    if solvent_pairs is None or temp_pairs is None:
        return None
    if sum(len(group) > 1 for group in (electrodes, solvents, temps)) > 1:
        return None
    covered_electrodes = []
    covered_solvents = []
    covered_temps = []
    selected = []
    for row, name, electrode_label in zip(table.rows, names, labels, strict=True):
        if name not in named:
            continue
        if electrode_pairs and electrode_label not in electrode_pairs:
            continue
        source = row[sample.index]
        row_text = " ".join(row)
        found_electrodes = electrodes & _electrodes(row_text)
        if electrodes and len(found_electrodes) != 1:
            continue
        found_solvents = {pair for pair in solvent_pairs if pair[0] == name
                          and pair[1] in _solvents(row_text)}
        if solvents and len(found_solvents) != 1:
            continue
        architecture_source = _architectures(row_text) or _architectures(table.caption)
        if architectures and not architectures.intersection(architecture_source):
            continue
        embedded = (
            _ROW_ANNEALING.search(source)
            if re.search(r"\bannealing\b", source, re.I)
            or re.search(r"\bannealing temperatures?\b", table.caption, re.I)
            else None
        )
        if field:
            temp = _condition_value(field, row[field.index])
        elif embedded:
            temp = Decimal(embedded["temp"])
        else:
            temp = electrode_label[1] if electrode_label else None
        if minutes:
            durations = _minutes(source)
            if field and field.unit == "°C/h" and "/" in row[field.index]:
                durations |= _minutes(row[field.index].split("/", 1)[1] + " h")
            if not minutes.intersection(durations):
                continue
        if temp_pairs and (name, temp) not in temp_pairs:
            continue
        if as_cast and not _AS_CAST.search(source):
            continue
        if kind == "annealing" and not as_cast and (
            not field and not embedded or temp is None
        ):
            continue
        selected.append((row, name, temp))
        covered_electrodes.extend([electrode_label] if electrode_pairs else found_electrodes)
        covered_solvents.extend(found_solvents)
        covered_temps.append((name, temp))
    if not selected or {name for _, name, _ in selected} != named:
        return None
    if electrodes and set(covered_electrodes) != electrodes:
        return None
    for observed, expected in (
        (covered_electrodes, electrode_pairs),
        (covered_solvents, solvent_pairs),
        (covered_temps, temp_pairs),
    ):
        if expected and (len(observed) != len(expected) or set(observed) != expected):
            return None
    if not temps:
        keys = [(row[sample.index], row[field.index] if field else "") for row, _, _ in selected]
        if len(keys) != len(set(keys)):
            return None
        if (
            "maximum" in _choices(_STATISTICS, question) and not explicitly_all
            and not (electrode_pairs or solvent_pairs or electrodes)
            and len(selected) > len(named) and not re.search(r"分别|各|\beach\b", question, re.I)
        ):
            return None
    return sample, field, selected


def _statistics_by_metric(question, requested):
    per_metric = {metric: set() for metric in requested}
    remaining = question
    for metric in (item for item in requested if item != "mobility"):
        for statistic, (pattern, _) in _STATISTICS.items():
            expression = re.compile(
                rf"(?:{pattern})\s*(?:的\s*)?(?:{_METRICS[metric].pattern})", re.I,
            )
            remaining, count = expression.subn(" ", remaining)
            if count:
                per_metric[metric].add(statistic)
    unbound = _choices(_STATISTICS, remaining)
    if "mobility" in requested or len(requested) == 1:
        per_metric["mobility" if "mobility" in requested else requested[0]].update(unbound)
    elif unbound:
        return None
    return per_metric


def _select_metrics(fields, question, *, alternatives=None):
    requested = _requested_metrics(question)
    statistics = _statistics_by_metric(question, requested)
    regimes = _choices(_REGIMES, question)
    carriers = _choices(_CARRIERS, question)
    if statistics is None:
        return None
    selected = []
    for metric in requested:
        candidates = [f for f in fields if f.kind == metric]
        if metric != "mobility":
            if len(candidates) != 1 or statistics[metric] - {candidates[0].statistic}:
                return None
            selected.append((candidates[0], False))
            continue
        if sum(len(group) > 1 for group in (regimes, carriers, statistics[metric])) > 1:
            return None
        for regime in sorted(regimes) or [""]:
            for carrier in sorted(carriers) or [""]:
                for statistic in sorted(statistics[metric]) or [""]:
                    matching = [
                        (f, statistic == "maximum" and f.inline_maximum)
                        for f in candidates
                        if (not regime or f.regime == regime)
                        and (not carrier or f.carrier == carrier)
                        and (
                            not statistic or f.statistic == statistic
                            or statistic == "maximum" and f.inline_maximum
                        )
                    ]
                    if len(matching) != 1:
                        if len(matching) > 1 and alternatives is not None:
                            alternatives.extend(matching)
                        return None
                    selected.append(matching[0])
    return selected


def _metric_label(field, use_inline_maximum):
    prefix = "".join(group[key][1] for group, key in (
        (_REGIMES, field.regime), (_CARRIERS, field.carrier),
        (_STATISTICS, "maximum" if use_inline_maximum else field.statistic),
    ) if key)
    unit = f"（{field.unit}）" if field.unit else ""
    return prefix + _LABELS[field.kind] + unit


def _measurement_value(value, field, use_inline_maximum):
    parsed = _MEASUREMENT.fullmatch(value)
    if use_inline_maximum:
        return _formatted_number(parsed["maximum"]) if parsed and parsed["maximum"] else ""
    if field.inline_maximum and parsed and parsed["maximum"]:
        value = value[:parsed.start("maximum") - 1].rstrip(",")
    return _formatted_number(value)


def _safe_cell(text):
    return text.replace("|", r"\|")


def _candidate_table(table, question, temps, page, *, conflicts=None):
    sample_data = not _requested_metrics(question)
    if sample_data:
        if not table.complete or not _sample_data_requested(question, table_samples(question)):
            return None
        metrics = [
            (field, maximum) for field in table.fields if field.kind in _METRICS
            for maximum in ((False, True) if field.inline_maximum else (False,))
        ]
    else:
        alternatives = [] if conflicts is not None else None
        metrics = _select_metrics(table.fields, question, alternatives=alternatives)
        if alternatives:
            selection = _select_rows(table, question, temps)
            if selection and all(
                _MEASUREMENT.fullmatch(row[field.index])
                and _measurement_value(row[field.index], field, maximum)
                for row, _, _ in selection[2] for field, maximum in alternatives
            ):
                conflicts.append(TableLookupAmbiguity(
                    ((table.label, page),),
                    tuple(dict.fromkeys(_metric_label(field, maximum)
                                        for field, maximum in alternatives)),
                ))
    selection = _select_rows(table, question, temps) if metrics else None
    if selection is None:
        return None
    sample_field, temp_field, rows = selection
    names = {name for _, name, _ in rows}
    show_sample = len(names) > 1 or not temp_field or any(
        row[sample_field.index] != name for row, name, _ in rows
    )
    labels = ["样品 / 条件"] if show_sample else []
    if temp_field:
        temperature = _TEMP_KINDS[temp_field.kind][1]
        labels.append(temperature + ("（°C） / 时间（h）" if temp_field.unit == "°C/h" else ""))
    labels.extend(_metric_label(field, maximum) for field, maximum in metrics)
    if sample_data and len(labels) != len(set(labels)):
        return None
    sample_label = f"中 **{_safe_cell(next(iter(names)))}** 的" if len(names) == 1 else "中的"
    answer = [f"{table.label} {sample_label}数据如下。", "",
              "| " + " | ".join(labels) + " |", "| " + " | ".join(["---"] * len(labels)) + " |"]
    evidence = []
    for row, _, temp in rows:
        values = [
            _measurement_value(row[field.index], field, maximum) for field, maximum in metrics
        ]
        if not all(values) or sample_data and any(
            not _MEASUREMENT.fullmatch(row[field.index]) for field, _ in metrics
        ):
            return None
        display = [_condition_label(row[sample_field.index])] if show_sample else []
        if temp_field:
            if temp_field.unit == "°C/h":
                display.append(row[temp_field.index])
            else:
                display.append(f"{temp} °C" if temp is not None else row[temp_field.index] or "—")
        answer.append("| " + " | ".join(map(_safe_cell, [*display, *values])) + " |")
        evidence.append(" | ".join(
            _formatted_number(cell) if _MEASUREMENT.fullmatch(cell) else cell for cell in row
        ))
    if sample_data:
        included = {sample_field.index, *(field.index for field, _ in metrics)}
        if temp_field:
            included.add(temp_field.index)
        omitted = [header or f"第 {index + 1} 列" for index, header in enumerate(table.headers)
                   if index not in included]
        if omitted:
            answer.extend(("", "未提取的列：" + "、".join(map(_safe_cell, omitted)) + "。"))
    text = "\n".join([table.caption, " | ".join(table.headers), *evidence])
    return TableLookup(page, text, "\n".join(answer), table.label)


def inspect_pdf_table(
    data: bytes, question: str, *, max_pages: int,
) -> TableLookup | TableLookupAmbiguity | None:
    """Find verifiable observations, or report choices the question doesn't distinguish."""
    if not table_lookup_requested(question) or max_pages <= 0 or len(data) > 100_000_000:
        return None
    try:
        import pypdfium2 as pdfium
        from pypdfium2 import raw
    except ImportError:
        return None
    query = _normalize(question)
    temps = table_temperatures(query)
    tables = {f"Table {(a or b).upper()}" for a, b in _TABLE.findall(query)}
    if len(tables) > 1:
        return None
    table = next(iter(tables), None)
    implicit_electrodes = not table and _electrode_pairs(query)
    try:
        with _PDFIUM_LOCK, pdfium.PdfDocument(data) as pdf:
            if len(pdf) > min(max_pages, 64):
                return None
            results = []
            conflicts: list[TableLookupAmbiguity] = []
            physical_tables = 0
            for index in range(len(pdf)):
                with closing(pdf[index]) as page:
                    for parsed in _page_tables(page, raw, table):
                        physical_tables += 1
                        result = _candidate_table(
                            parsed, query, temps, index + 1, conflicts=conflicts,
                        )
                        if implicit_electrodes and (not parsed.complete or result is None) and (
                            _select_metrics(parsed.fields, query)
                            and _select_rows(parsed, query, temps)
                        ):
                            return None
                        if result is not None:
                            results.append(result)
                if table and physical_tables > 1:
                    return None
                if len(results) + len(conflicts) >= 4:
                    break
            if len(results) > 1 or conflicts:
                locations = dict.fromkeys(
                    [(hit.table_label, hit.page) for hit in results]
                    + [location for conflict in conflicts for location in conflict.locations],
                )
                measurements = dict.fromkeys(
                    label for conflict in conflicts for label in conflict.measurements
                )
                return TableLookupAmbiguity(tuple(locations), tuple(measurements))
            return results[0] if len(results) == 1 else None
    except (OSError, ValueError, pdfium.PdfiumError):
        return None


def lookup_pdf_table(data: bytes, question: str, *, max_pages: int) -> TableLookup | None:
    """Return a direct answer only for a single verifiable digital table."""
    result = inspect_pdf_table(data, question, max_pages=max_pages)
    return result if isinstance(result, TableLookup) else None
