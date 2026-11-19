"""Compare one reviewed pair and open its original evidence on demand."""

import streamlit as st

from rag_workbench.application.experiment_comparison import compare_experiment
from rag_workbench.application.material_analysis import Observation
from rag_workbench.infrastructure.parsing import quote_in_source
from rag_workbench.ui.material_viewer import show_original

KEY = "materials_experiment"
STATES = {"same": "一致", "different": "不同", "unknown": "待核实"}
ORIGINS = {"measured": "实测", "calculated": "计算", "digitized": "图中读数", "cited": "转引"}


def _group_label(row: Observation) -> str:
    record = row.record
    return " · ".join(filter(None, (
        record.sample_label, record.material_name, record.property_name,
        f"{record.value_raw} {record.unit_raw or ''}".strip(),
    )))


def render_experiment_comparison(service, rows: list[Observation], field_labels) -> None:
    panel = st.expander("实验对照卡", key=f"{KEY}_panel", on_change="rerun")
    if not panel.open:
        return
    with panel:
        _comparison(service, rows, field_labels)


def _comparison(service, rows: list[Observation], field_labels) -> None:
    groups: dict[str, list[Observation]] = {}
    for row in rows:
        groups.setdefault(row.paper.id, []).append(row)
    papers = {identity: group[0].paper.name for identity, group in groups.items()
              if len(group) >= 2}
    if not papers:
        st.info("同一篇论文至少需要两条已审核记录才能建立对照。")
        return
    labels = {key: f"{index}. {name}" for index, (key, name) in enumerate(papers.items(), 1)}
    paper = st.selectbox(
        "对照论文", list(labels), format_func=labels.__getitem__, key=f"{KEY}_paper",
    )
    options = {item.record.id: item for item in groups[paper]}
    labels = {identity: f"{index}. {_group_label(row)}"
              for index, (identity, row) in enumerate(options.items(), 1)}
    baseline_column, treatment_column = st.columns(2)
    baseline_id = baseline_column.selectbox(
        "基线组", [None, *options], key=f"{KEY}_{paper}_baseline",
        format_func=lambda identity: labels.get(identity, "选择基线记录"),
    )
    treatment_id = treatment_column.selectbox(
        "处理组", [None, *(identity for identity in options if identity != baseline_id)],
        key=f"{KEY}_{paper}_{baseline_id}_treatment",
        format_func=lambda identity: labels.get(identity, "选择处理记录"),
    )
    documents = {item.id: item for item in service.repo.documents()
                 if item.id == paper or item.parent_id == paper}
    for column, role, identity in (
        (baseline_column, "基线组", baseline_id), (treatment_column, "处理组", treatment_id),
    ):
        if identity is not None:
            with column:
                _group(service, options[identity], documents, field_labels, role)
    if baseline_id is None or treatment_id is None:
        return
    result = compare_experiment(options[baseline_id], options[treatment_id])
    if result.reason:
        st.info(f"暂不计算差值。{result.reason}")
    else:
        st.metric("观察差值（处理组 − 基线组）", f"{result.difference:+.6g} {result.unit}")
        prior = f"换算后  {result.baseline:.6g} → {result.treatment:.6g} {result.unit}"
        if result.percent is not None:
            prior += f" · 相对基线 {result.percent:+.4g}%"
        st.caption(prior)
    differences = [item for item in result.conditions if item.state == "different"]
    unknown = [item for item in result.conditions if item.state == "unknown"]
    if len(differences) > 1:
        st.warning("多项记录条件不同，观察差异无法归因于单一因素。")
    elif unknown:
        st.caption("仍有条件待核实，单一因素的作用尚不能确认。")
    _condition_table(result.conditions, field_labels)


def _condition_table(conditions, field_labels) -> None:
    rows = [
        {"条件": field_labels.get(item.field, item.field),
         "基线组": ORIGINS.get(item.baseline, item.baseline)
         if item.field == "observation_origin" else item.baseline,
         "处理组": ORIGINS.get(item.treatment, item.treatment)
         if item.field == "observation_origin" else item.treatment,
         "核对": STATES[item.state]}
        for state in ("different", "unknown", "same")
        for item in conditions
        if item.state == state and (item.baseline != "未记录" or item.treatment != "未记录")
    ]
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")
    missing = [field_labels.get(item.field, item.field) for item in conditions
               if item.baseline == item.treatment == "未记录"]
    if missing:
        st.caption("两组均未记录 · " + "、".join(missing))


def _group(service, row, documents, field_labels, role) -> None:
    record = row.record
    st.text(_group_label(row))
    st.caption(f"{row.document.name} · 已审核修订 {record.revision}")
    if not record.evidence:
        st.caption("此记录没有可定位的字段引文。")
        return
    blocks = {block.id: block for identity in documents for block in service.repo.blocks(identity)}
    references = [
        (ref, blocks[ref.block_id]) for ref in record.evidence
        if ref.block_id in blocks and quote_in_source(ref.quote, blocks[ref.block_id].text)
    ]
    references.sort(key=lambda pair: pair[0].field != "value_raw")
    if not references:
        st.caption("字段引文已不可定位，请检查当前论文及补充材料。")
        return
    labels = [
        f"{index}. {field_labels.get(ref.field, ref.field)} · "
        f"{documents[block.document_id].name} · "
        f"{f'第 {block.page} 页' if block.page else '文本片段'}"
        for index, (ref, block) in enumerate(references, 1)
    ]
    prefix = f"{KEY}_{role}_{record.id}_{record.revision}"
    index = st.selectbox(f"{role}证据", range(len(references)), format_func=labels.__getitem__,
                         key=f"{prefix}_evidence")
    ref, block = references[index]
    st.text(ref.quote)
    if st.button(f"核对{role}原文", key=f"{prefix}_view"):
        show_original(service.repo, documents[block.document_id], block, ref.quote)
