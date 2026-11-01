"""Local literature records, explicit extraction consent, and evidence-aware review."""

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import streamlit as st
from pydantic import ValidationError

from rag_workbench.application.materials import MaterialsService
from rag_workbench.bootstrap import Services
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.materials import MaterialRecord

STATUS_LABELS = {"candidate": "待审核", "approved": "已批准", "rejected": "已拒绝"}
DOMAIN_LABELS = {
    "ofet": "有机场效应晶体管（OFET）",
    "opv": "有机光伏（OPV）",
    "general": "通用字段",
}
FIELD_LABELS = {
    "material_name": "材料名称",
    "sample_label": "样品编号",
    "property_name": "性能名称",
    "value_raw": "原始数值",
    "unit_raw": "原始单位",
    "material_structure": "材料结构",
    "device_structure": "器件结构",
    "process": "制备工艺",
    "conditions": "测试条件",
    "measurement_method": "测试方法",
    "carrier": "载流子类型",
    "regime": "工作区间",
    "statistic": "统计口径",
}
IMMUTABLE_FIELDS = {"id", "document_id", "revision", "created_at", "updated_at", "status"}


def _import_uploads(service, uploads, role: str, parent_id: str | None, parser_name: str):
    documents, errors = [], []
    for upload in uploads:
        try:
            documents.append(
                service.ingest(
                    upload.name,
                    upload.getvalue(),
                    role=role,
                    parent_id=parent_id,
                    parser_name=parser_name,
                )
            )
        except AppError as exc:
            errors.append(f"{upload.name}：{exc}")
        except OSError:
            errors.append(f"{upload.name}：文件读取失败，请检查原件和磁盘权限。")
    return documents, errors


def _workspace_state(root: Path) -> None:
    identity = str(root.resolve())
    if st.session_state.get("materials_workspace") != identity:
        for key in list(st.session_state):
            if key.startswith("materials_"):
                st.session_state.pop(key, None)
        st.session_state["materials_workspace"] = identity


def _flash(message: str, *, error=False) -> None:
    st.session_state["materials_notice"] = (message, error)


def _business_payload(record: MaterialRecord) -> dict:
    return {
        key: value
        for key, value in record.model_dump(mode="json").items()
        if key not in IMMUTABLE_FIELDS
    }


def _payload(text: str) -> dict:
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("记录必须是 JSON 对象。")
    if IMMUTABLE_FIELDS & value.keys():
        raise ValueError("请只编辑业务字段，记录身份、状态和版本由系统管理。")
    return value


def _error(exc) -> None:
    if isinstance(exc, AppError):
        st.error(str(exc))
    elif isinstance(exc, ValidationError):
        st.error("字段格式无效：请检查必填名称、数值文本和 evidence 证据列表。")
    else:
        st.error("记录 JSON 无效：请使用对象格式，检查引号、逗号和字段名称。")


def _show_sources(service, document) -> list:
    related = [document] + [
        item for item in service.repo.documents() if item.parent_id == document.id
    ]
    blocks = [block for item in related for block in service.repo.blocks(item.id)]
    names = {item.id: item.name for item in related}
    st.caption("证据使用解析后的原文块编号；页码为 PDF 页序。文字文档没有 PDF 页码。")
    if not blocks:
        st.info("当前文献还没有可查看的原文块。")
        return []
    if st.session_state.get("materials_source_block") not in {block.id for block in blocks}:
        st.session_state.pop("materials_source_block", None)
    selected = st.selectbox(
        "查看原文块",
        [block.id for block in blocks],
        key="materials_source_block",
        format_func=lambda identity: next(
            f"{names.get(b.document_id, '文献')} · "
            f"{'第 ' + str(b.page) + ' 页' if b.page else '文本'} · {b.id[:10]}"
            for b in blocks
            if b.id == identity
        ),
    )
    block = next(item for item in blocks if item.id == selected)
    st.text(block.text)
    st.caption(f"原文块编号：{block.id}")
    with st.expander("复制字段证据模板"):
        fields = st.multiselect(
            "这段原文支持哪些字段",
            list(FIELD_LABELS),
            format_func=FIELD_LABELS.get,
            key="materials_evidence_fields",
            default=["value_raw"],
        )
        st.caption(
            "一段原文可支持多个字段。复制模板到记录的 evidence 列表，quote 须保留原文。"
            "模板最多截取 10000 字符，可改为更精确的原文短句。"
        )
        st.code(
            json.dumps(
                [
                    {"field": field, "block_id": block.id, "quote": block.text[:10000]}
                    for field in fields
                ],
                ensure_ascii=False,
                indent=2,
            ),
            language="json",
        )
    return blocks


def _record_evidence(service, record, blocks) -> None:
    source_map = {block.id: block for block in blocks}
    with st.expander(f"查看 {len(record.evidence)} 条字段证据", expanded=bool(record.evidence)):
        if not record.evidence:
            st.caption("尚无字段证据；批准前需补充材料、性能值及各非空字段的原文依据。")
        for reference in record.evidence:
            block = source_map.get(reference.block_id)
            page = f"PDF 第 {block.page} 页" if block and block.page else "文本原文"
            st.caption(f"{FIELD_LABELS.get(reference.field, reference.field)} · {page}")
            st.text(reference.quote)
            if block is None:
                st.warning("该证据块不在当前论文及其补充材料中，请重新关联。")
            else:
                source = service.repo.document(block.document_id)
                st.caption(
                    f"来源：{source.name if source else block.document_id} · {block.id[:12]}"
                )


def _record_form_fields(defaults: dict, prefix: str) -> dict:
    """All widgets live in their caller's form: checking confirmation never reruns editing."""
    values = {}
    left, right = st.columns(2)
    for column, field in (
        (left, "material_name"),
        (right, "property_name"),
        (left, "value_raw"),
        (right, "unit_raw"),
        (left, "sample_label"),
    ):
        value = column.text_input(
            FIELD_LABELS[field], value=defaults.get(field) or "", key=f"{prefix}_{field}"
        )
        values[field] = (
            value if field in {"material_name", "property_name", "value_raw"} else value or None
        )
    values["domain"] = right.selectbox(
        "器件模板",
        list(DOMAIN_LABELS),
        format_func=DOMAIN_LABELS.get,
        index=list(DOMAIN_LABELS).index(defaults.get("domain", "ofet")),
        key=f"{prefix}_domain",
    )
    with st.expander("补充工艺、测试条件与结构", expanded=False):
        for field in (
            "process",
            "conditions",
            "measurement_method",
            "material_structure",
            "device_structure",
        ):
            values[field] = (
                st.text_area(
                    FIELD_LABELS[field],
                    defaults.get(field) or "",
                    height=90,
                    key=f"{prefix}_{field}",
                )
                or None
            )
        for field in ("carrier", "regime", "statistic"):
            values[field] = (
                st.text_input(
                    FIELD_LABELS[field], defaults.get(field) or "", key=f"{prefix}_{field}"
                )
                or None
            )
    raw_evidence = st.text_area(
        "字段证据（JSON 列表）",
        json.dumps(defaults.get("evidence", []), ensure_ascii=False, indent=2),
        height=170,
        key=f"{prefix}_evidence",
        help="在“原文证据”页复制模板。一段原文可支持多个字段，quote 必须与原文一致。",
    )
    values["evidence"] = raw_evidence
    return values


def _form_record(values: dict, original: dict) -> MaterialRecord:
    payload = {**original, **values, "evidence": json.loads(values["evidence"])}
    return MaterialRecord.model_validate(payload)


def _review(service, document, blocks) -> None:
    records = service.repo.records(document.id)
    st.subheader("实验记录核对")
    st.caption("候选未经确认，不会进入默认导出。不同样品、工艺或测试条件应保留为不同记录。")
    if records:
        identities = [item.id for item in records]
        if st.session_state.get("materials_selected_record") not in identities:
            st.session_state["materials_selected_record"] = identities[0]
        selected = st.selectbox(
            "选择实验记录",
            identities,
            key="materials_selected_record",
            format_func=lambda identity: next(
                f"{index + 1} · {r.material_name} · {r.property_name} · "
                f"{r.value_raw} {r.unit_raw or ''} · {STATUS_LABELS[r.status]}"
                for index, r in enumerate(records)
                if r.id == identity
            ),
        )
        record = next(item for item in records if item.id == selected)
        st.caption(f"{STATUS_LABELS[record.status]} · 修订 {record.revision} · {record.id[:12]}")
        _record_evidence(service, record, blocks)
        focus = (record.id, record.revision)
        if st.session_state.get("materials_review_focus") != focus:
            st.session_state.pop("materials_review_confirm", None)
            st.session_state["materials_review_focus"] = focus
        with st.form(f"materials_review_{record.id}_{record.revision}"):
            values = _record_form_fields(
                _business_payload(record), f"materials_edit_{record.id}_{record.revision}"
            )
            confirmed = st.checkbox(
                "我已逐项核对数值、单位、样品、条件与字段证据", key="materials_review_confirm"
            )
            col1, col2, col3 = st.columns(3)
            save = col1.form_submit_button("保存候选", key="materials_save_candidate")
            approve = col2.form_submit_button("批准记录", key="materials_approve", type="primary")
            reject = col3.form_submit_button("拒绝记录", key="materials_reject")
            if save or approve or reject:
                try:
                    if approve and not confirmed:
                        raise AppError("material_review_unconfirmed", "请先勾选逐项核对确认。")
                    updated = _form_record(values, record.model_dump())
                    status = "approved" if approve else "rejected" if reject else "candidate"
                    service.review(record.id, updated, status=status)
                    _flash(f"记录{STATUS_LABELS[status]}，已保存修订。")
                    st.rerun()
                except (AppError, ValueError, ValidationError) as exc:
                    _error(exc)
        with st.expander("高级：完整字段 JSON 编辑"):
            st.caption("JSON 编辑保存为候选，仍需在上方逐项核对后批准。")
            with st.form(f"materials_json_review_{record.id}_{record.revision}"):
                edited = st.text_area(
                    "记录字段（JSON）",
                    json.dumps(_business_payload(record), ensure_ascii=False, indent=2),
                    height=300,
                    key=f"materials_record_json_{record.id}_{record.revision}",
                )
                if st.form_submit_button("保存 JSON 候选", key="materials_save_json"):
                    try:
                        updated = MaterialRecord.model_validate(
                            {**record.model_dump(), **_payload(edited)}
                        )
                        service.review(record.id, updated, status="candidate")
                        _flash("JSON 候选已保存，请核对后批准。")
                        st.rerun()
                    except (AppError, ValueError, ValidationError) as exc:
                        _error(exc)
        revisions = service.repo.revisions(record.id)
        if revisions:
            with st.expander(f"修订历史（{len(revisions)}）"):
                st.json(revisions, expanded=False)
    else:
        st.info("还没有实验记录。可提取候选，也可根据原文手工添加。")
    _manual_record(service, document)


def _manual_record(service, document) -> None:
    with st.expander("手工添加候选（无需 API）"):
        st.caption("先在“原文证据”页复制证据模板。以下只提供空字段，不填入虚构实验数据。")
        with st.form(f"materials_add_{document.id}"):
            values = _record_form_fields({"domain": "ofet"}, f"materials_new_{document.id}")
            if st.form_submit_button("保存新候选", key="materials_add_candidate"):
                try:
                    record = _form_record(values, {"id": uuid4().hex, "document_id": document.id})
                    service.add_record(record)
                    _flash("已添加候选，请核对字段证据后批准。")
                    st.rerun()
                except (AppError, ValueError, ValidationError) as exc:
                    _error(exc)


def _extraction(service, services, document) -> None:
    st.subheader("提取实验候选")
    st.caption(
        "正文提取包括当前论文及关联补充材料；单独选择补充材料时仅处理该文件。"
        "完整解析文本按批次处理，"
        "不是聊天检索的 Top-K 片段。图片和曲线不会由文本路径自动读数。"
    )
    try:
        scope = service.extraction_scope(document.id)
        st.caption(
            f"本次发送范围：{scope['documents']} 份文献 · {scope['blocks']} 个原文块 · "
            f"{scope['characters']:,} 字符 · {scope['batches']} 次模型请求（不含重试）。"
        )
    except AppError as exc:
        st.error(str(exc))
        return
    st.selectbox(
        "指标模板", list(DOMAIN_LABELS), format_func=DOMAIN_LABELS.get, key="materials_domain"
    )
    has_key = bool(services.settings.api_key.get_secret_value().strip())
    if not has_key:
        st.caption("尚未配置 API Key；仍可导入原文、手工添加、审核及导出记录。")
    st.caption(f"文字提取模型：{services.settings.llm_model}")
    confirmed = st.checkbox(
        "我同意将上述文献文字发送到当前模型接口，可能产生 API 费用",
        key="materials_extract_consent",
        disabled=not has_key,
    )
    if st.button(
        "提取候选", key="materials_extract", type="primary", disabled=not has_key or not confirmed
    ):
        try:
            with st.spinner("正在逐批提取，结果仍需人工核对…"):
                run = service.extract(document.id, domain=st.session_state["materials_domain"])
            if run.status == "completed":
                _flash(f"已完成提取，生成 {run.record_count} 条候选，请核对后批准。")
            else:
                _flash("提取未完成，请查看任务记录；已保存的候选仍可核对。", error=True)
            st.rerun()
        except AppError as exc:
            st.error(str(exc))
    runs = service.repo.runs(document.id)
    if runs:
        with st.expander("提取任务记录"):
            st.dataframe(
                [
                    {
                        "状态": {"running": "处理中", "completed": "已完成", "failed": "失败"}[
                            r.status
                        ],
                        "模型": r.model,
                        "已处理批次": f"{r.batch_completed}/{r.batch_total}",
                        "候选数": r.record_count,
                        "错误码": r.error_code or "—",
                    }
                    for r in runs
                ],
                hide_index=True,
                width="stretch",
            )


def _exports(service, document) -> None:
    st.subheader("导出材料数据")
    approved_only = st.checkbox("仅导出已批准记录", value=True, key="materials_approved_only")
    if not approved_only:
        st.warning("当前下载包含未审核或已拒绝记录，仅用于整理，不应直接作为训练真值。")
    st.caption("CSV 一行一项性能观测；完整包保留嵌套字段、字段证据与原文定位。")
    records = service.repo.records(document.id)
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "document": document.id,
                "approved_only": approved_only,
                "records": sorted((r.id, r.revision, r.status) for r in records),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    prepared = st.session_state.get("materials_prepared_export")
    if prepared and prepared["fingerprint"] != fingerprint:
        st.session_state.pop("materials_prepared_export", None)
        prepared = None
    if st.button("准备导出文件", key="materials_prepare_export"):
        try:
            with st.spinner("正在校验证据与原件，准备导出文件…"):
                prepared = {
                    "fingerprint": fingerprint,
                    "csv": service.export_csv(document.id, approved_only=approved_only),
                    "bundle": service.export_bundle(document.id, approved_only=approved_only),
                }
            st.session_state["materials_prepared_export"] = prepared
        except AppError as exc:
            st.error(str(exc))
    if prepared:
        st.download_button(
            "下载 CSV",
            prepared["csv"],
            file_name="measurements.csv",
            mime="text/csv",
            key="materials_download_csv",
        )
        st.download_button(
            "下载完整数据包",
            prepared["bundle"],
            file_name="materials-dataset.zip",
            mime="application/zip",
            key="materials_download_bundle",
        )
    else:
        st.caption("点击准备后下载。修改记录、切换文献或审核筛选后，需要重新准备。")


def materials_page(services: Services) -> None:
    _workspace_state(services.settings.data_dir)
    service = MaterialsService(services.settings, services.pipeline.provider)
    with st.container(key="materials_page"):
        st.title("材料文献")
        st.caption("有机半导体 / 论文、样品、工艺、测试与性能的原文关联")
        if notice := st.session_state.pop("materials_notice", None):
            (st.error if notice[1] else st.success)(notice[0])
        documents = service.repo.documents()
        with st.expander("导入论文与补充材料", expanded=not documents):
            uploads = st.file_uploader(
                "选择 PDF、TXT 或 Markdown",
                type=["pdf", "txt", "md"],
                accept_multiple_files=True,
                key="materials_upload",
            )
            role = st.radio(
                "文件角色",
                ["main", "supplement"],
                horizontal=True,
                format_func=lambda value: "论文正文" if value == "main" else "补充材料",
                key="materials_upload_role",
            )
            parents = [doc for doc in documents if doc.role == "main"]
            parent_id = None
            if role == "supplement":
                if parents:
                    parent_id = st.selectbox(
                        "所属论文",
                        [doc.id for doc in parents],
                        key="materials_parent",
                        format_func=lambda identity: next(
                            d.name for d in parents if d.id == identity
                        ),
                    )
                else:
                    st.info("请先导入论文正文，再关联补充材料。")
            parser_name = st.selectbox(
                "解析方式",
                ["text", "docling"],
                format_func=lambda value: (
                    "文本层解析（PDF / TXT / MD）"
                    if value == "text"
                    else "Docling 结构化解析（可选组件）"
                ),
                key="materials_parser",
            )
            st.caption(
                "文本层解析不读取扫描图片；Docling 需另行安装。导入只保存在本地，不调用模型。"
            )
            if st.button(
                "解析并保存原文",
                key="materials_import",
                disabled=not uploads or role == "supplement" and parent_id is None,
            ):
                imported, errors = _import_uploads(service, uploads, role, parent_id, parser_name)
                for message in errors:
                    st.error(message)
                if imported:
                    st.session_state["materials_selected_doc"] = imported[-1].id
                    _flash(f"已保存 {len(imported)} 份文献原文。")
                    if not errors:
                        st.rerun()
                    documents = service.repo.documents()
        if not documents:
            st.info("先上传一篇论文，保留原文后再提取或手工整理实验记录。")
            return
        identities = [doc.id for doc in documents]
        if st.session_state.get("materials_selected_doc") not in identities:
            st.session_state["materials_selected_doc"] = identities[0]
        selected = st.selectbox(
            "当前文献",
            identities,
            key="materials_selected_doc",
            format_func=lambda identity: next(
                f"{d.name} · {'正文' if d.role == 'main' else '补充材料'}"
                for d in documents
                if d.id == identity
            ),
        )
        document = service.repo.document(selected)
        records = service.repo.records(document.id)
        st.caption(
            f"{len(documents)} 份文献 · {len(records)} 条实验记录 · "
            f"{sum(r.status == 'approved' for r in records)} 条已批准"
        )
        review_tab, source_tab, export_tab = st.tabs(
            ["提取与核对", "原文证据", "导出"], key="materials_tabs", on_change="rerun",
        )
        with source_tab:
            blocks = _show_sources(service, document)
        with review_tab:
            _extraction(service, services, document)
            st.divider()
            _review(service, document, blocks)
        with export_tab:
            _exports(service, document)
