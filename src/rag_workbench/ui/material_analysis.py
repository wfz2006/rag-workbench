"""Cross-paper exploration of approved experimental observations."""

import hashlib
import json
from collections import Counter

import altair as alt
import streamlit as st
import vl_convert as vlc

from rag_workbench.application.material_analysis import CONTEXT_AXES, load_observations
from rag_workbench.application.material_export import export_analysis
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.research import EvidenceSource
from rag_workbench.ui.material_viewer import render_source_viewer
from rag_workbench.ui.research_capture import capture_button

KEY = "materials_analysis"
GROUPS = "comparison"
CONDITIONS = {
    "carrier": "载流子", "regime": "工作区间", "statistic": "统计口径",
    "measurement_method": "测量方法", "measurement_environment": "测试环境",
    "device_structure": "器件结构", "deposition_method": "成膜方式",
    "electrode": "电极", "dielectric": "介电层",
    "anneal_temperature": "退火温度", "anneal_duration": "退火时间",
    "measurement_temperature": "测试温度", "film_thickness": "膜厚",
    "channel_length": "沟道长度", "channel_width": "沟道宽度",
}
AVAILABILITY = {
    "reported": "有记录", "not_reported": "原文未报告", "not_applicable": "不适用",
    "not_extracted": "尚未提取", "unknown": "待核实",
}
ORIGINS = {
    "measured": "实测", "calculated": "计算", "digitized": "图中读数", "cited": "引用",
}
VALUE_TYPES = {"scalar": "点值", "uncertainty": "带误差", "range": "范围", "bound": "界限"}


def _fingerprint(rows, settings):
    payload = [[(row.record.id, row.record.revision) for row in rows], settings]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:20]


def _filter_rows(rows):
    papers = {row.paper.id: row.paper.name for row in rows}
    duplicates = Counter(papers.values())
    paper_labels = {
        identity: f"{name} · {identity[:12]}" if duplicates[name] > 1 else name
        for identity, name in papers.items()
    }
    metrics = {row.metric.key: row.metric.label for row in rows if row.metric is not None}
    left, right = st.columns(2)
    paper_ids = left.multiselect(
        "论文", list(papers), format_func=paper_labels.get,
        placeholder="全部论文", key=f"{KEY}_papers",
    )
    materials = right.multiselect(
        "材料", sorted({row.record.material_name for row in rows}, key=str.casefold),
        placeholder="全部材料", key=f"{KEY}_materials",
    )
    metric = left.selectbox(
        "性能指标", [None, *metrics], index=1 if metrics else 0,
        format_func=lambda value: metrics.get(value, "全部指标"), key=f"{KEY}_metric",
    )
    condition = right.selectbox(
        "按实验条件筛选", [None, *CONDITIONS],
        format_func=lambda value: CONDITIONS.get(value, "全部条件"), key=f"{KEY}_condition",
    )
    eligible = [
        row for row in rows
        if (not paper_ids or row.paper.id in paper_ids)
        and (not materials or row.record.material_name in materials)
        and (metric is None or (row.metric and row.metric.key == metric))
    ]
    values = []
    if condition:
        available = {getattr(row.record, condition) for row in eligible}
        values = st.multiselect(
            CONDITIONS[condition], sorted(available, key=lambda value: (value or "").casefold()),
            format_func=lambda value: value or "缺少条件", placeholder="全部取值",
            key=f"{KEY}_values_{condition}",
        )
        if values:
            eligible = [row for row in eligible if getattr(row.record, condition) in values]
    return eligible, {
        "papers": paper_ids, "materials": materials, "metric": metric,
        "condition": condition, "condition_values": values,
    }


def _comparison_groups(rows):
    groups, labels = {}, {}
    for row in rows:
        key = row.comparison_key
        if key is not None and key not in groups:
            groups[key] = f"组 {len(groups) + 1}"
            labels[groups[key]] = row.comparison_label
    labels["口径待确认"] = "缺少可比条件，按单条观测展示"
    return groups, labels


def _plot_rows(rows, axis, logarithmic, groups):
    plotted = []
    for row in rows:
        quantity = row.value
        if quantity is None or quantity.kind == "bound":
            continue
        if axis == GROUPS:
            x = groups.get(row.comparison_key, "口径待确认")
        else:
            context = row.contexts.get(axis)
            if context is None or context.kind != "scalar":
                continue
            x = context.value
        y, low, high = quantity.value, quantity.low, quantity.high
        if logarithmic:
            if y is not None and y <= 0:
                continue
            if low is not None and low <= 0:
                low = high = None
        if y is None and (low is None or high is None):
            continue
        record = row.record
        plotted.append({
            "record_id": record.id, "revision": record.revision, "x": x, "y": y,
            "low": low, "high": high, "group": groups.get(row.comparison_key, "口径待确认"),
            "comparison_label": row.comparison_label,
            "material": record.material_name, "sample": record.sample_label or "—",
            "paper": row.paper.name,
            "reported": f"{record.value_raw} {record.unit_raw or ''}".strip(),
        })
    return plotted


def _chart(data, axis, y_title, logarithmic, *, interactive):
    x = (
        alt.X("x:N", title="比较组", sort=None)
        if axis == GROUPS else alt.X(
            "x:Q", title=f"{CONTEXT_AXES[axis][0]} ({CONTEXT_AXES[axis][1]})",
            scale=alt.Scale(zero=False), axis=alt.Axis(format="~g"),
        )
    )
    scale = alt.Scale(type="log" if logarithmic else "linear", zero=False)
    tooltips = [
        alt.Tooltip("material:N", title="材料"), alt.Tooltip("sample:N", title="样品"),
        alt.Tooltip("reported:N", title="原文值"), alt.Tooltip("paper:N", title="论文"),
        alt.Tooltip("group:N", title="比较组"),
    ]
    base = alt.Chart(alt.Data(values=data)).encode(
        x=x, color=alt.Color("group:N", title="比较组"),
        tooltip=tooltips,
    )
    points = base.mark_circle(size=85, opacity=0.82).encode(
        y=alt.Y("y:Q", title=y_title, scale=scale),
    )
    intervals = base.mark_rule(strokeWidth=2).encode(
        y=alt.Y("low:Q", title=y_title, scale=scale), y2="high:Q",
    ).transform_filter("isValid(datum.low) && isValid(datum.high)")
    if interactive:
        points = points.add_params(alt.selection_point(name="observation", fields=["record_id"]))
        if axis != GROUPS:
            points = points.add_params(alt.selection_interval(name="brush", encodings=["x", "y"]))
    chart = alt.layer(intervals, points).properties(height=330)
    if not interactive:
        chart = chart.properties(width=700).configure(font="Microsoft YaHei")
    return chart


def _chart_selection(state, plotted):
    selected = {item["record_id"] for item in state.get("observation", []) if "record_id" in item}
    brush = state.get("brush") or {}
    if len(brush.get("x", [])) == 2 and len(brush.get("y", [])) == 2:
        left, right = sorted(brush["x"])
        bottom, top = sorted(brush["y"])
        selected.update(
            point["record_id"] for point in plotted if point["y"] is not None
            and left <= point["x"] <= right and bottom <= point["y"] <= top
        )
    return selected


def _render_completeness(rows):
    panel = st.expander("实验条件完整度", key=f"{KEY}_completeness", on_change="rerun")
    if not panel.open:
        return
    with panel:
        status_rows, chart_rows = [], []
        for field, label in CONDITIONS.items():
            counts = dict.fromkeys(AVAILABILITY, 0)
            for row in rows:
                record = row.record
                status = "reported" if getattr(record, field) else record.field_availability.get(
                    field, "unknown",
                )
                counts[status] += 1
            status_rows.append({
                "字段": label, **{AVAILABILITY[key]: count for key, count in counts.items()},
            })
            chart_rows.extend(
                {"字段": label, "状态": AVAILABILITY[key], "记录数": count}
                for key, count in counts.items() if count
            )
        chart = alt.Chart(alt.Data(values=chart_rows)).mark_bar().encode(
            x=alt.X("记录数:Q", axis=alt.Axis(tickMinStep=1)),
            y=alt.Y("字段:N", sort=list(CONDITIONS.values())),
            color=alt.Color("状态:N", scale=alt.Scale(
                domain=list(AVAILABILITY.values()),
                range=["#22756b", "#b76b2c", "#8e80b0", "#d5a44c", "#9099a7"],
            ), legend=alt.Legend(orient="bottom")),
            tooltip=["字段:N", "状态:N", "记录数:Q"],
        ).properties(height=310)
        st.altair_chart(chart, width="stretch", alt="各实验字段的填报及缺失状态")
        st.dataframe(status_rows, hide_index=True, height=255)


def _observation_table(rows, groups, fingerprint):
    table = []
    for row in rows:
        record, quantity = row.record, row.value
        table.append({
            "材料": record.material_name, "样品": record.sample_label,
            "指标": row.metric.label if row.metric else record.property_name,
            "原始值": record.value_raw, "原始单位": record.unit_raw,
            "规范数值": quantity.value if quantity else None,
            "规范单位": quantity.unit if quantity else None,
            "数值类型": VALUE_TYPES[quantity.kind] if quantity else "待核实",
            "规范下界": quantity.low if quantity else None,
            "规范上界": quantity.high if quantity else None,
            "数据来源": ORIGINS.get(record.observation_origin, "待核实"),
            "比较组": groups.get(row.comparison_key, "口径待确认"),
            "论文": row.paper.name, "修订": record.revision,
        })
    event = st.dataframe(
        table, hide_index=True, height=min(420, 40 + 36 * len(table)),
        on_select="rerun", selection_mode="single-row", key=f"{KEY}_table_{fingerprint}",
        alt="选中实验记录后可查看字段证据",
    )
    indexes = event.selection.rows
    return rows[indexes[0]] if indexes and 0 <= indexes[0] < len(rows) else None


def _focus_evidence(block, quote):
    st.session_state["materials_source_focus"] = {
        "document_id": block.document_id, "block_id": block.id, "quote": quote,
    }


def _open_review(record):
    st.session_state["materials_tabs"] = "提取与核对"
    st.session_state["materials_selected_doc"] = record.document_id
    st.session_state["materials_document_focus"] = record.document_id
    st.session_state["materials_pending_record"] = record.id
    st.session_state["materials_pending_record_document"] = record.document_id
    st.session_state["materials_review_view"] = "record"


def _record_details(service, row, labels, groups, field_labels):
    record = row.record
    with st.expander("所选记录与原文证据", expanded=True):
        sample = record.sample_label or "无样品编号"
        st.text(f"{record.material_name} · {sample} · 修订 {record.revision}")
        st.caption(labels[groups.get(row.comparison_key, "口径待确认")])
        st.button("打开记录核对", key=f"{KEY}_review_{record.id}",
                  on_click=_open_review, args=(record,))
        st.dataframe([
            {"字段": label, "原文值": getattr(record, field),
             "状态": AVAILABILITY["reported" if getattr(record, field) else
                                 record.field_availability.get(field, "unknown")]}
            for field, label in CONDITIONS.items()
        ], hide_index=True, height=180)
        scope = [doc for doc in service.repo.documents()
                 if doc.id == row.paper.id or doc.parent_id == row.paper.id]
        blocks = [block for doc in scope for block in service.repo.blocks(doc.id)]
        by_id = {block.id: block for block in blocks}
        references = sorted(
            (ref for ref in record.evidence if ref.block_id in by_id),
            key=lambda ref: ref.field != "value_raw",
        )
        if not references:
            st.info("此记录没有可定位的字段证据。")
            return
        source_names = {doc.id: doc.name for doc in scope}
        choices = []
        for index, ref in enumerate(references, start=1):
            block = by_id[ref.block_id]
            label = field_labels.get(ref.field, ref.field)
            page = f" · 第 {block.page} 页" if block.page else ""
            choices.append(f"{index}. {label} · {source_names[block.document_id]}{page}")
        ref_index = st.selectbox(
            "字段证据", range(len(references)), key=f"{KEY}_evidence_{record.id}_{record.revision}",
            format_func=choices.__getitem__,
            on_change=_focus_selected_evidence,
            args=(record.id, record.revision, references, by_id),
        )
        ref = references[ref_index]
        block = by_id[ref.block_id]
        st.text(ref.quote)
        capture_button(
            EvidenceSource(
                origin="material", library="materials", document_id=block.document_id,
                name=source_names[block.document_id], page=block.page, quote=ref.quote,
                locator=block.id, record_id=record.id, record_revision=record.revision,
                field=ref.field,
            ),
            key=f"research_material_{record.id}_{record.revision}_{ref_index}",
        )
        st.button("在原文中查看", key=f"{KEY}_locate_{record.id}",
                  on_click=_focus_evidence, args=(block, ref.quote))
        viewer_key = f"{KEY}_viewer_{record.id}"
        if f"{viewer_key}_scope" not in st.session_state:
            _focus_evidence(block, ref.quote)
        render_source_viewer(service, row.paper, blocks, key_prefix=viewer_key)


def _focus_selected_evidence(record_id, revision, references, by_id):
    ref = references[st.session_state[f"{KEY}_evidence_{record_id}_{revision}"]]
    _focus_evidence(by_id[ref.block_id], ref.quote)


def _downloads(service, rows, settings, plotted, y_title, fingerprint):
    saved_key = f"{KEY}_export"
    if st.button("准备分析导出", key=f"{KEY}_prepare"):
        st.session_state.pop(saved_key, None)
        try:
            spec = files = None
            if plotted:
                chart = _chart(plotted, settings["axis"], y_title, settings["logarithmic"],
                               interactive=False)
                spec = chart.to_dict()
                files = {"chart.svg": vlc.vegalite_to_svg(spec).encode(),
                         "chart.pdf": vlc.vegalite_to_pdf(spec)}
            data = export_analysis(service.repo, rows, settings, plotted,
                                   chart_spec=spec, chart_files=files)
            st.session_state[saved_key] = (fingerprint, data, files)
        except (AppError, ValueError, RuntimeError) as exc:
            st.error(f"未能准备分析导出：{exc}")
    saved = st.session_state.get(saved_key)
    if saved and saved[0] == fingerprint:
        col1, col2, col3 = st.columns(3)
        col1.download_button("下载分析包", saved[1], "materials-analysis.zip", "application/zip",
                             key=f"{KEY}_download", on_click="ignore")
        if saved[2]:
            col2.download_button("下载 SVG", saved[2]["chart.svg"], "chart.svg", "image/svg+xml",
                                 key=f"{KEY}_svg", on_click="ignore")
            col3.download_button("下载 PDF", saved[2]["chart.pdf"], "chart.pdf", "application/pdf",
                                 key=f"{KEY}_pdf", on_click="ignore")


def render_material_analysis(service, field_labels):
    rows = load_observations(service.repo)
    if not rows:
        st.info("批准实验记录后即可进行跨论文比较。")
        return
    rows, settings = _filter_rows(rows)
    if not rows:
        st.info("没有符合筛选条件的已批准记录。")
        return
    groups, labels = _comparison_groups(rows)
    st.caption(f"{len({row.paper.id for row in rows})} 篇论文 · {len(rows)} 条已批准记录")
    plotted, y_title, selected = [], "", set()
    if settings["metric"]:
        left, right = st.columns([3, 1])
        axes = [axis for axis in CONTEXT_AXES if any(
            axis in row.contexts and row.contexts[axis].kind == "scalar" for row in rows
        )]
        axis = left.selectbox(
            "横轴", [GROUPS, *axes], key=f"{KEY}_axis",
            format_func=lambda value: (
                "按比较口径分组" if value == GROUPS else CONTEXT_AXES[value][0]
            ),
        )
        logarithmic = right.selectbox("纵轴刻度", [False, True], key=f"{KEY}_scale",
                                      format_func=lambda value: "对数" if value else "线性")
        settings.update(axis=axis, logarithmic=logarithmic)
        metric = rows[0].metric
        y_title = f"{metric.label} ({metric.unit})"
        plotted = _plot_rows(rows, axis, logarithmic, groups)
        if plotted:
            fingerprint = _fingerprint(rows, settings)
            epoch = st.session_state.get(f"{KEY}_plot_epoch", 0)
            mode = ["observation"] if axis == GROUPS else ["observation", "brush"]
            event = st.altair_chart(
                _chart(plotted, axis, y_title, logarithmic, interactive=True),
                width="stretch", key=f"{KEY}_plot_{fingerprint}_{epoch}", on_select="rerun",
                selection_mode=mode, alt=f"{y_title}的实验观测与原文报告区间",
            )
            selected = _chart_selection(event.selection, plotted)
            st.caption(f"图中 {len(plotted)} 条，另有 {len(rows) - len(plotted)} 条只显示在表格。")
            if logarithmic:
                st.caption("对数轴只绘制正值及完全为正的误差范围。")
            with st.expander("比较组条件"):
                st.dataframe([
                    {"组": label, "口径与条件": meaning,
                     "绘制观测数": sum(point["group"] == label for point in plotted)}
                    for label, meaning in labels.items()
                ], hide_index=True)
        else:
            st.info("当前条件下没有可绘制的值。可以调整横轴、刻度或查看原始值。")
    else:
        st.caption("选择一个性能指标即可绘图。")
    _render_completeness(rows)
    if selected:
        rows = [row for row in rows if row.record.id in selected]
        plotted = [point for point in plotted if point["record_id"] in selected]
        st.caption(f"图中已选 {len(rows)} 条")
        st.button("清除图中选择", key=f"{KEY}_clear", on_click=_clear_plot)
    settings["chart_selection"] = sorted(selected)
    fingerprint = _fingerprint(rows, settings)
    row = _observation_table(rows, groups, fingerprint)
    _downloads(service, rows, settings, plotted, y_title, fingerprint)
    if row is not None or len(rows) == 1:
        _record_details(service, row or rows[0], labels, groups, field_labels)
    else:
        st.caption("选择图点或表格行核对原文。只有范围的观测可从表格选取。")


def _clear_plot():
    key = f"{KEY}_plot_epoch"
    st.session_state[key] = st.session_state.get(key, 0) + 1
