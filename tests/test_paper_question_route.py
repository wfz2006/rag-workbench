import io
import json
from types import SimpleNamespace

import pytest
from reportlab.pdfgen.canvas import Canvas

from rag_workbench.application.paper_sources import paper_passages
from rag_workbench.application.pipeline import RagPipeline
from rag_workbench.application.prompts import EVIDENCE_SYSTEM
from rag_workbench.domain.models import ChatMessage, Document, Section

from .fakes import FakeProvider
from .test_pdf_table_lookup import ruled_table


def paper(*pages):
    data = io.BytesIO()
    canvas = Canvas(data)
    for lines in pages:
        for i, line in enumerate(lines):
            canvas.drawString(40, 780 - 16 * i, line)
        canvas.showPage()
    canvas.save()
    return data.getvalue()


class ScopedProvider:
    def complete(self, system, user, *, json_mode=False, history=None):
        payload = json.loads(user)
        assert payload["paper_scope"] == "薄膜文献1.pdf"
        evidence = payload["evidence"]
        assert {item["document"] for item in evidence} == {"薄膜文献1.pdf"}
        assert {item["page"] for item in evidence} == {1, 2}
        assert any("Lena Chen" in item["text"] for item in evidence)
        assert any("vapor deposited" in item["text"] for item in evidence)
        ids = [item["chunk_id"] for item in evidence]
        if system == EVIDENCE_SYSTEM:
            return json.dumps({"answerable": True, "reason": "论文有部分依据",
                               "supporting_chunk_ids": ids})
        return json.dumps({"claims": [{"text": "Lena Chen", "chunk_ids": ids[:1]}]})


def test_named_paper_profile_keeps_front_matter_and_methods_even_with_small_top_k(knowledge):
    knowledge.settings.top_k = 1
    knowledge.ingest_many([
        ("薄膜文献1.pdf", paper(["A Study of Films", "Lena Chen", "Abstract", "Two films studied."],
                                ["Methods", "The films were vapor deposited."])),
        ("chemical review 2012.pdf", paper(["Different article", "Different author"])),
    ])
    result = RagPipeline(knowledge, ScopedProvider()).answer(
        "请你阅读薄膜文献1，给我论文标题、作者、年份、DOI、器件和性能信息"
    )
    assert result.mode == "knowledge_base"
    assert "Lena Chen" in result.answer
    assert result.sources[0].chunk.document_name == "薄膜文献1.pdf"


def test_duplicate_paper_names_report_ambiguity_without_provider(knowledge):
    knowledge.ingest_many([
        ("薄膜文献1.pdf", paper(["First article"])),
        ("薄膜文献1.pdf", paper(["Other article"])),
    ])
    result = RagPipeline(knowledge, object()).answer("请总结薄膜文献1")
    assert result.mode == "insufficient_evidence"
    assert "同名" in result.answer


def test_table_question_uses_only_verified_pdf_and_cites_exact_page(knowledge, monkeypatch):
    first = paper(["PY3-OTS at three substrate temperatures"])
    review = paper(["Related device theory in a different paper"])
    knowledge.ingest_many([("paper.pdf", first), ("review.pdf", review)])
    read = []

    def lookup(data, question, *, max_pages):
        read.append(data)
        assert "PY3-OTS" in question
        return SimpleNamespace(page=1, text="Table 1\nPY3-OTS 25 C 2.3e-7",
                               answer="25 °C：2.3 × 10⁻⁷", table_label="Table 1")

    monkeypatch.setattr("rag_workbench.application.knowledge.lookup_pdf_table", lookup)
    previous = [ChatMessage(role="user", content="PY3-OTS 在不同温度下的迁移率是多少？")]
    result = RagPipeline(knowledge, object()).answer(
        "根据 Table 1 回答不同温度下的迁移率", previous,
    )
    assert result.mode == "knowledge_base"
    assert "2.3 × 10⁻⁷" in result.answer
    assert read == [first]
    source, = result.sources
    assert (source.chunk.document_name, source.chunk.page) == ("paper.pdf", 1)
    assert source.retrieval_method == "pdf_table"
    assert "direct_answer" not in result.debug.model_dump()


def test_compares_two_explicit_sources_including_plain_text(knowledge):
    knowledge.ingest_many([
        ("ma100997g.pdf", paper(["Thin film paper", "Lena Chen", "The film was spin coated."])),
        ("试验笔记.txt", "笔记中采用热蒸镀。".encode()),
        ("unrelated.txt", b"Unrelated experiment"),
    ])

    profile = knowledge.paper_evidence("总结 ma100997g 的作者")
    assert {r.chunk.document_name for r in profile.candidates} == {"ma100997g.pdf"}
    comparison = knowledge.paper_evidence("ma100997g 和 试验笔记.txt 对比")
    assert {r.chunk.document_name for r in comparison.candidates} == {
        "ma100997g.pdf", "试验笔记.txt",
    }
    assert all(r.eligible for r in comparison.candidates)
    assert any("笔记中采用热蒸镀" in r.chunk.text for r in comparison.candidates)
    provider = FakeProvider()
    RagPipeline(knowledge, provider).inspect("ma100997g 和 试验笔记.txt 对比")
    payload = json.loads(provider.calls[0][1])
    assert set(payload["paper_scope"]) == {"ma100997g.pdf", "试验笔记.txt"}


def test_long_paper_passages_keep_major_sections_and_body_in_page_order():
    doc = Document(id="x", name="long.pdf", extension=".pdf", size=100)
    pages = [Section(page=i, text=f"Paper body on page {i}. " * 200) for i in range(1, 11)]
    pages[2].text = "■ RESULTS AND DISCUSSION\nDistinctive outcome.\n" + pages[2].text
    pages[7].text = (
        "▪ EXPERIMENTAL SECTION:\nDistinctive method.\n"
        "Conclusions. Distinctive conclusion.\n" + pages[7].text
    )
    pages[9].text = "REFERENCES AND NOTES\nExcluded citation list."

    chunks = [r.chunk for r in paper_passages(doc, pages)]

    assert all(c.text == pages[c.page - 1].text[c.start:c.end] for c in chunks)
    assert sum(len(c.text) for c in chunks) <= 18_000
    assert [(c.page, c.start) for c in chunks] == sorted({(c.page, c.start) for c in chunks})
    joined = "\n".join(c.text for c in chunks)
    for content in ("Distinctive outcome", "Distinctive method", "Distinctive conclusion"):
        assert content in joined
    assert "Excluded citation list" not in joined


def test_table_miss_in_explicit_paper_uses_scoped_evidence(knowledge):
    knowledge.ingest_many([
        ("paper.pdf", paper(["No ruled table.", "D42-OTS mobility under 60 C was 0.02."])),
        ("notes.txt", b"An unrelated note mentions D42-OTS at 60 C."),
    ])
    provider = FakeProvider(answerable=False)

    result = RagPipeline(knowledge, provider).answer("paper.pdf 的 D42-OTS 在60℃的迁移率是多少？")

    assert {item.chunk.document_name for item in result.debug.candidates} == {"paper.pdf"}
    assert result.mode == "insufficient_evidence"
    assert len(provider.calls) == 1


def test_table_lookup_uses_both_sample_names_to_find_unnamed_paper(knowledge, monkeypatch):
    targeted = paper(["Characteristics of para-IDT(QNCN)2", "and meta-IDT(QNCN)2"])
    knowledge.ingest_many([
        ("paper.pdf", targeted),
        ("other.pdf", paper(["No relevant compound data."])),
    ])
    scanned = []

    def lookup(data, question, *, max_pages):
        scanned.append(data)
        return None

    monkeypatch.setattr("rag_workbench.application.knowledge.lookup_pdf_table", lookup)
    assert knowledge.paper_evidence(
        "para-IDT(QNCN)2 和 meta-IDT(QNCN)2 的线性与饱和迁移率对比"
    ) is None
    assert scanned == [targeted]


def test_table_lookup_finds_unnamed_paper_with_space_separated_compound_id(
    knowledge, monkeypatch,
):
    targeted = paper(["Electronic properties of TCNBT", "Th IDT transistors"])
    distractor = paper(["Electronic properties of TCNBT OMeTh IDT transistors"])
    knowledge.ingest_many([("paper.pdf", targeted), ("other.pdf", distractor)])
    scanned = []

    def lookup(data, question, *, max_pages):
        scanned.append(data)
        return None

    monkeypatch.setattr("rag_workbench.application.knowledge.lookup_pdf_table", lookup)
    knowledge.paper_evidence("TCNBT-Th-IDT 在所有条件下的饱和电子平均迁移率是多少？")

    assert scanned == [targeted]


@pytest.mark.parametrize("reference", [
    "Opoku 2017",
    "《Macro Chemistry   Physics - 2017 - Opoku - Thin Films.pdf》",
])
def test_table_guard_does_not_treat_document_reference_as_numeric_condition(
    knowledge, reference,
):
    filename = "Macro Chemistry   Physics - 2017 - Opoku - Thin Films.pdf"
    knowledge.ingest(filename, ruled_table())

    result = RagPipeline(knowledge, object()).answer(
        f"{reference} Table 1 里 D42-OTS 在 60 °C 的迁移率是多少？"
    )

    source, = result.sources
    assert source.chunk.document_name == filename
    assert source.retrieval_method == "pdf_table"
    assert "60 °C | 3.5 × 10⁻³" in result.answer


def test_table_followup_keeps_the_previously_named_paper(knowledge):
    knowledge.ingest_many([
        ("first-study.pdf", paper(["D42-OTS mobility is discussed here, without a table."])),
        ("other-study.pdf", ruled_table()),
    ])

    evidence = knowledge.paper_evidence(
        "根据 Table 1 告诉我60℃的迁移率",
        previous_question="first-study.pdf 里的 D42-OTS 在60℃的迁移率是多少？",
    )

    assert evidence.answer is None
    assert {r.chunk.document_name for r in evidence.candidates} == {"first-study.pdf"}


def test_heading_free_paper_samples_results_and_late_body():
    doc = Document(id="x", name="long.pdf", extension=".pdf", size=100)
    pages = [Section(page=i, text=f"Page {i} contains an experiment. " * 160)
             for i in range(1, 11)]
    pages[8].text += "\nIn summary, the crystal grains aligned with the channel.\n"
    pages[9].text = "REFERENCES AND NOTES\nExcluded citation list."

    sources = paper_passages(doc, pages)

    assert any(2 < item.chunk.page < 9 for item in sources)
    assert any("the crystal grains aligned" in item.chunk.text for item in sources)
    assert not any(item.chunk.page == 10 for item in sources)
