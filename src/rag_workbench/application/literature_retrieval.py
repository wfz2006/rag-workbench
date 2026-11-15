"""Conservative lexical recall for material questions about original PDF passages."""

import re
import unicodedata
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from rag_workbench.domain.models import Chunk, Document

_SPACE = re.compile(r"\s+")
_TRANSLATE = str.maketrans("‐‑–—−", "-----", "\u00ad")
_MOBILITY = r"(?<![a-z])mobilit(?:y|ies|es)(?![a-z])|迁移率"
_SIDE_CHAIN = r"(?<![a-z])side[ -]+chains?(?![a-z])|侧链"
_METRICS = {
    "mobility": re.compile(_MOBILITY, re.I),
    "side_chain": re.compile(_SIDE_CHAIN, re.I),
}
_CUES = {
    "copolymer": re.compile(r"\bcopolymers?\b|共聚物", re.I),
    "evaporation": re.compile(
        r"\bevaporat(?:ion|ed|ing)\b|\bvacuum[- ]+deposit(?:ion|ed)\b|蒸镀", re.I
    ),
    "solution": re.compile(
        r"\bsolution[- ]+(?:process(?:ed|ing)?|deposit(?:ion|ed))\b|溶液法|溶液加工",
        re.I,
    ),
    "microfilm": re.compile(r"\bmicro[- ]?crystalline(?:\W+\w+){0,3}\W+films?\b|微晶薄膜", re.I),
}
_QUERY_CUES = _CUES | {
    "microfilm": re.compile(r"\bmicro[- ]?crystalline(?: thin)? films?\b|微晶薄膜", re.I),
}
_QUERY_CARRIER = re.compile(
    r"(?:(?<![a-z])(?:electron|hole)[ -]+|电子\s*|空穴\s*)(?=" + _MOBILITY + r")",
    re.I,
)
_QUERY_GLUE = re.compile(r"请问|想知道|是多少|为多少|多少|怎么样|如何|的|了|呢|吗|是|在|中")
_ENGLISH_GLUE = re.compile(
    r"^(?:what (?:is|are) (?:the )?|tell me (?:about )?|the |of |for |in )| (?:of|for|in)$",
    re.I,
)
_LOCANT = r"\[\s*\d[\d,.\s-]*[a-z](?:\s*,\s*[a-z])*\s*\]"
_PRINTED_LOCANT = re.compile(rf"(?<=[a-z])\s*{_LOCANT}\s*(?=[a-z])", re.I)
_MATERIAL = re.compile(
    rf"[a-z][a-z0-9]*(?:{_LOCANT}[a-z][a-z0-9]*)?(?:[ -]+[a-z][a-z0-9]*){{0,3}}",
    re.I,
)
_ACRONYM = re.compile(r"\s*[(\[]([A-Z][A-Z0-9]{1,7})\s*[,;)\]]")
_MOBILITY_UNIT = re.compile(r"\d\s*c\s*m\s*2\s*(?:/|v\b)", re.I)
_CLAUSE_BREAK = re.compile(r"[.!?](?=\s|$)|[。！？；;]")
_EVAPORATION_CONTINUATION = re.compile(r"^during the evaporation process\b", re.I)
_FINE_CRYSTALS = re.compile(
    r"\b(?:small|fine|nanoscale|micromet(?:er|re)[- ]?sized)\s+"
    r"(?:needles|crystallites|crystals)\b",
    re.I,
)
_CRYSTALLIZATION = re.compile(r"\bcrystalli[sz](?:ation|ed|ing)\b", re.I)
_FILM = re.compile(r"\bfilms?\b", re.I)
_MAX_GAP = 240


@dataclass(frozen=True)
class _Question:
    material: re.Pattern[str]
    prefix: str
    metric: str
    carrier: str | None
    cues: tuple[str, ...]
    allow_locants: bool


@dataclass(frozen=True)
class _Passage:
    index: int
    page: int | None
    ordinal: int
    text: str
    names: tuple[re.Match[str], ...]


def _normalize(text: str) -> str:
    return _SPACE.sub(" ", unicodedata.normalize("NFKC", text).translate(_TRANSLATE)).strip()


def _parse_question(question: str) -> _Question | None:
    if len(question) > 240:
        return None
    query = _normalize(question).casefold().strip(" ?!。？！，,：:")
    carrier_match = _QUERY_CARRIER.search(query)
    carrier = None
    if carrier_match:
        carrier = "electron" if carrier_match[0].strip(" -") in {"electron", "电子"} else "hole"
        query = _QUERY_CARRIER.sub(" ", query, count=1)
    metrics = [name for name, pattern in _METRICS.items() if pattern.search(query)]
    if len(metrics) != 1:
        return None
    metric = metrics[0]
    query = _METRICS[metric].sub(" ", query, count=1)
    cues = tuple(name for name, pattern in _QUERY_CUES.items() if pattern.search(query))
    for cue in cues:
        query = _QUERY_CUES[cue].sub(" ", query, count=1)
    material = _SPACE.sub(" ", _QUERY_GLUE.sub(" ", query)).strip()
    material = _ENGLISH_GLUE.sub("", material).strip()
    material = re.sub(r"\s*([\[\],])\s*", r"\1", material)
    if not _MATERIAL.fullmatch(material):
        return None
    words = re.findall(r"[a-z]{2,}", _PRINTED_LOCANT.sub("", material))
    if not words or max(map(len, words)) < (8 if len(words) == 1 else 5):
        return None
    if material in {"electron", "hole", "charge carrier", "field effect", "transistor"}:
        return None
    literal = re.escape(material).replace(r"\ ", r"[ -]+")
    literal = literal.replace(r"\[", r"\s*\[\s*").replace(r"\]", r"\s*\]\s*")
    literal = literal.replace(",", r",\s*").replace(r"\-", r"\s*-\s*")
    pattern = re.compile(rf"(?<![a-z0-9_-]){literal}(?![a-z0-9_])", re.I)
    prefix = words[0][:5]
    return _Question(pattern, prefix, metric, carrier, cues, "[" not in material)


def _gap(left: re.Match[str], right: re.Match[str]) -> int:
    return max(0, left.start() - right.end(), right.start() - left.end())


def _same_clause(text: str, left: re.Match[str], right: re.Match[str]) -> bool:
    start, end = sorted((left, right), key=lambda match: match.start())
    return not _CLAUSE_BREAK.search(text, start.end(), max(start.end(), end.start()))


def _metric_matches(query: _Question, text: str) -> list[re.Match[str]]:
    metrics = list(_METRICS[query.metric].finditer(text))
    if not query.carrier:
        return metrics
    carrier = query.carrier
    localized = re.compile(r"(?<![a-z])((?:hole|electron)[- ]+|(?:电子|空穴)\s*)$", re.I)
    transport = re.compile(rf"\b{carrier}[- ]+(?:transport|conduction)\b", re.I)
    transports = list(transport.finditer(text))
    accepted = []
    for metric in metrics:
        relation = localized.search(text[max(0, metric.start() - 14) : metric.start()])
        if relation:
            chinese = "电子" if carrier == "electron" else "空穴"
            if relation[1].casefold().strip(" -") in {carrier, chinese}:
                accepted.append(metric)
        elif any(_gap(metric, span) <= 75 for span in transports):
            accepted.append(metric)
    return accepted


def _close_page(left: _Passage, right: _Passage, pages: int) -> bool:
    if left.page is not None and right.page is not None:
        return abs(left.page - right.page) <= pages
    return abs(left.ordinal - right.ordinal) <= (12 if pages else 2)


def _source_ranking(query: _Question, rows: list[_Passage]) -> list[tuple[tuple, int]]:
    rows.sort(key=lambda row: (row.page or 0, row.ordinal, row.index))
    definitions: dict[str, list[_Passage]] = defaultdict(list)
    for row in rows:
        for name in row.names:
            if definition := _ACRONYM.match(row.text, name.end()):
                definitions[definition[1]].append(row)
    aliases = {
        alias: re.compile(rf"(?<![A-Za-z0-9_]){re.escape(alias)}(?![A-Za-z0-9_])")
        for alias in definitions
    }
    names_by_index = {
        row.index: (
            *row.names,
            *(
                match
                for alias, pattern in aliases.items()
                if any(_close_page(row, site, 2) for site in definitions[alias])
                for match in pattern.finditer(row.text)
            ),
        )
        for row in rows
    }

    def has_cue(row: _Passage, cue: str) -> bool:
        return any(
            _gap(name, match) <= _MAX_GAP and _same_clause(row.text, name, match)
            for name in names_by_index[row.index]
            for match in _CUES[cue].finditer(row.text)
        )

    def microstructure(position: int) -> bool:
        row = rows[position]
        if has_cue(row, "microfilm"):
            return True
        # Some papers describe the film's small crystals or needles instead of
        # naming it microcrystalline. Require the crystallization discussion too.
        for pattern in (_FILM, _FINE_CRYSTALS):
            if not any(
                _gap(name, match) <= _MAX_GAP and _same_clause(row.text, name, match)
                for name in names_by_index[row.index]
                for match in pattern.finditer(row.text)
            ):
                return False
        return any(
            _close_page(row, other, 0) and _CRYSTALLIZATION.search(other.text)
            for other in rows[max(0, position - 1) : position + 2]
        )

    ranked = []
    for position, row in enumerate(rows):
        names = names_by_index[row.index]
        if not names:
            continue
        metrics = _metric_matches(query, row.text)
        distance = min((_gap(name, metric) for name in names for metric in metrics), default=999)
        if distance > _MAX_GAP:
            continue
        direct = 0
        for cue in query.cues:
            if has_cue(row, cue) or (
                cue == "evaporation" and _EVAPORATION_CONTINUATION.search(row.text)
            ):
                direct += 1
                continue
            radius = 16 if cue == "microfilm" else 2
            nearby = range(max(0, position - radius), min(len(rows), position + radius + 1))
            if not any(
                _close_page(row, rows[j], int(cue == "microfilm"))
                and (microstructure(j) if cue == "microfilm" else has_cue(rows[j], cue))
                for j in nearby
            ):
                break
        else:
            # Prefer a passage containing measured mobility over a title, then
            # favor locally supported constraints and the spelled-out material.
            units = query.metric == "mobility" and _MOBILITY_UNIT.search(row.text) is not None
            rank = (-int(units), -direct, -int(bool(row.names)), -len(metrics), distance)
            ranked.append((rank, row.index))
    return ranked


def ranked_literature_matches(
    question: str, chunks: Sequence[Chunk], catalog: Mapping[str, Document]
) -> list[int]:
    """Return best-first PDF indices with a material and locally supported question cues."""
    query = _parse_question(question)
    if query is None:
        return []
    sources: dict[tuple[str, str], list[tuple[int, Chunk, int | None]]] = defaultdict(list)
    anchored: dict[int, _Passage] = {}
    for index, chunk in enumerate(chunks):
        source = catalog.get(chunk.document_id)
        if source is None or source.extension != ".pdf":
            continue
        page = source.source_page or chunk.page
        key = ("group", source.source_group) if source.source_group else ("document", source.id)
        sources[key].append((index, chunk, page))
        if query.prefix not in unicodedata.normalize("NFKC", chunk.text).casefold():
            continue
        text = _normalize(chunk.text)
        if query.allow_locants and "[" in text:
            # The short printed name can omit ring locants; an explicit locant
            # in the question must match its spelling and ordering in the PDF.
            text = _PRINTED_LOCANT.sub("", text)
        if names := tuple(query.material.finditer(text)):
            anchored[index] = _Passage(index, page, chunk.ordinal, text, names)
    ranked = [
        entry
        for rows in sources.values()
        if any(index in anchored for index, _, _ in rows)
        for entry in _source_ranking(
            query,
            [
                anchored[index]
                if index in anchored
                else _Passage(index, page, chunk.ordinal, _normalize(chunk.text), ())
                for index, chunk, page in rows
            ],
        )
    ]
    return [index for _, index in sorted(ranked)]
