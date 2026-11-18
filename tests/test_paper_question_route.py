import io
import json
from types import SimpleNamespace

import pytest
from reportlab.pdfgen.canvas import Canvas

from rag_workbench.application.paper_sources import paper_passages
from rag_workbench.application.pipeline import RagPipeline
from rag_workbench.application.prompts import EVIDENCE_SYSTEM
from rag_workbench.bootstrap import build_services
from rag_workbench.domain.models import ChatMessage, Document, Section

from .fakes import FakeProvider
from .test_pdf_table_lookup import (
    condition_table,
    pdfium,
    qualified_table,
    ruled_table,
    solvent_free_table,
)


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

    monkeypatch.setattr("rag_workbench.application.knowledge.inspect_pdf_table", lookup)
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

    monkeypatch.setattr("rag_workbench.application.knowledge.inspect_pdf_table", lookup)
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

    monkeypatch.setattr("rag_workbench.application.knowledge.inspect_pdf_table", lookup)
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
        previous_questions=("first-study.pdf 里的 D42-OTS 在60℃的迁移率是多少？",),
    )

    assert not evidence.candidates
    assert "first-study.pdf" in evidence.answer and "Table 1" in evidence.answer


def test_table_pointer_recovers_the_verified_document_and_label(knowledge):
    knowledge.ingest_many([
        ("薄膜文献1.pdf", solvent_free_table()),
        ("文献3(1).pdf", paper(["Review discussion of film processing."])),
        ("文献4(1).pdf", paper(["Another paper without device tables."])),
    ])
    services = build_services(knowledge.settings, embedder=knowledge.embedder, provider=object())
    session = services.repo.new_session()
    first_question = "Table 1 中 PY3-OTS 在 60℃ 的迁移率是多少？"

    first = services.ask(session, first_question)
    assert first.mode == "knowledge_base"
    source, = first.sources
    assert (source.chunk.document_name, source.chunk.page) == ("薄膜文献1.pdf", 3)
    assert "2.3 × 10⁻³" in first.answer
    saved_question = services.history(session)[0]
    assert saved_question.table_reference.document_id == source.chunk.document_id
    assert "table_reference" not in saved_question.model_dump()

    second = services.ask(session, "该表中 60℃ 的阈值电压是多少？")
    assert second.mode == "knowledge_base", second.answer
    source, = second.sources
    assert (source.chunk.document_name, source.chunk.page) == ("薄膜文献1.pdf", 3)
    assert "−8" in second.answer


@pytest.mark.parametrize("replacement_name", ["another-study.pdf", "original-study.pdf"])
def test_saved_table_pointer_never_substitutes_a_new_paper(knowledge, replacement_name):
    original = knowledge.ingest("original-study.pdf", solvent_free_table())
    knowledge.ingest("review.pdf", paper(["Notes on film preparation."]))
    services = build_services(knowledge.settings, embedder=knowledge.embedder, provider=object())
    session = services.repo.new_session()
    question = "Table 1 中 PY3-OTS 在 60℃ 的迁移率是多少？"
    source, = services.ask(session, question).sources
    assert source.chunk.document_id == original.id

    knowledge.delete(original.id)
    with (
        pdfium.PdfDocument.new() as merged,
        pdfium.PdfDocument(paper(["Another study."])) as intro,
        pdfium.PdfDocument(solvent_free_table()) as data,
    ):
        merged.import_pages(intro)
        merged.import_pages(data)
        output = io.BytesIO()
        merged.save(output)
    replacement = knowledge.ingest(replacement_name, output.getvalue())
    reopened = build_services(knowledge.settings, embedder=knowledge.embedder, provider=object())
    for followup in (
        "该表中 60℃ 的阈值电压是多少？",
        "该表（Table 1）中 PY3-OTS 在 60℃ 的阈值电压是多少？",
        "阈值电压呢？",
    ):
        result = reopened.ask(session, followup)
        assert result.mode == "insufficient_evidence", followup
        assert not result.sources
    explicit = reopened.ask(session, f"{replacement_name} {question}")
    assert [s.chunk.document_id for s in explicit.sources] == [replacement.id]


@pytest.fixture
def paper_with_two_tables(knowledge):
    threshold = condition_table(
        ("Sample", "Annealing temp. (°C)", "VT(V)"),
        (("para-IDT(QNCN)2", "80", "-40"), ("meta-IDT(QNCN)2", "80", "-42")),
    )
    with (
        pdfium.PdfDocument.new() as merged,
        pdfium.PdfDocument(qualified_table()) as first,
        pdfium.PdfDocument(threshold) as second,
    ):
        merged.import_pages(first)
        merged.import_pages(second)
        data = io.BytesIO()
        merged.save(data)
    return knowledge.ingest("thin-films-study.pdf", data.getvalue()).name


@pytest.mark.parametrize("reference", ["该表", "上表", "Table 4"])
def test_explicit_or_inherited_table_miss_cannot_use_another_table(
    knowledge, paper_with_two_tables, reference,
):
    provider = FakeProvider()
    pipeline = RagPipeline(knowledge, provider)
    first = (f"{paper_with_two_tables} 的 Table 4 中 meta-IDT(QNCN)2 "
             "在 80℃ 退火的线性迁移率是多少？")
    result = pipeline.answer(first)
    assert "80 °C | 0.021" in result.answer
    assert [source.chunk.page for source in result.sources] == [1]
    history = [ChatMessage(role="user", content=first),
               ChatMessage(role="assistant", content=result.answer)]

    missing = pipeline.answer(f"{reference}中 80℃ 退火的阈值电压是多少？", history)
    assert missing.mode == "insufficient_evidence"
    assert not missing.sources
    assert not provider.calls  # The containing paper is not evidence for a different table.

    explicit = pipeline.answer("根据 Table 2 回答 80℃ 退火的阈值电压", history)
    assert explicit.mode == "knowledge_base" and "80 °C | −42" in explicit.answer
    assert [source.chunk.page for source in explicit.sources] == [2]


def test_table_pointer_requires_an_unambiguous_previous_table(knowledge, paper_with_two_tables):
    first = (f"{paper_with_two_tables} 的 Table 4 和 Table 2 中 meta-IDT(QNCN)2 "
             "在 80℃ 的器件数据")
    provider = FakeProvider()
    result = RagPipeline(knowledge, provider).answer(
        "该表中 meta-IDT(QNCN)2 在 80℃ 退火的阈值电压是多少？",
        [ChatMessage(role="user", content=first)],
    )
    assert result.mode == "insufficient_evidence"
    assert not result.sources and not provider.calls


def test_repeated_table_pointer_keeps_the_named_document(knowledge, paper_with_two_tables):
    knowledge.ingest("other-study.pdf", qualified_table())
    pipeline = RagPipeline(knowledge, FakeProvider())
    first = (f"{paper_with_two_tables} 的 Table 4 中 meta-IDT(QNCN)2 "
             "在 80℃ 退火的线性迁移率是多少？")
    history = [ChatMessage(role="user", content=first)]
    for question in (
        "该表中 meta-IDT(QNCN)2 在 80℃ 退火的饱和迁移率是多少？",
        "上表中 80℃ 退火的饱和迁移率是多少？",
    ):
        result = pipeline.answer(question, history)
        source, = result.sources
        assert (source.chunk.document_name, source.chunk.page) == (paper_with_two_tables, 1)
        assert "80 °C | 0.043" in result.answer
        history.append(ChatMessage(role="user", content=question))


@pytest.mark.parametrize("question", [
    "该表述是否严谨？",
    "该表达有什么问题？",
    "该表示方法对吗？",
    "以上表现怎么评价？",
    "薄膜上表面形貌怎么样？",
    "该表征说明了什么？",
])
def test_non_table_phrases_keep_general_route(knowledge, question):
    assert RagPipeline(knowledge, FakeProvider()).answer(question).mode == "general"


def test_short_followup_cannot_drop_unverified_table_conditions(knowledge, paper_with_two_tables):
    first = (f"{paper_with_two_tables} 的 Table 4 中 meta-IDT(QNCN)2 "
             "用氯苯制膜且在 80℃ 退火的迁移率是多少？")
    provider = FakeProvider()
    pipeline = RagPipeline(knowledge, provider)
    assert pipeline.answer(first).mode == "insufficient_evidence"

    result = pipeline.answer("阈值电压呢？", [ChatMessage(role="user", content=first)])
    assert result.mode == "insufficient_evidence"
    assert not result.sources and not provider.calls


def test_short_table_followups_keep_scope_and_replace_only_the_requested_part(knowledge):
    knowledge.ingest_many([
        ("薄膜文献1.pdf", ruled_table()),
        ("other-study.pdf", paper(["Other D42-OTS and D42-Bare results"])),
    ])
    pipeline = RagPipeline(knowledge, object())
    history = []
    for question, expected in [
        ("薄膜文献1里 D42-OTS 在 25 ℃ 的迁移率", ("| 25 °C | 2.8 × 10⁻⁷",)),
        ("那 60 度呢", ("| 60 °C | 3.5 × 10⁻³",)),
        ("阈值电压呢", ("| 60 °C | −8",)),
        ("和 D42-Bare 比呢", ("D42-OTS | 60 °C | −8", "D42-Bare | 60 °C | −10")),
        ("迁移率呢", ("D42-OTS | 60 °C | 3.5 × 10⁻³", "D42-Bare | 60 °C | 0.06")),
    ]:
        result = pipeline.answer(question, history)
        assert result.mode == "knowledge_base", result.answer
        assert result.sources[0].chunk.document_name == "薄膜文献1.pdf"
        assert result.sources[0].retrieval_method == "pdf_table"
        assert "D42-OTS" in result.answer
        assert all(line in result.answer for line in expected), (question, result.answer)
        if history:
            assert "25 °C" not in result.answer
        history += [
            ChatMessage(role="user", content=question),
            ChatMessage(role="assistant", content="一些旧答案，其中数字不应作为检索条件。"),
        ]


def test_titles_scope_retrieval_debug_and_chat_including_note_summaries(knowledge):
    knowledge.ingest_many([
        ("薄膜文献1.pdf", ruled_table()),
        ("文献2整理.txt", "这份记录总结了晶格和器件结构。".encode()),
        ("文献2.pdf", paper(["Similar filename, unrelated experiment."])),
        ("an-internal-note.txt", "开关比 讨论 记录了关键数据和要点。".encode()),
    ])
    for question, name in [
        ("薄膜文献1 里 Table 1 的开关比怎么查", "薄膜文献1.pdf"),
        ("文献2整理里记了哪些关键数据", "文献2整理.txt"),
    ]:
        hits = knowledge.retrieve(question)
        assert hits and all(hit.eligible and hit.chunk.document_name == name for hit in hits)
        result = RagPipeline(knowledge, FakeProvider()).inspect(question)
        assert {r.chunk.document_name for r in result.candidates} == {name}


def test_verified_table_ambiguity_asks_to_specify_table_instead_of_calling_model(knowledge):
    stream = io.BytesIO()
    with (
        pdfium.PdfDocument.new() as result,
        pdfium.PdfDocument(ruled_table()) as first,
        pdfium.PdfDocument(ruled_table(table_number=2)) as second,
    ):
        result.import_pages(first)
        result.import_pages(second)
        result.save(stream)
    knowledge.ingest("comparison-study.pdf", stream.getvalue())
    pipeline = RagPipeline(knowledge, object())

    answer = pipeline.answer("D42-OTS 在 60 度下的迁移率")
    assert answer.mode == "insufficient_evidence"
    assert "第 1 页 Table 1" in answer.answer and "第 2 页 Table 2" in answer.answer
    assert "请明确表号" in answer.answer and not answer.sources

    clarified = pipeline.answer("Table 2 里 D42-OTS 在 60 度下的迁移率")
    assert clarified.mode == "knowledge_base"
    assert clarified.sources[0].chunk.page == 2


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
