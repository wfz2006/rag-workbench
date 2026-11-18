"""Connect source-backed concepts within the active local knowledge base."""

import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import combinations
from typing import Literal

from rag_workbench.application.knowledge import KnowledgeService
from rag_workbench.application.knowledge_graph import (
    GraphEvidence,
    _body_positions,
    _evidence,
    _material_excerpt,
)
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.models import Chunk, Document
from rag_workbench.infrastructure.material_repository import (
    MaterialNameEvidence,
    MaterialsRepository,
)
from rag_workbench.infrastructure.parsing import comparable_pdf_text

ConceptKind = Literal["material", "method", "property", "topic"]
RelationKind = Literal["reviewed", "co_occurrence"]
_MAX_CHUNKS = 5000
_MAX_TERMS = 180
_MAX_AUTOMATIC_TERMS = 360
_MAX_PER_CHUNK = 10
_MAX_NODE_EVIDENCE = 3
_MAX_EDGE_EVIDENCE = 2
_OBSERVATION_FIELDS: dict[str, ConceptKind] = {
    "material_name": "material",
    "property_name": "property",
    "process": "method",
    "measurement_method": "method",
    "deposition_method": "method",
}
_ANCHORS: dict[ConceptKind, tuple[str, ...]] = {
    "material": ("pentacene", "perfluoropentacene", "DMSO", "PAAc", "P3HT", "TIPS-pentacene"),
    "method": (
        "gelation",
        "solvent exchange",
        "UV cross-linking",
        "UV crosslinking",
        "spin coating",
        "spin-coated",
        "thermal evaporation",
        "vapor deposition",
        "annealing",
        "X-ray diffraction",
        "scanning electron microscopy",
        "凝胶化",
        "溶剂交换",
        "退火",
        "旋涂",
        "热蒸发",
        "X射线衍射",
    ),
    "property": (
        "field-effect mobility",
        "hole mobility",
        "electron mobility",
        "threshold voltage",
        "on/off ratio",
        "charge carrier mobility",
        "电荷迁移率",
        "空穴迁移率",
        "电子迁移率",
        "阈值电压",
        "开关比",
    ),
    "topic": (
        "interconnected network",
        "nano-aggregates",
        "thin-film transistor",
        "有机薄膜晶体管",
        "二叉树",
        "完全二叉树",
        "优先队列",
        "优先级队列",
        "宽度优先遍历",
        "深度优先遍历",
        "层序遍历",
    ),
}
_IDENTIFIER = re.compile(
    r"(?<![\w-])(?:[pP]\([a-zA-Z0-9]+(?:-[a-zA-Z0-9]+)+\)"
    r"|[A-Z]{2,7}\d[A-Z0-9]{0,9}(?:-[A-Z0-9]+)?)(?![\w-])"
)
_HEADING = re.compile(r"^\s*(?:#{1,5}\s+|\d+(?:\.\d+){1,3}\s+)(.{2,65}?)\s*#*\s*$", re.M)
_WORDS = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)*")
_GENERIC_HEADINGS = frozenset(
    (
        "简介",
        "概述",
        "摘要",
        "定义",
        "创建",
        "操作",
        "代码",
        "示例",
        "总结",
        "小结",
        "结论",
        "基本概念",
        "实现方法",
        "应用",
        "特点",
        "说明",
        "目录",
        "参考文献",
        "introduction",
        "abstract",
        "overview",
        "results",
        "conclusion",
        "references",
    )
)
_STOP = frozenset(
    "a about above after again all also an and any are as at be been before being between both but"  # noqa: SIM905
    " by can could does done during each even for from had has have he her here how however i if in"
    " into is it its may might more most new no not of on one only or other our out over per same"
    " she should so some such than that the their them then there these they this those through to"
    " under up use used using very via was we were what when where which while who will with within"
    " would you your fig figure table see obtained shown show doi online view downloaded copyright"
    " https http www pdf supplemental supplementary supporting publisher respectively"
    " results journal information article study papers university based research experimental"
    " material materials methods first second manuscript figures corresponding".split()
)
_NOUN_ENDS = frozenset(
    "tree trees graph graphs search sorting sort heap heaps queue queues network networks"  # noqa: SIM905
    " algorithm algorithms model models data index indices variables equation equations"
    " polymer polymers gel gels solution solutions systems mobility voltage conductivity"
    " diffusion energy density surface method technique processes evaporation deposition imaging"
    " film films microscopy diffraction analysis structure structures morphology".split()
)


@dataclass(frozen=True)
class ConceptNode:
    id: str
    label: str
    kind: ConceptKind
    evidence: tuple[GraphEvidence, ...]


@dataclass(frozen=True)
class ConceptEdge:
    id: str
    source: str
    target: str
    kind: RelationKind
    label: str
    evidence: tuple[GraphEvidence, ...]
    support: int = 1


@dataclass(frozen=True)
class ConceptView:
    nodes: tuple[ConceptNode, ...]
    edges: tuple[ConceptEdge, ...]


@dataclass(frozen=True)
class ConceptMap:
    nodes: tuple[ConceptNode, ...]
    edges: tuple[ConceptEdge, ...] = ()
    material_warning: str | None = None

    def focus(self, center_id: str, *, max_neighbors: int = 12) -> ConceptView:
        catalog = {node.id: node for node in self.nodes}
        if center_id not in catalog:
            return ConceptView((), ())

        def neighbor(edge: ConceptEdge) -> str:
            return edge.target if edge.source == center_id else edge.source

        adjacent = sorted(
            (edge for edge in self.edges if center_id in (edge.source, edge.target)),
            key=lambda edge: (
                edge.kind != "reviewed",
                -edge.support,
                catalog[neighbor(edge)].kind == "topic",
                catalog[neighbor(edge)].label,
                edge.id,
            ),
        )
        selected = tuple(dict.fromkeys(map(neighbor, adjacent)))[: max(0, max_neighbors)]
        visible = set(selected)
        return ConceptView(
            (catalog[center_id], *(catalog[key] for key in selected)),
            tuple(edge for edge in adjacent if neighbor(edge) in visible),
        )


def _term_key(label: str) -> str:
    term = comparable_pdf_text(label).strip()
    # Preserve case in formulas and mixed-case identifiers. Prose labels may vary by sentence.
    return term if _IDENTIFIER.fullmatch(term) or term in {"PAAc", "DMSO"} else term.casefold()


def _label(text: str) -> str | None:
    clean = " ".join(text.split()).strip("。.:：# ")
    if (
        2 <= len(clean) <= 65
        and any(char.isalpha() for char in clean)
        and not re.search(r"\.(?:pdf|txt|md|docx|html)\b|https?://", clean, re.I)
    ):
        return clean
    return None


def _automatic_phrases(text: str) -> set[str]:
    phrases = set()
    run = []
    last_end = 0
    for word in _WORDS.finditer(text):
        word_key = word[0].casefold()
        if word.start() and not text[last_end : word.start()].isspace():
            run.clear()
        last_end = word.end()
        if word_key in _STOP or len(word_key) < 3:
            run.clear()
            continue
        run.append(word_key)
        if len(run) >= 2 and word_key in _NOUN_ENDS:
            phrases.add(" ".join(run[-2:]))
            if len(run) >= 3:
                phrases.add(" ".join(run[-3:]))
    return phrases


def _configured_vocabulary(knowledge: KnowledgeService) -> dict[str, ConceptKind]:
    words = {term: kind for kind, entries in _ANCHORS.items() for term in entries}
    configured = knowledge.expansions.get("aliases", {})
    if isinstance(configured, dict):
        for key, aliases in configured.items():
            for term in (key, *aliases):
                if label := _label(term):
                    words.setdefault(label, "topic")
    return words


def _local_vocabulary(text: str, *, headings: bool) -> dict[str, ConceptKind]:
    words: dict[str, ConceptKind] = {}
    if headings:
        for heading in _HEADING.findall(text):
            label = _label(heading)
            if (
                label
                and label.casefold() not in _GENERIC_HEADINGS
                and not label.endswith("测试代码")
                and not any(
                    len(word) < 3 and not word.isupper() and word.lower() not in _STOP
                    for word in _WORDS.findall(label)
                )
            ):
                words[label] = "topic"
    for match in _IDENTIFIER.finditer(text):
        label = match[0]
        words[label] = "material" if label.lower().startswith("p(") else "topic"
    return words


def _matches(word: str) -> re.Pattern[str]:
    # Keep complete chemical and technical identifiers. Chinese terms can touch prose.
    ascii_bound = r"[A-Za-z0-9_\u2010-\u2015\u2212-]"
    left = f"(?<!{ascii_bound})" if word[0].isascii() else ""
    right = f"(?!{ascii_bound})" if word[-1].isascii() else ""
    token = re.escape(" ".join(word.split()))
    sensitive = word in {"PAAc", "DMSO"} or _IDENTIFIER.fullmatch(word) is not None
    return re.compile(left + token + right, 0 if sensitive else re.I)


def _remember(
    store: list[GraphEvidence],
    item: GraphEvidence,
    limit: int,
    *,
    distinct_pages: bool = False,
) -> None:
    if len(store) >= limit or item in store:
        return
    if (
        distinct_pages
        and item.page is not None
        and any((other.document_id, other.page) == (item.document_id, item.page) for other in store)
    ):
        return
    store.append(item)


def _node_id(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def _edge(
    a: str,
    b: str,
    kind: RelationKind,
    evidence: list[GraphEvidence],
    support: int = 1,
) -> ConceptEdge:
    source, target = sorted((_node_id(a), _node_id(b)))
    label = "已审核关联" if kind == "reviewed" else "同段出现"
    return ConceptEdge(
        f"{kind}:{source}:{target}",
        source,
        target,
        kind,
        label,
        tuple(evidence),
        support,
    )


def _approved_evidence(
    knowledge: KnowledgeService,
    docs: dict[str, Document],
    positioned: tuple[tuple[int, Chunk], ...],
) -> dict[str, dict[str, tuple[str, GraphEvidence]]]:
    permitted_pages = defaultdict(set)
    for _, chunk in positioned:
        permitted_pages[chunk.document_id].add(chunk.page)
    records = MaterialsRepository.approved_concept_evidence(knowledge.root / "materials", set(docs))
    by_record: dict[str, dict[str, tuple[str, GraphEvidence]]] = defaultdict(dict)
    for row in records:
        if row.block.page not in permitted_pages[row.document_id]:
            continue
        document = docs[row.document_id]
        if (
            knowledge.settings.spoiler_max_page is not None
            and document.source_page is None
            and row.block.page is None
        ):
            continue
        label = _label(row.value)
        if label is None or row.field in by_record[row.record_id]:
            continue
        excerpt = _material_excerpt(
            MaterialNameEvidence(row.document_id, row.value, row.quote, row.block)
        )
        if excerpt:
            evidence = _evidence(document, row.block.page, excerpt)
            by_record[row.record_id][row.field] = (label, evidence)
    return by_record


def build_concept_map(knowledge: KnowledgeService) -> ConceptMap:
    catalog, positioned, _ = knowledge.visible_index_snapshot()
    if not positioned:
        return ConceptMap(())
    docs = {item.id: item for item in catalog}
    body = list(_body_positions(docs, positioned))
    # Large libraries are sampled evenly so a long file at the start does not consume the budget.
    size = len(body)
    count = min(size, _MAX_CHUNKS)
    chunks = tuple(body[index * size // count][1] for index in range(count))
    vocabulary = _configured_vocabulary(knowledge)
    labels: dict[str, str] = {}
    kinds: dict[str, ConceptKind] = {}
    source_evidence: dict[str, list[GraphEvidence]] = defaultdict(list)
    frequency: Counter[str] = Counter()
    warning = None
    try:
        reviewed = _approved_evidence(knowledge, docs, positioned)
    except AppError as exc:
        reviewed, warning = {}, str(exc)
    approved_terms = set()
    approved_edges: dict[tuple[str, str], list[GraphEvidence]] = defaultdict(list)
    for record in reviewed.values():
        # A relation requires separately supported material and observation fields in one record.
        material = record.get("material_name")
        material_key = _term_key(material[0]) if material else ""
        for field, (label, evidence) in record.items():
            key = _term_key(label)
            labels[key], kinds[key] = label, _OBSERVATION_FIELDS[field]
            approved_terms.add(key)
            _remember(source_evidence[key], evidence, _MAX_NODE_EVIDENCE)
            if material and material_key != key:
                quotes = approved_edges[tuple(sorted((material_key, key)))]
                _remember(quotes, material[1], _MAX_EDGE_EVIDENCE)
                _remember(quotes, evidence, _MAX_EDGE_EVIDENCE)

    configured = [
        (term, kind, " ".join(term.split()).lower(), _matches(term))
        for term, kind in vocabulary.items()
    ]
    candidates = []
    recurring: Counter[str] = Counter()
    for chunk in chunks:
        document = docs[chunk.document_id]
        normalized = " ".join(chunk.text.split())
        lower = normalized.lower()
        precise = _local_vocabulary(chunk.text, headings=document.extension != ".pdf")
        for term, kind, needle, pattern in configured:
            if needle in lower and pattern.search(normalized):
                precise[term] = kind
        phrases = _automatic_phrases(chunk.text)
        recurring.update(phrases)
        candidates.append((chunk, precise, phrases))

    ranked_phrases = sorted(recurring, key=lambda term: (-recurring[term], term))
    recurring_terms = {
        term for term in ranked_phrases[:_MAX_AUTOMATIC_TERMS] if recurring[term] >= 2
    }
    explicit_terms = set(approved_terms)
    occurrences: list[tuple[set[str], GraphEvidence]] = []
    for chunk, precise, phrases in candidates:
        terms = dict.fromkeys(phrases & recurring_terms, "topic")
        terms.update(precise)
        explicit_terms.update(map(_term_key, precise))
        if not terms:
            continue
        found = set()
        excerpt = _evidence(docs[chunk.document_id], chunk.page, chunk.text)
        for label, kind in terms.items():
            key = _term_key(label)
            labels.setdefault(key, label)
            kinds.setdefault(key, kind)
            found.add(key)
            _remember(source_evidence[key], excerpt, _MAX_NODE_EVIDENCE, distinct_pages=True)
        frequency.update(found)
        occurrences.append((found, excerpt))

    ranking = sorted(
        source_evidence,
        key=lambda key: (
            key not in approved_terms,
            kinds[key] == "topic",
            key not in explicit_terms,
            -frequency[key],
            labels[key],
            key,
        ),
    )[:_MAX_TERMS]
    order = {key: rank for rank, key in enumerate(ranking)}
    edges = [
        _edge(a, b, "reviewed", evidence)
        for (a, b), evidence in approved_edges.items()
        if a in order and b in order
    ]
    co_evidence: dict[tuple[str, str], list[GraphEvidence]] = defaultdict(list)
    co_support: Counter[tuple[str, str]] = Counter()
    for found, excerpt in occurrences:
        shared = sorted(found & order.keys(), key=order.__getitem__)[:_MAX_PER_CHUNK]
        for a, b in combinations(sorted(shared), 2):
            pair = (a, b)
            _remember(co_evidence[pair], excerpt, _MAX_EDGE_EVIDENCE, distinct_pages=True)
            co_support[pair] += 1
    edges.extend(
        _edge(a, b, "co_occurrence", evidence, co_support[a, b])
        for (a, b), evidence in co_evidence.items()
    )
    nodes = tuple(
        ConceptNode(_node_id(key), labels[key], kinds[key], tuple(source_evidence[key]))
        for key in ranking
    )
    return ConceptMap(nodes, tuple(edges), warning)
