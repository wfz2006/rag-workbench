# Desktop retrieval tuning notes (0.1.11)

Scope: the portable Windows build of RAG Workbench 0.1.11. These notes record
why a knowledge base can show "ready" while answers still fall back to a
general-model response, and which settings were changed to fix it.

## 1. Layout and startup

The executable must stay next to `runtime/`, `resources/` and `models/`.
On first launch the shell creates `user-data/` next to the executable:

```
user-data/
  .env                  configuration and API key (never commit or share)
  knowledge/            ingested sources, metadata and index
  models/               local copy of the embedding model
  .streamlit/           UI config
  desktop.log           startup log
  desktop-session.json  random loopback port for this session
```

The Streamlit backend listens on `127.0.0.1` only. The port changes on each
launch; read `desktop-session.json` rather than assuming a fixed one.
`prepare_profile()` writes `.env` only when it does not exist, so shipping a
key-free template is safe. Editing `.env` requires a full restart.

A key-free template lives at `docs/rag-workbench-0.1.11.env.example`.
Copy its contents into `user-data/.env` and fill in `DEEPSEEK_API_KEY`.

## 2. Recommended overrides

| Setting | Default | Recommended | Reason |
| --- | --- | --- | --- |
| `RAG_SIMILARITY_THRESHOLD` | 0.45 | 0.40 | Short Chinese queries against an English corpus score roughly 0.48-0.51 with bge-small-zh-v1.5; the 0.1.10 trial build used 0.55 and dropped every candidate. 0.45 is still tight for some queries. |
| `RAG_TOP_K` | 5 | 10 | More candidates reach the evidence reviewer. |
| `RAG_RETRIEVAL_CONTEXT_WINDOW` | 0 | 1 | Pull neighbouring chunks; fixed 400-character windows otherwise cut sentences in half. |
| `RAG_ALLOW_GENERAL` | true | false | Prevent a general-model answer from being presented as knowledge-base output. With evidence missing, the UI reports insufficient material instead. Set back to true to restore the fallback. |
| `RAG_CROSS_LINGUAL_RETRIEVAL` | true | true | Translate Chinese questions for retrieval over English corpora, then merge candidates. Costs one extra LLM call. |
| `RAG_PDF_PARSER` | auto | pypdf | Keep parsing offline and the index signature stable. `auto` tries Docling and may download weights; switching parsers marks existing indexes as `incompatible` and requires a rebuild. |

## 3. Diagnosing "imported but not detected"

The label "general model answer / not from the knowledge base" maps to
`inspect()` returning `route=general`. Only two paths produce it:

1. No candidate is `eligible` - it failed the similarity threshold, the
   lexical check and the context-window rule.
2. Candidates exist but the evidence reviewer returns `answerable=false`,
   because the provided snippets do not fully answer the question.

The "ready" index badge is not a guarantee that an answer can be produced:
`ingest_many()` rebuilds and publishes the index before setting `ready`, so a
successful import only proves that parsing and indexing finished. An
incompatible index is reported as an error, not as a general answer.

Practical checks: inspect the retrieval debug page for scores and eligibility,
confirm the document / chunk / index counters, and make sure no knowledge pack
is active in the sidebar (packs switch `data_dir`).

## 4. Why long PDFs produce poor grounded answers

`pypdf` extracts the text layer only, then a deterministic 400-character
window with 60-character overlap splits it. On a 60-page review this produced
878 chunks containing running headers, page numbers and words cut in half.
Even a question as simple as "what is the title of this review?" could not be
answered from those snippets: retrieval and the evidence reviewer both saw
fragments, not facts.

A working mitigation is to convert the PDF text layer into structured
Markdown before importing:

- drop running headers, page numbers, copyright lines and DOI banners;
- restore ligatures and rebuild hard-wrapped lines into paragraphs;
- turn numbered section headings into `##` headings;
- write the title / journal / topic as metadata at the top;
- keep a blank line between paragraphs.

The chunker treats `\n` as a boundary, so each chunk becomes a complete
semantic unit instead of a mid-sentence slice. Re-ingesting the same review as
Markdown produced 841 chunks; the title question and a factual question about
device structures then returned grounded answers with citations.
`scripts/build_structured_markdown.py` automates the conversion.

## 5. Known limits

- Grounded answers require every claim to map to a cited snippet. A
  "summarise the whole paper across five dimensions" request is rejected by
  the evidence reviewer by design, not because ingestion failed.
- Chinese meta-information questions such as "what is the title?" can still
  miss; English wording or distinctive original terms is more reliable.
- Text-layer parsing only. Images, equations, figure captions, molecular
  structures and plotted values do not enter the knowledge base.
- Enabling Docling (`auto`) needs network access for weights and an index
  rebuild; keep `pypdf` for fully offline use.
- Cross-lingual retrieval adds one translation call per question.

## 6. Privacy

A used `user-data/` contains the API key, imported documents, indexes,
session records and logs. Never commit it or attach it to a release. When
redistributing the portable build, ship only the clean bundle content.
