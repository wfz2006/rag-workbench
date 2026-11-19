"""Build evidence-backed neighborhoods from the current, locally indexed knowledge base."""

import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import numpy as np

from rag_workbench.application.knowledge import KnowledgeService
from rag_workbench.application.paper_sources import REFERENCES
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.models import Chunk, Document
from rag_workbench.infrastructure.material_repository import (
    MaterialNameEvidence,
    MaterialsRepository,
)
from rag_workbench.infrastructure.parsing import comparable_pdf_text

if TYPE_CHECKING:
    from faiss import IndexFlatIP

_QUERY_CHUNKS = 16
_CHUNK_BATCH = 1024
_PUBLISHER_NOTICES = (
    "view journal", "view article online", "view online", "export citation",
    "downloaded on", "downloaded by", "this journal is", "permissions", "all rights reserved",
)


@dataclass(frozen=True)
class GraphEvidence:
    document_id: str
    document_name: str
    page: int | None
    text: str
    physical_page: int | None = None
    mapped_page: int | None = None
    locator: str | None = None
    record_id: str | None = None
    record_revision: int | None = None
    field: str | None = None


@dataclass(frozen=True)
class GraphEdge:
    id: str
    source: str
    target: str
    kind: Literal["semantic", "shared_material"]
    evidence: tuple[GraphEvidence, GraphEvidence]
    score: float | None = None
    material: str | None = None


@dataclass(frozen=True)
class DocumentGraph:
    nodes: tuple[Document, ...]
    edges: tuple[GraphEdge, ...]
    material_warning: str | None = None


def graph_documents(knowledge: KnowledgeService) -> tuple[Document, ...]:
    documents, _, _ = knowledge.visible_index_snapshot()
    return documents


def _edge_id(kind: str, first: str, second: str) -> str:
    return ":".join((kind, *sorted((first, second))))


def _evidence(
    document: Document, page: int | None, excerpt: str, *, locator: str | None = None,
) -> GraphEvidence:
    return GraphEvidence(
        document.id, document.name, document.source_page or page, excerpt,
        page, document.source_page, locator,
    )


def _body_positions(
    documents: dict[str, Document], positioned: tuple[tuple[int, Chunk], ...],
) -> Iterator[tuple[int, Chunk]]:
    """Skip explicit PDF bibliographies and recognizable publisher navigation blocks."""
    last_page = {}
    for _, chunk in positioned:
        last_page[chunk.document_id] = max(last_page.get(chunk.document_id, 1), chunk.page or 1)
    references_started = set()
    for position, chunk in positioned:
        if documents[chunk.document_id].extension == ".pdf":
            if (
                (chunk.page or 1) >= max(1, last_page[chunk.document_id] // 3)
                and REFERENCES.search(chunk.text)
            ):
                references_started.add(chunk.document_id)
            if chunk.document_id in references_started:
                continue
            text = " ".join(chunk.text.casefold().split())
            if sum(marker in text for marker in _PUBLISHER_NOTICES) >= 2:
                continue
        yield position, chunk


def _semantic_edges(
    center: Document, documents: dict[str, Document], positioned: tuple[tuple[int, Chunk], ...],
    index: "IndexFlatIP", threshold: float,
) -> dict[str, GraphEdge]:
    # A bounded, evenly spread sample represents different parts of a long paper. Each
    # chosen vector is compared to visible chunks only, without reading originals or embedding.
    body = list(_body_positions(documents, positioned))
    own = [pair for pair in body if pair[1].document_id == center.id]
    others = [pair for pair in body if pair[1].document_id != center.id]
    if not own or not others:
        return {}
    sample_rows = np.linspace(0, len(own) - 1, min(len(own), _QUERY_CHUNKS), dtype=int)
    sample = [own[i] for i in sample_rows]
    queries = index.reconstruct_batch(np.array([position for position, _ in sample], np.int64))
    related = {}
    for offset in range(0, len(others), _CHUNK_BATCH):
        batch = others[offset:offset + _CHUNK_BATCH]
        targets = index.reconstruct_batch(np.array([position for position, _ in batch], np.int64))
        scores = queries @ targets.T
        highest = scores.argmax(axis=0)
        peaks = scores[highest, np.arange(len(batch))]
        for i in np.flatnonzero((peaks > 0) & (peaks >= threshold)):
            score = min(float(peaks[i]), 1.0)
            _, matched = batch[i]
            target = documents[matched.document_id]
            best = related.get(target.id)
            if best is not None and best.score is not None and score <= best.score:
                continue
            _, origin = sample[highest[i]]
            related[target.id] = GraphEdge(
                _edge_id("semantic", center.id, target.id), center.id, target.id, "semantic",
                (_evidence(center, origin.page, origin.text),
                 _evidence(target, matched.page, matched.text)),
                score=score,
            )
    return related


def _has_material_quote(source: str, quote: str, material: re.Pattern[str]) -> bool:
    # Verify that a complete token in the source lies inside an occurrence of the quote.
    # Matching the token on the quote alone would create new word boundaries at its ends.
    length = len(quote)
    return any(
        source.find(quote, max(0, match.end() - length), match.start() + length) >= 0
        for match in material.finditer(source)
    )


def _material_excerpt(record: MaterialNameEvidence) -> str | None:
    name = comparable_pdf_text(record.material_name)
    quote = comparable_pdf_text(record.quote)
    if not name or not quote or name not in quote:
        return None
    # Chemical spelling is case sensitive. Preserve adjacent ASCII identifiers and dashes
    # when checking boundaries, including when the quote cuts through a longer name.
    bound = r"[a-zA-Z0-9_\u2010-\u2015\u2212-]"
    left = f"(?<!{bound})" if name[0].isascii() else ""
    right = f"(?!{bound})" if name[-1].isascii() else ""
    material = re.compile(left + re.escape(name) + right)
    raw = record.block.text
    if not _has_material_quote(comparable_pdf_text(raw), quote, material):
        return None

    # PDF line wrapping can differ from a saved quote even when its words are verbatim.
    raw_quote = r"\s+".join(map(re.escape, record.quote.split()))
    raw_name = r"\s+".join(map(re.escape, name.split()))
    raw_material = re.compile(left + raw_name + right)
    for match in re.finditer(raw_quote, raw):
        # Search against the full original block. Restricting the regex end position would
        # falsely turn a truncated suffix into an exact token.
        found = raw_material.search(raw, match.start())
        if found is not None and found.end() <= match.end():
            excerpt = raw[max(0, match.start() - 120):match.end() + 120].strip()
            if _has_material_quote(comparable_pdf_text(excerpt), quote, material):
                return excerpt
    # The verified quote may exist only after folding PDF glyphs or spaces. Keep the original
    # block verbatim when short enough rather than inventing the position of that occurrence.
    return raw if len(raw) <= 10_000 else None


def _shared_material_edges(
    knowledge: KnowledgeService, center: Document, documents: dict[str, Document],
    positioned: tuple[tuple[int, Chunk], ...],
) -> dict[str, GraphEdge]:
    if len(documents) < 2:
        return {}
    records = MaterialsRepository.approved_material_name_evidence(
        knowledge.root / "materials", set(documents),
    )
    limit = knowledge.settings.spoiler_max_page
    allowed_pages = defaultdict(set)
    if limit is not None:
        for _, chunk in positioned:
            allowed_pages[chunk.document_id].add(chunk.page)

    by_document = defaultdict(dict)
    for record in records:
        document, page = documents[record.document_id], record.block.page
        if limit is not None and (page is None or page not in allowed_pages[document.id]):
            continue
        name = comparable_pdf_text(record.material_name)
        if name in by_document[document.id]:
            continue
        excerpt = _material_excerpt(record)
        if excerpt:
            by_document[document.id][name] = _evidence(document, page, excerpt)

    own = by_document.pop(center.id, {})
    related = {}
    for identity, target in by_document.items():
        name = min(own.keys() & target.keys(), default=None)
        if name is not None:
            related[identity] = GraphEdge(
                _edge_id("shared_material", center.id, identity), center.id, identity,
                "shared_material", (own[name], target[name]), material=name,
            )
    return related


def build_document_graph(
    knowledge: KnowledgeService, center_id: str, *, max_neighbors: int = 12,
) -> DocumentGraph:
    catalog, positioned, index = knowledge.visible_index_snapshot(load_vectors=True)
    documents = {document.id: document for document in catalog}
    center = documents.get(center_id)
    if center is None or index is None:
        return DocumentGraph((), ())
    if max_neighbors <= 0 or len(documents) < 2:
        return DocumentGraph((center,), ())
    semantic = _semantic_edges(
        center, documents, positioned, index, knowledge.settings.similarity_threshold,
    )
    warning = None
    try:
        material = _shared_material_edges(knowledge, center, documents, positioned)
    except AppError as exc:
        material, warning = {}, str(exc)
    scores = {identity: edge.score or 0.0 for identity, edge in semantic.items()}
    neighbors = sorted(
        semantic.keys() | material.keys(),
        key=lambda identity: (
            identity not in material, -scores.get(identity, 0.0),
            documents[identity].name, identity,
        ),
    )[:max_neighbors]
    edges = tuple(
        kind[identity] for identity in neighbors
        for kind in (semantic, material) if identity in kind
    )
    return DocumentGraph((center, *(documents[identity] for identity in neighbors)), edges, warning)
