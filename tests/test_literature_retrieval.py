import pytest

from rag_workbench.application import literature_retrieval
from rag_workbench.domain.models import Chunk, Document


def _rank(question, passages):
    chunks = []
    catalog = {}
    for i, (document, page, text) in enumerate(passages):
        catalog[document] = Document(
            id=document, name=document, extension="." + document.rsplit(".", 1)[-1], size=0
        )
        chunks.append(
            Chunk(
                id=str(i),
                document_id=document,
                document_name=document,
                text=text,
                ordinal=i,
                page=page,
                start=0,
                end=len(text),
            )
        )
    return literature_retrieval.ranked_literature_matches(question, chunks, catalog)


def test_cross_language_ranking_requires_compound_carrier_and_copolymer():
    assert _rank(
        "naphthalene diimide 共聚物的电子迁移率",
        [
            ("notes.txt", 1, "Naphthalene diimide copolymer electron mobility was 0.8 cm²/Vs."),
            ("different.pdf", 1, "Naphthalene diimide copolymer hole mobility was 0.9 cm²/Vs."),
            ("neat.pdf", 1, "The neat naphthalene diimide electron mobility was 0.5 cm²/Vs."),
            (
                "paper.pdf",
                1,
                "Naphthalene\ndiimide copolymers show electron mobilities of 0.8 cm²/Vs.",
            ),
        ],
    ) == [3]


def test_defined_acronym_stays_in_its_paper_and_retains_film_morphology():
    assert _rank(
        "diketopyrrolopyrrole 微晶薄膜的空穴迁移率",
        [
            ("paper.pdf", 2, "Devices were based on diketopyrrolopyrrole (DPP) or quinacridone."),
            (
                "paper.pdf",
                3,
                "The hole transport of DPP was confirmed. Field effect mobilities "
                "are 7.19 × 10⁻⁶ cm² V⁻¹ s⁻¹ for the spin-coated devices.",
            ),
            (
                "paper.pdf",
                4,
                "The regenerated DPP film had small needles formed during crystallization.",
            ),
            ("wrong.pdf", 3, "The hole mobility of microcrystalline DPP films was 1.5 cm²/Vs."),
            ("no-morphology.pdf", 1, "Diketopyrrolopyrrole films had a hole mobility of 1 cm²/Vs."),
        ],
    ) == [1]


def test_microcrystalline_query_preserves_specific_processing_qualifiers():
    assert (
        _rank(
            "diketopyrrolopyrrole microcrystalline high-pressure films hole mobility",
            [
                (
                    "paper.pdf",
                    1,
                    "The hole mobility of diketopyrrolopyrrole microcrystalline films "
                    "was 1 cm²/Vs.",
                ),
            ],
        )
        == []
    )


def test_evaporation_is_not_inferred_from_generic_deposition():
    assert _rank(
        "pentacene 蒸镀的空穴迁移率",
        [
            ("a.pdf", 1, "The hole mobility was high in solution-deposited pentacene films."),
            ("b.pdf", 1, "The hole mobility of thermally evaporated pentacene was 0.45 cm²/Vs."),
            (
                "c.pdf",
                1,
                "Thermally evaporated perfluoropentacene had a hole mobility of 1 cm²/Vs.",
            ),
            (
                "d.pdf",
                1,
                "Thermally evaporated pentacene was studied. Another material had high "
                "electron mobility.",
            ),
            (
                "e.pdf",
                1,
                "Perfluoropentacene was evaporated. In comparison, the hole mobility of "
                "solution-deposited pentacene films was 1 cm²/Vs.",
            ),
        ],
    ) == [1]


def test_tips_pentacene_keeps_both_words_and_requires_solution_processing():
    assert _rank(
        "TIPS pentacene 溶液法迁移率",
        [
            ("a.pdf", 1, "The mobility of solution-processed pentacene increased to 0.45 cm²/Vs."),
            ("b.pdf", 1, "The mobility of evaporated TIPS-pentacene was 0.45 cm²/Vs."),
            ("c.pdf", 1, "Solution-processed TIPS-pentacene mobilities reached 1 cm²/Vs."),
        ],
    ) == [2]


@pytest.mark.parametrize(
    ("query", "matches"),
    [
        ("indacenodithienothiophene side chain", [1, 2]),
        ("indacenodithieno[3,2-b]thiophene side chains", [1]),
        ("indacenodithieno[2,3-a]thiophene side chains", []),
    ],
)
def test_explicit_fused_ring_locants_are_preserved(query, matches):
    assert (
        _rank(
            query,
            [
                ("generic.pdf", 1, "Different side chains were discussed."),
                (
                    "paper.pdf",
                    1,
                    "Indacenodithieno [3,2‐b] thiophene with two side chains was synthesized.",
                ),
                (
                    "other.pdf",
                    1,
                    "Indacenodithieno [2,3-b]thiophene with two side chains was synthesized.",
                ),
            ],
        )
        == matches
    )


@pytest.mark.parametrize(
    "query",
    [
        "pentacene 在真空退火后空穴迁移率",
        "pentacene 蒸镀 80 度的空穴迁移率",
        "帮我写 Python 快速排序",
        "pentacene side chain oxidation",
    ],
)
def test_fallback_does_not_drop_unsupported_qualifiers(query):
    assert (
        _rank(
            query,
            [
                (
                    "paper.pdf",
                    1,
                    "The hole mobility of thermally evaporated pentacene was 0.45 cm²/Vs. "
                    "Pentacene had no side chains.",
                ),
            ],
        )
        == []
    )
