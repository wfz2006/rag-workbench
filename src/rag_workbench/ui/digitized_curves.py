"""Local import and review of estimated data from PDF charts."""

import altair as alt
import streamlit as st
from pydantic import ValidationError

from rag_workbench.application.digitized_curves import MAX_CSV_BYTES, import_curve
from rag_workbench.domain.digitized_curves import CurveSource
from rag_workbench.domain.errors import AppError
from rag_workbench.ui.material_viewer import show_pdf_page

KEY = "materials_curves"
ESTIMATE = "图中估读"


def render_digitized_curves(service, documents) -> None:
    papers = {doc.id: doc for doc in documents}
    pdfs = {doc.id: doc for doc in documents if doc.extension == ".pdf"}
    if not pdfs:
        st.info("导入含图表的正文或补充材料 PDF 后即可关联取数结果。")
        return
    labels = {}
    for index, (identity, doc) in enumerate(pdfs.items(), 1):
        parent = papers.get(doc.parent_id)
        context = f"补充材料 · {parent.name}" if parent else "正文"
        labels[identity] = f"{index}. {doc.name} · {context}"
    identity = st.selectbox("曲线来源 PDF", list(pdfs), key=f"{KEY}_pdf",
                            format_func=labels.__getitem__)
    doc = pdfs[identity]
    if st.button("导入 WebPlotDigitizer CSV", key=f"{KEY}_import", type="primary"):
        _import_dialog(service.repo, doc)
    curves = service.repo.digitized_curves(identity)
    if not curves:
        st.caption("这份 PDF 暂无曲线取数记录。")
        return
    by_id = {curve.id: curve for curve in curves}
    labels = {curve.id: f"{index}. {curve.source.figure} · {curve.source.series}"
              for index, curve in enumerate(curves, 1)}
    selected = st.selectbox("已导入曲线", list(by_id), key=f"{KEY}_{identity}_selection",
                            format_func=labels.__getitem__)
    curve = by_id[selected]
    source = curve.source
    st.caption(f"{ESTIMATE} · {doc.name} · PDF 第 {source.page} 页 · {len(curve.points)} 个点")
    st.text(f"{source.figure} · {source.series}")
    st.caption(f"CSV 文件：{curve.csv_name}")
    left, right = st.columns(2)
    if left.button("核对图表原页", key=f"{KEY}_source"):
        _source_dialog(service.repo, doc, source)
    if right.button("移除曲线", key=f"{KEY}_remove", type="tertiary"):
        _remove_dialog(service.repo, curve)
    _show_curve(curve)


def _show_curve(curve) -> None:
    source = curve.source
    x_label = f"{source.x_name} ({source.x_unit})"
    y_label = f"{source.y_name} ({source.y_unit})"
    rows = [{"x": x, "y": y, "order": index} for index, (x, y) in enumerate(curve.points, 1)]
    chart = (alt.Chart(alt.InlineData(values=rows)).mark_line(point=True).encode(
        x=alt.X("x:Q", title=x_label, scale=alt.Scale(zero=False)),
        y=alt.Y("y:Q", title=y_label, scale=alt.Scale(zero=False)),
        order="order:Q", tooltip=[alt.Tooltip("x:Q", title=x_label),
                                  alt.Tooltip("y:Q", title=y_label)],
    ).properties(height=320))
    st.altair_chart(chart, width="stretch", alt=f"{source.figure} {source.series} {ESTIMATE}")
    with st.expander("导入点与单位"):
        st.dataframe([{"点序": row["order"], f"X · {x_label}": row["x"],
                       f"Y · {y_label}": row["y"], "来源": ESTIMATE}
                      for row in rows], hide_index=True, width="stretch")


@st.dialog("导入曲线", width="large")
def _import_dialog(repo, doc) -> None:
    st.text(doc.name)
    st.caption("每次导入一个 XY 数据集，使用 UTF-8 逗号分隔 CSV。无量纲的单位可填 1。")
    with st.form(f"{KEY}_import_form"):
        upload = st.file_uploader("WebPlotDigitizer CSV", type=["csv"])
        header = st.checkbox("首行是列名")
        left, right = st.columns(2)
        figure = left.text_input("图号或分图", placeholder="Fig. 3F", max_chars=120)
        page = right.number_input("PDF 页码", min_value=1, step=1)
        series = st.text_input("曲线或系列名称", max_chars=160)
        left, right = st.columns(2)
        x_name = left.text_input("X 轴名称", max_chars=120)
        y_name = right.text_input("Y 轴名称", max_chars=120)
        x_unit = left.text_input("X 轴单位", max_chars=80)
        y_unit = right.text_input("Y 轴单位", max_chars=80)
        if st.form_submit_button("保存为图中估读", type="primary"):
            if upload is None:
                st.error("请选择 CSV 文件。")
                return
            if upload.size > MAX_CSV_BYTES:
                st.error("CSV 超过 2 MiB，请拆分成较小的数据集。")
                return
            try:
                source = CurveSource(document_id=doc.id, figure=figure, page=page, series=series,
                                     x_name=x_name, x_unit=x_unit, y_name=y_name, y_unit=y_unit)
                curve = import_curve(
                    repo, source, upload.getvalue(), upload.name, has_header=header,
                )
            except ValidationError:
                st.error("请填写图号、页码、曲线名称和两个坐标轴的名称及单位。")
            except AppError as exc:
                st.error(str(exc))
            else:
                st.session_state[f"{KEY}_{doc.id}_selection"] = curve.id
                st.rerun()


@st.dialog("图表原页", width="large")
def _source_dialog(repo, doc, source) -> None:
    st.text(f"{doc.name} · PDF 第 {source.page} 页 · {source.figure} · {ESTIMATE}")
    try:
        show_pdf_page(repo, doc, source.page)
    except AppError as exc:
        st.warning(str(exc))


@st.dialog("移除曲线")
def _remove_dialog(repo, curve) -> None:
    st.text(f"{curve.source.figure} · {curve.source.series}")
    if st.button("确认移除曲线", type="primary"):
        try:
            repo.remove_digitized_curve(curve)
        except AppError as exc:
            st.error(str(exc))
        else:
            st.rerun()
