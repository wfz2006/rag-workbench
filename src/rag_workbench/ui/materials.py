"""Local literature records, explicit extraction consent, and evidence-aware review."""

import hashlib
import json
from pathlib import Path

import streamlit as st
from pydantic import ValidationError

from rag_workbench.application.materials import MaterialsService
from rag_workbench.bootstrap import Services
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.material_terms import MATERIAL_TERMS
from rag_workbench.domain.materials import STRUCTURED_FIELDS, MaterialRecord
from rag_workbench.infrastructure.material_draft_store import MaterialDraftStore
from rag_workbench.ui import material_drafts as drafts
from rag_workbench.ui.digitized_curves import render_digitized_curves
from rag_workbench.ui.material_analysis import render_material_analysis
from rag_workbench.ui.material_evidence import evidence_editor
from rag_workbench.ui.material_recovery import AVAILABILITY_PREFIX, render_recovery
from rag_workbench.ui.material_viewer import render_source_viewer
from rag_workbench.ui.reproduction import render_reproduction

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
    "anneal_temperature": "退火温度",
    "anneal_duration": "退火时间",
    "deposition_method": "成膜方式",
    "electrode": "电极",
    "dielectric": "介电层",
    "channel_length": "沟道长度",
    "channel_width": "沟道宽度",
    "measurement_environment": "测量环境",
    "measurement_temperature": "测量温度",
    "film_thickness": "膜厚",
    "observation_origin": "观测来源",
}
AVAILABILITY_LABELS = {
    "": "无标记",
    "not_extracted": "尚未提取",
    "not_reported": "原文未报告",
    "not_applicable": "不适用",
}
ORIGIN_LABELS = {
    "": "未核对",
    "measured": "论文报告的实测值",
    "calculated": "计算值",
    "digitized": "图中读数",
    "cited": "引用的其他研究结果",
}
TERM_HELP = {
    "carrier": "选 hole（空穴）或 electron（电子），也可照抄其他原文用词。",
    "regime": "选 saturation（饱和区）或 linear（线性区），也可照抄其他原文用词。",
    "statistic": (
        "选 max（最大值，如 highest）、mean（平均值，如 avg）、min（最小值）或 typical（典型值）。"
        "原文仅说 obtained 或 up to 时，请勿据此选 max。"
    ),
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


def _remember_document() -> None:
    st.session_state["materials_document_focus"] = st.session_state["materials_selected_doc"]
    st.session_state.pop("materials_source_focus", None)
    st.session_state["materials_source_reset"] = True


def _remember_record(document_id: str) -> None:
    st.session_state.setdefault("materials_record_focus", {})[document_id] = (
        st.session_state["materials_selected_record"]
    )


def _select_record(document_id: str) -> None:
    _remember_record(document_id)
    st.session_state.pop("materials_source_focus", None)
    st.session_state["materials_source_reset"] = True


def _locate_evidence(block, quote: str) -> None:
    st.session_state["materials_source_focus"] = {
        "document_id": block.document_id, "block_id": block.id, "quote": quote,
    }
    st.session_state["materials_review_view"] = "source"


def _next_candidate(document_id: str, identity: str) -> None:
    st.session_state[f"materials_filter_{document_id}"] = "candidate"
    st.session_state["materials_selected_record"] = identity
    _select_record(document_id)
    st.session_state["materials_review_view"] = "record"


def _business_payload(record: MaterialRecord) -> dict:
    return {
        key: value
        for key, value in record.model_dump(mode="json").items()
        if key not in IMMUTABLE_FIELDS
    }


def _manual_identity(record: MaterialRecord) -> str:
    payload = _business_payload(record)
    for field in ("value_normalized", "unit_normalized"):
        payload.pop(field, None)
    for field in (*STRUCTURED_FIELDS, "field_availability", "observation_origin"):
        if not payload[field]:
            payload.pop(field)
    payload["evidence"] = sorted(
        payload["evidence"], key=lambda item: json.dumps(item, sort_keys=True),
    )
    return hashlib.sha256(json.dumps(
        {"document_id": record.document_id, **payload}, sort_keys=True, ensure_ascii=False,
    ).encode()).hexdigest()


def _save_manual_candidate(service, record):
    record = record.model_copy(update={"id": _manual_identity(record)})
    existing = service.repo.record(record.id)
    if existing is None:
        try:
            saved = service.add_record(record)
            if _manual_identity(saved) != record.id:
                raise AppError(
                    "material_candidate_revised",
                    "候选已被另一窗口修订；未清理当前草稿，请核对现有记录。",
                )
            return saved, saved.created_at == record.created_at
        except AppError as exc:
            if exc.code != "material_record_exists":
                raise
            existing = service.repo.record(record.id)
    if existing is None or _manual_identity(existing) != record.id:
        raise AppError(
            "material_candidate_revised",
            "这份候选此前已保存并被修订；未重复新增，草稿仍保留，请先核对现有记录。",
        )
    return existing, False


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
    with st.expander("复制字段证据模板", key="materials_source_template", on_change="rerun"):
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
    with st.expander(f"查看 {len(record.evidence)} 条字段证据"):
        if not record.evidence:
            st.caption("尚无字段证据；批准前需补充材料、性能值及各非空字段的原文依据。")
        groups = {}
        for index, reference in enumerate(record.evidence):
            groups.setdefault((reference.block_id, reference.quote), []).append((index, reference))
        for entries in groups.values():
            index, reference = entries[0]
            block = source_map.get(reference.block_id)
            page = f"PDF 第 {block.page} 页" if block and block.page else "文本原文"
            labels = dict.fromkeys(FIELD_LABELS.get(item.field, item.field) for _, item in entries)
            st.caption(f"{'、'.join(labels)} · {page}")
            st.text(reference.quote)
            if block is None:
                st.warning("该证据块不在当前论文及其补充材料中，请重新关联。")
            else:
                source = service.repo.document(block.document_id)
                st.caption(
                    f"来源：{source.name if source else block.document_id} · {block.id[:12]}"
                )
                st.button(
                    "在原文中查看", key=f"materials_locate_{record.id}_{index}",
                    on_click=_locate_evidence, args=(block, reference.quote),
                )


def _editor_defaults(defaults: dict) -> dict:
    availability = defaults.get("field_availability", {})
    return {
        **{field: defaults.get(field) or "" for field in FIELD_LABELS},
        **{
            f"{AVAILABILITY_PREFIX}{field}": availability.get(field, "")
            for field in STRUCTURED_FIELDS
        },
        "domain": defaults.get("domain", "ofet"),
        "evidence": json.dumps(defaults.get("evidence", []), ensure_ascii=False, indent=2),
    }


def _record_form_fields(defaults: dict, prefix: str, blocks: list) -> dict:
    """Commit on blur to a session draft, then explicitly save to the repository."""
    initial = _editor_defaults(defaults)
    drafts.hydrate(prefix, initial)
    if drafts.dirty(prefix, initial):
        st.info("有尚未提交为实验记录的修改，可继续编辑、保存或放弃。")
    drafts.status(prefix)
    st.button(
        "放弃此草稿", key=f"{prefix}_discard", on_click=drafts.discard, args=(prefix,),
        disabled=not drafts.dirty(prefix, initial), type="tertiary",
    )
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
            FIELD_LABELS[field], key=f"{prefix}_{field}",
            on_change=drafts.remember, args=(prefix, field),
        )
        values[field] = (
            value if field in {"material_name", "property_name", "value_raw"} else value or None
        )
    values["domain"] = right.selectbox(
        "器件模板",
        list(DOMAIN_LABELS),
        format_func=DOMAIN_LABELS.get,
        key=f"{prefix}_domain",
        on_change=drafts.remember, args=(prefix, "domain"),
    )
    with st.expander(
        "补充工艺、测试条件与结构", key=f"{prefix}_details", on_change="rerun",
    ):
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
                    height=90,
                    key=f"{prefix}_{field}",
                    on_change=drafts.remember, args=(prefix, field),
                )
                or None
            )
        st.caption("选用标准术语或直接输入原文用词，并关联支持此字段的引文。不确定时留空。")
        for field, terms in MATERIAL_TERMS.items():
            key = f"{prefix}_{field}"
            options = ["", *terms]
            current = st.session_state[key]
            if current and current not in options:
                options.append(current)
            values[field] = st.selectbox(
                FIELD_LABELS[field], options, key=key, accept_new_options=True,
                format_func=lambda option: option or "未填写",
                help=TERM_HELP[field], on_change=drafts.remember, args=(prefix, field),
            ) or None
    with st.expander("实验字段与观测来源", key=f"{prefix}_experiment", on_change="rerun"):
        values["observation_origin"] = st.selectbox(
            FIELD_LABELS["observation_origin"], list(ORIGIN_LABELS),
            format_func=ORIGIN_LABELS.get, key=f"{prefix}_observation_origin",
            on_change=drafts.remember, args=(prefix, "observation_origin"),
            help="按原文核对并分类。批准时请关联支持该分类的原文证据。",
        ) or None
        st.caption("按原文填写字段，数量须保留单位。留空时可标记缺失原因。")
        availability = {}
        for field in STRUCTURED_FIELDS:
            value_column, status_column = st.columns([3, 2])
            label = FIELD_LABELS[field]
            values[field] = value_column.text_input(
                label, key=f"{prefix}_{field}", max_chars=1000,
                on_change=drafts.remember, args=(prefix, field),
            ) or None
            status_field = f"{AVAILABILITY_PREFIX}{field}"
            status = status_column.selectbox(
                f"{label}缺失原因", list(AVAILABILITY_LABELS),
                format_func=AVAILABILITY_LABELS.get, key=f"{prefix}_{status_field}",
                on_change=drafts.remember, args=(prefix, status_field),
            )
            if status:
                availability[field] = status
                if values[field]:
                    st.caption(f"{label}已有值，请清除缺失标记。")
        values["field_availability"] = availability
    evidence_editor(prefix, blocks, FIELD_LABELS)
    with st.expander("高级：字段证据 JSON", key=f"{prefix}_evidence_panel", on_change="rerun"):
        raw_evidence = st.text_area(
            "字段证据（JSON 列表）", height=170, key=f"{prefix}_evidence",
            help="一段引文可支持多个字段，quote 必须与原文一致。",
            on_change=drafts.remember, args=(prefix, "evidence"),
        )
    values["evidence"] = raw_evidence
    return values


def _form_record(values: dict, original: dict) -> MaterialRecord:
    payload = {**original, **values, "evidence": json.loads(values["evidence"])}
    return MaterialRecord.model_validate(payload)


def _review(service, document, blocks) -> None:
    records = service.repo.records(document.id)
    pending = [record for record in records if record.status == "candidate"]
    st.subheader("实验记录核对")
    st.caption("候选未经确认，不会进入默认导出。不同样品、工艺或测试条件应保留为不同记录。")
    if records:
        status = st.selectbox(
            "审核筛选", ["all", *STATUS_LABELS], key=f"materials_filter_{document.id}",
            format_func=lambda value: "全部记录" if value == "all" else STATUS_LABELS[value],
        )
        total = len(records)
        records = [record for record in records if status == "all" or record.status == status]
        st.caption(f"显示 {len(records)} / {total} 条记录；修改字段后需点击保存或批准。")
    if records:
        identities = [item.id for item in records]
        if (
            st.session_state.get("materials_selected_record") not in identities
            or st.session_state.get("materials_record_document") != document.id
        ):
            remembered = st.session_state.get("materials_record_focus", {}).get(document.id)
            st.session_state["materials_selected_record"] = (
                remembered if remembered in identities else identities[0]
            )
        st.session_state["materials_record_document"] = document.id
        selected = st.selectbox(
            "选择实验记录",
            identities,
            key="materials_selected_record",
            on_change=_select_record, args=(document.id,),
            format_func=lambda identity: next(
                f"{index + 1} · {r.material_name} · {r.property_name} · "
                f"{r.value_raw} {r.unit_raw or ''} · {STATUS_LABELS[r.status]}"
                for index, r in enumerate(records)
                if r.id == identity
            ),
        )
        record = next(item for item in records if item.id == selected)
        _remember_record(document.id)
        st.caption(f"{STATUS_LABELS[record.status]} · 修订 {record.revision} · {record.id[:12]}")
        pending_ids = [item.id for item in pending]
        if record.id in pending_ids:
            following = (
                pending_ids[(pending_ids.index(record.id) + 1) % len(pending_ids)]
                if len(pending_ids) > 1 else None
            )
        else:
            following = pending_ids[0] if pending_ids else None
        st.button(
            "下一条待审核", key="materials_next_candidate", disabled=following is None,
            on_click=_next_candidate, args=(document.id, following), type="tertiary",
        )
        _record_evidence(service, record, blocks)
        focus = (record.id, record.revision)
        if st.session_state.get("materials_review_focus") != focus:
            st.session_state.pop("materials_review_confirm", None)
            st.session_state["materials_review_focus"] = focus
        prefix = f"materials_edit_{record.id}_{record.revision}"
        json_prefix = f"materials_json_{record.id}_{record.revision}"
        old_drafts = {
            key: value for key, value in st.session_state.get("materials_drafts", {}).items()
            if key.startswith((f"materials_edit_{record.id}_", f"materials_json_{record.id}_"))
            and key not in {prefix, json_prefix}
        }
        if old_drafts:
            st.warning("该记录已有新修订，旧版本草稿未自动覆盖新记录。可展开查看并手工合并。")
            with st.expander("查看旧版本草稿"):
                st.json(old_drafts)
        with st.container(border=True):
            drafts.bind(
                prefix, document_id=document.id, kind="review",
                record_id=record.id, base_revision=record.revision,
            )
            values = _record_form_fields(
                _business_payload(record), prefix, blocks,
            )
            try:
                references = json.loads(values["evidence"])
                covered = {
                    item.get("field") for item in references if isinstance(item, dict)
                } if isinstance(references, list) else set()
                missing = [
                    FIELD_LABELS[field] for field in FIELD_LABELS
                    if values.get(field) and field not in covered
                ]
                if missing:
                    st.caption("尚未关联字段证据：" + "、".join(missing))
            except (ValueError, TypeError):
                pass  # Incomplete evidence JSON is validated only on an explicit submit.
            confirmed = st.checkbox(
                "我已逐项核对数值、单位、样品、条件与字段证据", key="materials_review_confirm"
            )
            col1, col2, col3 = st.columns(3)
            save = col1.button("保存候选", key="materials_save_candidate")
            approve = col2.button("批准记录", key="materials_approve", type="primary")
            reject = col3.button("拒绝记录", key="materials_reject")
            if save or approve or reject:
                try:
                    if approve and not confirmed:
                        raise AppError("material_review_unconfirmed", "请先勾选逐项核对确认。")
                    updated = _form_record(values, record.model_dump())
                    status = "approved" if approve else "rejected" if reject else "candidate"
                    service.review(record.id, updated, status=status)
                    st.session_state["materials_reset_draft"] = prefix
                    _flash(f"记录{STATUS_LABELS[status]}，已保存修订。")
                    st.rerun()
                except (AppError, ValueError, ValidationError) as exc:
                    _error(exc)
        with st.expander(
            "高级：完整字段 JSON 编辑", key=f"{json_prefix}_panel", on_change="rerun",
        ):
            st.caption("JSON 编辑保存为候选，仍需在上方逐项核对后批准。")
            json_defaults = {
                "record": json.dumps(_business_payload(record), ensure_ascii=False, indent=2),
            }
            drafts.bind(
                json_prefix, document_id=document.id, kind="json",
                record_id=record.id, base_revision=record.revision,
            )
            drafts.hydrate(json_prefix, json_defaults)
            drafts.status(json_prefix)
            if drafts.dirty(json_prefix, json_defaults):
                st.info("完整 JSON 有未保存的修改；它与上方字段草稿分别保存，请选择一处提交。")
            st.button(
                "放弃 JSON 草稿", key=f"{json_prefix}_discard", type="tertiary",
                disabled=not drafts.dirty(json_prefix, json_defaults),
                on_click=drafts.discard, args=(json_prefix,),
            )
            with st.container():
                edited = st.text_area(
                    "记录字段（JSON）",
                    height=300,
                    key=f"{json_prefix}_record",
                    on_change=drafts.remember, args=(json_prefix, "record"),
                )
                if st.button("保存 JSON 候选", key="materials_save_json"):
                    try:
                        updated = MaterialRecord.model_validate(
                            {**record.model_dump(), **_payload(edited)}
                        )
                        service.review(record.id, updated, status="candidate")
                        st.session_state["materials_reset_draft"] = json_prefix
                        _flash("JSON 候选已保存，请核对后批准。")
                        st.rerun()
                    except (AppError, ValueError, ValidationError) as exc:
                        _error(exc)
        revisions = service.repo.revisions(record.id)
        if revisions:
            with st.expander(f"修订历史（{len(revisions)}）"):
                st.json(revisions, expanded=False)
    else:
        st.info("当前没有可核对的记录。可调整审核筛选、提取候选或根据原文手工添加。")
    _manual_record(service, document, blocks)


def _manual_record(service, document, blocks) -> None:
    prefix = f"materials_new_{document.id}"
    with st.expander("手工添加候选（无需 API）", key=f"{prefix}_panel", on_change="rerun"):
        st.caption("填写原文字段，可直接关联证据。保存成功后清空表单，继续添加下一条。")
        with st.container(border=True):
            drafts.bind(prefix, document_id=document.id, kind="manual")
            values = _record_form_fields({"domain": "ofet"}, prefix, blocks)
            if st.button("保存新候选", key="materials_add_candidate"):
                try:
                    record = _form_record(values, {"id": "manual", "document_id": document.id})
                    record, created = _save_manual_candidate(service, record)
                    st.session_state["materials_reset_draft"] = prefix
                    st.session_state["materials_pending_record"] = record.id
                    st.session_state["materials_pending_record_document"] = document.id
                    _flash(
                        "已添加候选，请核对字段证据后批准。" if created
                        else "这份候选已保存，已定位到现有记录；未重复新增或改变审核状态。"
                    )
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
    domain = st.selectbox(
        "指标模板", list(DOMAIN_LABELS), format_func=DOMAIN_LABELS.get, key="materials_domain"
    )
    actual = getattr(service.provider, "settings", services.settings)
    key = actual.api_key.get_secret_value().strip()
    has_key = bool(key)
    if not has_key:
        st.caption("尚未配置 API Key；仍可导入原文、手工添加、审核及导出记录。")
    st.caption(f"文字提取模型：{actual.llm_model}")
    scope_ids = sorted(
        item.id for item in service.repo.documents()
        if item.id == document.id or item.parent_id == document.id
    )
    consent_scope = hashlib.sha256(json.dumps({
        "documents": scope_ids, "domain": domain, "model": actual.llm_model,
        "endpoint": actual.llm_base_url,
        "credential": hashlib.sha256(key.encode()).hexdigest(),
    }, sort_keys=True).encode()).hexdigest()
    if st.session_state.get("materials_consent_scope") != consent_scope:
        st.session_state["materials_extract_consent"] = False
        st.session_state["materials_consent_scope"] = consent_scope
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
                message = f"已保存 {run.record_count} 条待核对候选。"
                if run.skipped_count:
                    message += f"另有 {run.skipped_count} 条未通过校验，未保存。"
                _flash(message)
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
                        "未通过校验": r.skipped_count,
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
    st.caption(
        "CSV 的规范值列只填写可换算的单值。范围、带误差的数值和单位不明的记录保留原文；"
        "已批准的范围与误差可在「跨论文分析」查看和导出。"
    )
    # Use the records behind the count and fingerprint for both downloads.
    records = tuple(service.repo.records(document.id))
    count = sum(record.status == "approved" or not approved_only for record in records)
    st.caption(f"本次导出：{document.name} · {count} 条实验记录。")
    if not count:
        st.info("当前筛选下没有记录；导出只含表头和原文资料。可先审核记录或调整筛选。")
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
        st.session_state.pop("materials_prepared_export", None)
        prepared = None
        try:
            with st.spinner("正在校验证据与原件，准备导出文件…"):
                options = {"approved_only": approved_only, "record_snapshot": records}
                prepared = {
                    "fingerprint": fingerprint,
                    "csv": service.export_csv(document.id, **options),
                }
                try:
                    prepared["bundle"] = service.export_bundle(document.id, **options)
                except AppError as exc:
                    prepared["bundle_error"] = f"完整数据包未能生成：{exc}。CSV 仍可下载。"
            st.session_state["materials_prepared_export"] = prepared
        except AppError as exc:
            st.error(str(exc))
    if prepared:
        if prepared.get("bundle_error"):
            st.error(prepared["bundle_error"])
        st.download_button(
            "下载 CSV",
            prepared["csv"],
            file_name="measurements.csv",
            mime="text/csv",
            key="materials_download_csv",
        )
        if prepared.get("bundle"):
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
    try:
        draft_store = MaterialDraftStore(service.repo.root)
    except AppError as exc:
        draft_store = None
        st.warning(f"本地草稿存储暂不可用，当前会话仍可编辑：{exc}")
    drafts.configure(draft_store)
    drafts.collect_resets()
    if selected_record := st.session_state.pop("materials_pending_record", None):
        st.session_state["materials_selected_record"] = selected_record
        if identity := st.session_state.pop("materials_pending_record_document", None):
            st.session_state[f"materials_filter_{identity}"] = "all"
            st.session_state.setdefault("materials_record_focus", {})[identity] = selected_record
    with st.container(key="materials_page"):
        st.title("材料文献")
        st.caption("有机半导体 / 论文、样品、工艺、测试与性能的原文关联")
        if notice := st.session_state.pop("materials_notice", None):
            (st.error if notice[1] else st.success)(notice[0])
            st.toast(notice[0], icon=":material/error:" if notice[1] else ":material/check_circle:")
        documents = service.repo.documents()
        with st.expander(
            "导入论文与补充材料", expanded=not documents,
            key="materials_import_panel", on_change="rerun",
        ):
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
                    st.session_state["materials_document_focus"] = imported[-1].id
                    _flash(f"已保存 {len(imported)} 份文献原文。")
                    if not errors:
                        st.rerun()
                    documents = service.repo.documents()
        render_recovery(service, draft_store)
        if not documents:
            st.info("先上传一篇论文，保留原文后再提取或手工整理实验记录。")
            return
        document_picker = st.container()
        review_tab, source_tab, export_tab, analysis_tab, reproduction_tab, curves_tab = st.tabs(
            ["提取与核对", "原文证据", "导出", "跨论文分析", "实验复现清单", "论文曲线取数"],
            key="materials_tabs", on_change="rerun",
        )
        if analysis_tab.open:
            with analysis_tab:
                render_material_analysis(service, FIELD_LABELS)
            return
        if reproduction_tab.open:
            with reproduction_tab:
                render_reproduction(service, documents)
            return
        if curves_tab.open:
            with curves_tab:
                render_digitized_curves(service, documents)
            return
        identities = [doc.id for doc in documents]
        if st.session_state.get("materials_selected_doc") not in identities:
            remembered = st.session_state.get("materials_document_focus")
            st.session_state["materials_selected_doc"] = (
                remembered if remembered in identities else identities[0]
            )
        selected = document_picker.selectbox(
            "当前文献",
            identities,
            key="materials_selected_doc",
            on_change=_remember_document,
            format_func=lambda identity: next(
                f"{d.name} · {'正文' if d.role == 'main' else '补充材料'}"
                for d in documents
                if d.id == identity
            ),
        )
        document = service.repo.document(selected)
        records = service.repo.records(document.id)
        document_picker.caption(
            f"{len(documents)} 份文献 · {len(records)} 条实验记录 · "
            f"{sum(r.status == 'approved' for r in records)} 条已批准"
        )
        with source_tab:
            blocks = _show_sources(service, document)
        with review_tab:
            st.radio(
                "核对视图", ["record", "source"], key="materials_review_view", horizontal=True,
                format_func=lambda value: "编辑记录" if value == "record" else "原文预览",
            )
            with st.expander(
                "模型提取候选", expanded=not records,
                key=f"materials_extract_panel_{document.id}", on_change="rerun",
            ):
                _extraction(service, services, document)
            with st.container(key="materials_review_grid"):
                source_column, record_column = st.columns([1, 1.05], gap="large")
                with source_column, st.container(key="materials_source_column"):
                    render_source_viewer(service, document, blocks)
                with record_column, st.container(key="materials_record_column"):
                    _review(service, document, blocks)
        with export_tab:
            _exports(service, document)
