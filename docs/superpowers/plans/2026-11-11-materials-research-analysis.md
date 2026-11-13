# OFET literature analysis implementation plan

**Goal** Add a cross-paper analysis view that researchers can filter, plot, trace to source, and export reproducibly.

**Scope** Implement the next iteration of the [materials roadmap](2026-11-02-materials-roadmap.md). Use OFET as the first structured template. Keep sample and experiment identity on every observation, retain existing record IDs and revisions, and require field evidence for reported values. Keep structure recognition, chart digitization and prediction in their later roadmap stages.

- [x] Extend `domain/materials.py`, `application/materials.py` and the existing evidence editor with optional raw experiment fields. Verify old records and drafts, provenance, and reviewed origin classification.
- [x] Implement `application/material_analysis.py` over approved observations. Test units against metric dimensions, value qualifiers, and comparison groups. Preserve unrecognized values in the table.
- [x] Add the cross-paper analysis tab with linked filters, table, individual observation plots, and PDF or SI evidence. Show comparison coverage and missing field status. Check point selection and box selection in Edge.
- [x] Export filtered records, chart settings, source locations and chart data as a reproducible bundle. Render the same Altair specification to SVG and PDF. Verify that exports match the active filters and selected revisions.
- [x] Exercise the workflow with a small real-paper pilot and document the ground truth and held-out paper. Report workflow checks separately from extraction accuracy.
