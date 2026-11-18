from rag_workbench.application.concept_graph import build_concept_map
from rag_workbench.application.knowledge import KnowledgeService
from rag_workbench.application.materials import MaterialsService
from rag_workbench.domain.materials import EvidenceReference, MaterialRecord
from tests.test_knowledge_graph import _Embedding, _pdf


def test_local_concepts_use_real_source_blocks_without_embeddings(settings):
    embedder = _Embedding()
    knowledge = KnowledgeService(settings, embedder)
    doc = knowledge.ingest(
        "file.pdf",
        _pdf(
            "Polymer p(g2T-T) forms an interconnected network after solvent exchange.",
            "p(g2T-T) was processed in DMSO by UV cross-linking.",
        ),
    )
    embedder.sealed = True

    graph = build_concept_map(knowledge)
    nodes = {node.label: node for node in graph.nodes}
    assert "file.pdf" not in nodes
    assert nodes["p(g2T-T)"].kind == "material"
    assert nodes["solvent exchange"].kind == "method"
    neighborhood = graph.focus(nodes["p(g2T-T)"].id)
    edge = next(
        e
        for e in neighborhood.edges
        if {e.source, e.target}
        == {
            nodes["p(g2T-T)"].id,
            nodes["solvent exchange"].id,
        }
    )
    assert edge.kind == "co_occurrence"
    (evidence,) = edge.evidence
    assert (evidence.document_id, evidence.page) == (doc.id, 1)
    assert "p(g2T-T)" in evidence.text and "solvent exchange" in evidence.text
    assert not (knowledge.root / "materials").exists()

    knowledge.settings.spoiler_max_page = 1
    limited = build_concept_map(knowledge)
    assert "DMSO" not in {node.label for node in limited.nodes}
    assert all(quote.page == 1 for node in limited.nodes for quote in node.evidence)


def test_configured_concepts_and_markdown_headings_require_visible_occurrences(settings):
    knowledge = KnowledgeService(settings, _Embedding())
    knowledge.expansions["aliases"] = {"优先队列": ["优先级队列"], "隐藏人物": ["隐名"]}
    document = knowledge.ingest(
        "notes.md",
        "# 堆的性质\n\n堆可用于实现优先级队列。\n\n## p op\n\n## 所有测试代码\n".encode(),
    )
    knowledge.assign_sources({document.id: ("课程讲义", 6)})

    graph = build_concept_map(knowledge)
    labels = {node.label for node in graph.nodes}
    assert "堆的性质" in labels and "优先级队列" in labels
    assert "隐藏人物" not in labels and "notes.md" not in labels
    assert "p op" not in labels and "所有测试代码" not in labels
    assert all(evidence.page == 6 for node in graph.nodes for evidence in node.evidence)


def test_reviewed_material_relationship_needs_exact_name_and_supported_pages(settings):
    knowledge = KnowledgeService(settings, _Embedding())
    materials = MaterialsService(settings)

    def upload(name, source_text):
        data = _pdf("Polymer background", source_text)
        indexed = knowledge.ingest(name, data)
        doc = materials.ingest(name, data)
        assert doc.id == indexed.id
        block = next(block for block in materials.repo.blocks(doc.id) if block.page == 2)
        record = materials.add_record(
            MaterialRecord(
                id=doc.id,
                document_id=doc.id,
                material_name="pentacene",
                property_name="threshold voltage",
                value_raw="2",
                evidence=[
                    EvidenceReference(field=field, block_id=block.id, quote=quote)
                    for field, quote in (
                        ("material_name", "pentacene"),
                        ("property_name", "threshold voltage"),
                        ("value_raw", "2"),
                    )
                ],
            )
        )
        materials.review(record.id, record, status="approved")
        return doc.id

    true_document = upload("Valid.pdf", "pentacene threshold voltage 2 was reported.")
    upload("Prefix.pdf", "perfluoropentacene threshold voltage 2 was reported.")

    graph = build_concept_map(knowledge)
    nodes = {node.label: node for node in graph.nodes}
    edges = [e for e in graph.focus(nodes["pentacene"].id).edges if e.kind == "reviewed"]
    (edge,) = edges
    assert {edge.source, edge.target} == {nodes["pentacene"].id, nodes["threshold voltage"].id}
    assert {(e.document_id, e.page) for e in edge.evidence} == {(true_document, 2)}

    knowledge.settings.spoiler_max_page = 1
    limited = build_concept_map(knowledge)
    assert "pentacene" not in {node.label for node in limited.nodes}
