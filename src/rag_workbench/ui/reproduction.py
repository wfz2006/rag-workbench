import streamlit as st
from pydantic import ValidationError

from rag_workbench.application.reproduction import ReproductionChecklist
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.reproduction import STAGES, STATUSES, ReproductionStep
from rag_workbench.ui.material_viewer import show_original

KEY = "materials_reproduction"


def render_reproduction(service, documents) -> None:
    papers = {doc.id: doc for doc in documents if doc.role == "main"}
    if not papers:
        st.info("导入正文论文后即可添加复现条目。")
        return
    labels = {identity: f"{index}. {doc.name}" for index, (identity, doc) in
              enumerate(papers.items(), 1)}
    paper_id = st.selectbox("复现论文", list(papers), key=f"{KEY}_paper",
                            format_func=labels.__getitem__)
    checklist = ReproductionChecklist(service.repo)
    if st.button("添加复现条目", key=f"{KEY}_add", type="primary"):
        _edit_step(checklist, paper_id)
    steps = checklist.steps(paper_id)
    for stage, label in STAGES.items():
        group = [step for step in steps if step.stage == stage]
        st.subheader(label)
        if not group:
            st.caption("暂无条目")
        for step in group:
            _show_step(checklist, step)


def _show_step(checklist, step) -> None:
    prefix = f"{KEY}_{step.id}"
    with st.container(border=True):
        st.text(step.title)
        st.caption(STATUSES[step.status])
        if step.note:
            st.text(step.note)
        if step.block_id:
            source = checklist.source(step)
            if source is None:
                st.warning("原文位置已不可用。")
            else:
                doc, block = source
                location = f"第 {block.page} 页" if block.page else "文本片段"
                st.caption(f"{doc.name} · {location}")
                st.text(step.quote)
                if st.button("看原文", key=f"{prefix}_view", type="tertiary"):
                    show_original(checklist.repo, doc, block, step.quote)
        if st.button("编辑条目", key=f"{prefix}_edit", type="tertiary"):
            _edit_step(checklist, step.paper_id, step)


@st.dialog("复现条目", width="large")
def _edit_step(checklist: ReproductionChecklist, paper_id: str,
               step: ReproductionStep | None = None) -> None:
    key = f"{KEY}_edit_{step.id}_{step.revision}" if step else f"{KEY}_new_{paper_id}"
    title = st.text_input(
        "步骤或参数", step.title if step else "", max_chars=240, key=f"{key}_title",
    )
    stage = st.selectbox("阶段", list(STAGES), index=list(STAGES).index(step.stage) if step else 0,
                         format_func=STAGES.__getitem__, key=f"{key}_stage")
    status = st.selectbox(
        "核实状态", list(STATUSES), index=list(STATUSES).index(step.status) if step else 0,
        format_func=STATUSES.__getitem__, key=f"{key}_status",
    )
    note = st.text_area("步骤笔记或核查范围", step.note if step else "", max_chars=4000,
                        help="标记为文中未报告时，请写明查过的章节、页码或补充材料。",
                        key=f"{key}_note")
    block_id, quote = _source_fields(checklist, paper_id, step, key)
    if st.button("保存条目", type="primary", key=f"{key}_save"):
        values = dict(paper_id=paper_id, title=title, stage=stage, status=status, note=note,
                      block_id=block_id, quote=quote)
        if step:
            values.update(id=step.id, revision=step.revision)
        try:
            checklist.save(ReproductionStep(**values))
        except ValidationError:
            st.error("请输入步骤或参数名称。")
        except AppError as exc:
            st.error(str(exc))
        else:
            st.rerun()
    if step and st.checkbox("确认移除此条目", key=f"{key}_confirm") and st.button(
        "移除条目", key=f"{key}_remove",
    ):
        try:
            checklist.remove(step)
        except AppError as exc:
            st.error(str(exc))
        else:
            st.rerun()


def _source_fields(checklist, paper_id, step, key) -> tuple[str | None, str]:
    documents = checklist.documents(paper_id)
    current = checklist.source(step) if step and step.block_id else None
    labels = {doc.id: f"{index}. {doc.name} · {'正文' if doc.role == 'main' else '补充材料'}"
              for index, doc in enumerate(documents, 1)}
    options = [None, *labels]
    document = st.selectbox(
        "引文文件", options, index=options.index(current[0].id) if current else 0,
        format_func=lambda identity: labels.get(identity, "暂不关联引文"), key=f"{key}_document",
    )
    if document is None:
        return None, ""
    blocks = {block.id: block for block in checklist.repo.blocks(document)}
    labels = {identity: f"{index}. 第 {block.page} 页 · {block.text[:65]}"
              if block.page else f"{index}. 文本 · {block.text[:65]}"
              for index, (identity, block) in enumerate(blocks.items(), 1)}
    if not blocks:
        st.caption("该文件暂无可选原文块。")
        return None, ""
    ids = [None, *blocks]
    old = current[1].id if current and current[0].id == document else None
    selected = st.selectbox("原文块", ids, index=ids.index(old),
                            format_func=lambda item: labels.get(item, "选择原文块"),
                            key=f"{key}_{document}_block")
    if selected is None:
        return None, ""
    with st.expander("查看原文块"):
        st.text(blocks[selected].text)
    quote = st.text_area("原文引文", step.quote if old == selected else "", max_chars=10000,
                         help="从上方原文块复制与步骤相关的文字。",
                         key=f"{key}_{selected}_quote")
    return selected, quote
