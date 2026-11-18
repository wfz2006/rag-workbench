"""Find English preparation evidence for Chinese questions naming one material."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from rag_workbench.domain.models import Chunk, Document
from rag_workbench.infrastructure.lexical import identifier_text

_CHANGE_QUESTION = re.compile(
    r"(?:微观结构|微结构|结构|形貌)"
    r"(?:上|的|是|会|怎么|怎样|如何|发生|出现|有|了|哪些|什么|\s){0,8}"
    r"(?:变化|演变|改变|转变)",
)
_PREPARATION_QUESTION = re.compile(r"制备|制程|加工|成膜|合成|后处理")
_QUESTION_GLUE = re.compile(
    r"聚合物半导体|聚合物|有机半导体|半导体|材料|请问|想知道|请说明|"
    r"有哪些|有什么|过程|阶段|步骤|之间|期间|前后|怎么|怎样|如何|"
    r"[在的中时下前后了是呢吗及和与\s，。？?！!、,：:]",
)
_SAMPLE = re.compile(r"(?:[a-z]+\([a-z0-9][a-z0-9-]*\)|[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*)")
_PHASES = (
    ("gelation", r"凝胶化|凝胶形成|成胶", r"\b(?:gelation|gelled|gelling|gel)\b"),
    (
        "solvent_exchange", r"溶剂(?:交换|置换|替换)",
        r"\b(?:solvent[ -]+(?:exchange|replacement)|exchange of (?:the )?solvent)\b",
    ),
    ("crosslinking", r"(?:紫外(?:光)?|uv\s*)?交联", r"\bcross[ -]?link(?:ed|ing|s)?\b"),
    ("annealing", r"退火", r"\banneal(?:ed|ing)?\b"),
)
_PREPARATION = re.compile(
    r"\b(?:prepar(?:ation|e[ds]?)|fabricat(?:ion|e[ds]?)|processing|"
    r"(?:spin|dip)[ -]+coat(?:ed|ing)?|film formation|cast(?:ing)?|deposition)\b",
)
_NUMBERED_STAGE = re.compile(r"\bstage\s+([1-9]\d?)\b")
_OVERVIEW = re.compile(
    r"\b(?:morphological evolution|(?:morpholog(?:y|ical)|structural|microstructural)"
    r" (?:changes?|evolution|transformation)|changes? in (?:the )?morphology)\b",
)
_CHANGES = (
    ("aggregation", r"\b(?:nano[ -]?)?aggregat(?:e[ds]?|ion|ing)\b"),
    (
        "size",
        r"\b(?:particles?|grains?|domains?) (?:size|diameter|(?:[a-z]+ ){0,3}"
        r"(?:(?:maintain|retain)(?:ed|s)?|preserv(?:e[ds]?)) (?:their |the )?size)\b",
    ),
    (
        "network",
        r"\b(?:interconnect(?:ed|ion)|interpenetrat(?:ed|ion)|"
        r"(?:fiber|fibrillar|porous|continuous) networks?)\b",
    ),
    ("order", r"\b(?:crystallinity|crystalliz(?:ation|ed)|crystalli[sz]es?)\b"),
)
_SURFACE = re.compile(r"\b(?:morpholog(?:y|ies|ical)|microstructur(?:e|al))\b")
_LOCAL_RADIUS = 240


@dataclass(frozen=True)
class _Match:
    index: int
    overview: bool
    phases: frozenset[str]
    changes: frozenset[str]


def _query_material(question: str) -> tuple[re.Pattern[str], frozenset[str]] | None:
    if len(question) > 240:
        return None
    normalized = identifier_text(question)
    if not _CHANGE_QUESTION.search(normalized):
        return None
    remaining = _CHANGE_QUESTION.sub(" ", normalized)
    phases = frozenset(label for label, chinese, _ in _PHASES if re.search(chinese, remaining))
    if not (phases or _PREPARATION_QUESTION.search(remaining)):
        return None
    for _, chinese, _ in _PHASES:
        remaining = re.sub(chinese, " ", remaining)
    sample = _QUESTION_GLUE.sub(" ", _PREPARATION_QUESTION.sub(" ", remaining)).strip()
    sample = re.sub(r"\s*([()])\s*", r"\1", sample)
    if not _SAMPLE.fullmatch(sample) or not (
        any(char.isdigit() for char in sample) or sample.count("-") >= 2
    ):
        return None
    literal = re.escape(sample).replace(r"\(", r"\s*\(\s*").replace(r"\)", r"\s*\)")
    literal = literal.replace(r"\-", r"\s*-\s*")
    return re.compile(rf"(?<![a-z0-9_.-]){literal}(?![a-z0-9_(-])"), phases


def _observations(index: int, text: str, requested: frozenset[str]) -> _Match | None:
    phases = {label for label, _, english in _PHASES if re.search(english, text)}
    if requested and not requested.intersection(phases):
        return None
    phases.update(f"stage {match[1]}" for match in _NUMBERED_STAGE.finditer(text))
    if not phases and not _PREPARATION.search(text):
        return None
    changes = {label for label, expression in _CHANGES if re.search(expression, text)}
    overview = _OVERVIEW.search(text) is not None
    if not (changes or overview or _SURFACE.search(text)):
        return None
    return _Match(index, overview, frozenset(phases), frozenset(changes))


def ranked_preparation_matches(
    question: str, chunks: Sequence[Chunk], catalog: Mapping[str, Document], *, limit: int,
) -> list[int]:
    """Choose a bounded set of original passages covering distinct stages and observations."""
    focus = _query_material(question)
    if focus is None or limit < 1:
        return []
    material, requested = focus
    matches: list[_Match] = []
    for index, chunk in enumerate(chunks):
        source = catalog.get(chunk.document_id)
        if source is None or source.extension != ".pdf":
            continue
        text = " ".join(identifier_text(chunk.text).split())
        windows = (
            text[max(0, anchor.start() - _LOCAL_RADIUS):anchor.end() + _LOCAL_RADIUS]
            for anchor in material.finditer(text)
        )
        local_matches = (match for window in windows
                         if (match := _observations(index, window, requested)) is not None)
        best_local = max(local_matches, key=lambda match: (
            len(match.phases & requested), bool(match.changes), match.overview,
            len(match.changes), len(match.phases),
        ), default=None)
        if best_local is not None:
            matches.append(best_local)

    chosen: list[int] = []
    seen_overview = False
    seen_phases: set[str] = set()
    seen_changes: set[str] = set()
    while matches and len(chosen) < limit:
        best = max(matches, key=lambda match: (
            len((match.phases & requested) - seen_phases),
            match.overview and not seen_overview,
            len(match.changes - seen_changes),
            len(match.phases - seen_phases),
            len(match.changes),
            -match.index,
        ))
        matches.remove(best)
        chosen.append(best.index)
        seen_overview |= best.overview
        seen_phases.update(best.phases)
        seen_changes.update(best.changes)
    return chosen
