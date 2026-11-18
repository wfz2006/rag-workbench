import io

import numpy as np
import pytest
from reportlab.pdfgen.canvas import Canvas

from rag_workbench.application.knowledge import KnowledgeService
from rag_workbench.application.knowledge_graph import build_document_graph, graph_documents
from rag_workbench.application.materials import MaterialsService
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.materials import EvidenceReference, MaterialRecord


class _Embedding:
    signature = "star-test-v1"
    sealed = False

    def encode_documents(self, texts):
        assert not self.sealed, "The graph must use indexed vectors"
        return np.array([[1.0, 0.0] if "Polymer" in t else [0.0, 1.0] for t in texts])


def _pdf(*pages: str) -> bytes:
    data = io.BytesIO()
    canvas = Canvas(data)
    for page in pages:
        for index, line in enumerate(page.splitlines()):
            canvas.drawString(40, 770 - index * 14, line)
        canvas.showPage()
    canvas.save()
    return data.getvalue()


def _record(materials, payload, name, *, page=1, approve=True, material=None, quote=None):
    document = materials.ingest(name, payload)
    block = next(block for block in materials.repo.blocks(document.id) if block.page == page)
    material = material or block.text.split()[0]
    record = materials.add_record(MaterialRecord(
        id=f"{document.id}-{page}", document_id=document.id, material_name=material,
        property_name="threshold voltage", value_raw="2",
        evidence=[EvidenceReference(field=field, block_id=block.id, quote=quote) for field, quote
                  in [("material_name", quote or material), ("property_name", "threshold voltage"),
                      ("value_raw", "2")]],
    ))
    if approve:
        materials.review(record.id, record, status="approved")


def test_semantic_edges_reuse_index_and_show_the_matching_pdf_pages(settings):
    embedder = _Embedding()
    knowledge = KnowledgeService(settings, embedder)
    center = knowledge.ingest("Alpha.pdf", _pdf("Polymer methods and gelation"))
    neighbor = knowledge.ingest("Beta.pdf", _pdf("A different topic", "Polymer processing"))
    knowledge.ingest("Unrelated.pdf", _pdf("Another measurement"))
    embedder.sealed = True

    assert len(graph_documents(knowledge)) == 3
    graph = build_document_graph(knowledge, center.id)
    assert {node.id for node in graph.nodes} == {center.id, neighbor.id}
    edge, = graph.edges
    assert (edge.source, edge.target, edge.kind, edge.score) == (
        center.id, neighbor.id, "semantic", 1.0,
    )
    assert [(e.document_id, e.page, e.text) for e in edge.evidence] == [
        (center.id, 1, "Polymer methods and gelation"),
        (neighbor.id, 2, "Polymer processing"),
    ]
    assert build_document_graph(knowledge, neighbor.id).edges[0].id == edge.id
    assert not (knowledge.root / "materials").exists()

    knowledge.settings.spoiler_max_page = 1
    limited = build_document_graph(knowledge, center.id)
    assert limited.nodes == (center,)
    assert not limited.edges


def test_only_approved_supported_materials_link_documents_and_keep_original_pages(settings):
    knowledge = KnowledgeService(settings, _Embedding())
    materials = MaterialsService(settings)
    urls = [
        ("Alpha.pdf", _pdf("Polymer gelation", "PTB7 threshold voltage 2 in Alpha")),
        ("Beta.pdf", _pdf("PTB7 threshold voltage 2 in Beta")),
        ("Candidate.pdf", _pdf("PTB7 threshold voltage 2 in a candidate")),
        ("Different.pdf", _pdf("PTB7F threshold voltage 2 in another record")),
    ]
    alpha, beta, _, _ = knowledge.ingest_many(urls)
    for index, (name, payload) in enumerate(urls):
        _record(materials, payload, name, page=2 if index == 0 else 1, approve=index != 2)

    graph = build_document_graph(knowledge, alpha.id)
    edge, = [e for e in graph.edges if e.kind == "shared_material"]
    assert (edge.source, edge.target, edge.material) == (alpha.id, beta.id, "PTB7")
    assert edge.score is None
    assert [(e.document_id, e.page) for e in edge.evidence] == [(alpha.id, 2), (beta.id, 1)]
    assert all("PTB7" in e.text for e in edge.evidence)

    knowledge.settings.spoiler_max_page = 1
    assert not build_document_graph(knowledge, alpha.id).edges


def test_graph_uses_new_generation_after_delete_and_rejects_corrupted_vectors(settings):
    knowledge = KnowledgeService(settings, _Embedding())
    center = knowledge.ingest("Alpha.pdf", _pdf("Polymer alpha"))
    neighbor = knowledge.ingest("Beta.pdf", _pdf("Polymer beta"))
    assert len(build_document_graph(knowledge, center.id).edges) == 1

    knowledge.delete(neighbor.id)
    assert graph_documents(knowledge) == (center,)
    assert not build_document_graph(knowledge, neighbor.id).nodes
    assert not build_document_graph(knowledge, center.id).edges

    generation = knowledge.repo.get_state("active")["generation"]
    (knowledge.root / "indexes" / generation / "index.faiss").write_bytes(b"corrupt")
    with pytest.raises(AppError) as failure:
        build_document_graph(knowledge, center.id)
    assert failure.value.code == "index_corrupt"


def test_publisher_navigation_and_reference_lists_do_not_create_semantic_edges(settings):
    knowledge = KnowledgeService(settings, _Embedding())
    center = knowledge.ingest("Alpha.pdf", _pdf("Polymer gelation research"))
    cover = _pdf("View\nOnline\nExport\nCitation\nPolymer", "Another subject")
    knowledge.ingest("Cover.pdf", cover)
    knowledge.ingest("Other.pdf", _pdf("Another subject", "References\nPolymer reference title"))

    assert not build_document_graph(knowledge, center.id).edges


def test_material_edge_requires_a_complete_name_in_the_original_quote_context(settings):
    knowledge = KnowledgeService(settings, _Embedding())
    materials = MaterialsService(settings)
    filler = "Measurement conditions were recorded separately for each sample.\n" * 6
    payloads = [
        ("Alpha.pdf", _pdf("pentacene OFET threshold voltage 2 in Alpha"), "pentacene OFET"),
        ("Prefix.pdf", _pdf("perfluoropentacene OFET threshold voltage 2"), "pentacene OFET"),
        ("Suffix.pdf", _pdf("pentacene-2 OFET threshold voltage 2"), "pentacene"),
        ("Repeated.pdf", _pdf(
            "perfluoropentacene OFET threshold voltage 2 first\n" + filler
            + "Valid source: pentacene\nOFET threshold voltage 2 here",
        ), "pentacene OFET"),
    ]
    alpha, _, _, valid = knowledge.ingest_many([(name, data) for name, data, _ in payloads])
    # The legacy reviewer accepted each cropped quote. The graph still checks the source context.
    for name, data, quote in payloads:
        _record(materials, data, name, material="pentacene", quote=quote)

    edges = [edge for edge in build_document_graph(knowledge, alpha.id).edges
             if edge.kind == "shared_material"]
    assert [edge.target for edge in edges] == [valid.id]
    excerpt = edges[0].evidence[1].text
    assert "Valid source: pentacene\nOFET" in excerpt
    assert "perfluoropentacene OFET" not in excerpt
