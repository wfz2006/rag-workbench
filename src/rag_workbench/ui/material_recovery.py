"""Explicit recovery of local drafts; stale record revisions are preview-only."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import streamlit as st

from rag_workbench.domain.errors import AppError
from rag_workbench.infrastructure.material_draft_store import DraftSnapshot, MaterialDraftStore
from rag_workbench.ui import material_drafts as drafts

KINDS = {"review": "记录字段", "manual": "新增候选", "json": "完整 JSON"}
EDITOR_FIELDS = {
    "material_name", "sample_label", "property_name", "value_raw", "unit_raw", "domain",
    "material_structure", "device_structure", "process", "conditions", "measurement_method",
    "carrier", "regime", "statistic", "evidence",
}


def _recovery_issue(service, snapshot: DraftSnapshot) -> str | None:
    document = service.repo.document(snapshot.document_id)
    if document is None:
        return "原文文献已不在当前工作区，草稿仅供查看或移除。"
    if snapshot.kind == "manual":
        canonical = f"materials_new_{snapshot.document_id}"
        if snapshot.record_id is not None or snapshot.base_revision is not None:
            return "新增草稿的身份信息不兼容，请查看旧值后手工整理。"
    elif snapshot.kind in {"review", "json"}:
        record = service.repo.record(snapshot.record_id) if snapshot.record_id else None
        if record is None or record.document_id != snapshot.document_id:
            return "对应实验记录已不存在或文献归属不同，草稿仅供查看或移除。"
        if record.revision != snapshot.base_revision:
            return "基础修订与当前记录不一致，不能直接恢复；请查看旧值并手工合并。"
        part = "edit" if snapshot.kind == "review" else "json"
        canonical = f"materials_{part}_{snapshot.record_id}_{snapshot.base_revision}"
    else:
        return "草稿类型与当前版本不兼容，请查看旧值后手工整理。"
    allowed = {"record"} if snapshot.kind == "json" else EDITOR_FIELDS
    if (
        snapshot.key != canonical or not snapshot.values.keys() <= allowed
        or any(not isinstance(value, str) for value in snapshot.values.values())
        or (
            "domain" in snapshot.values
            and snapshot.values["domain"] not in {"ofet", "opv", "general"}
        )
    ):
        return "草稿字段或身份格式与当前编辑区不兼容，请查看旧值后手工整理。"
    return None


def _notice(message: str, error: bool = False) -> None:
    st.session_state["materials_recovery_notice"] = (message, error)


def _saved_time(value: str) -> str:
    try:
        saved = datetime.fromisoformat(value).astimezone(timezone(timedelta(hours=8)))
        return saved.strftime("%m-%d %H:%M") + "（UTC+08）"
    except ValueError:
        return "保存时间待核对"


def _restore(service, store, snapshot: DraftSnapshot) -> None:
    try:
        latest = store.get(snapshot.key)
        if latest is None or latest.version != snapshot.version:
            raise AppError("material_draft_conflict", "所选草稿已变化，请重新核对后恢复。")
        if issue := _recovery_issue(service, snapshot):
            raise AppError("material_draft_incompatible", issue)
        local = st.session_state.get("materials_drafts", {}).get(snapshot.key)
        if local and local != snapshot.values and not st.session_state.get(
            "materials_recovery_replace_confirm", False,
        ):
            raise AppError("material_draft_unconfirmed", "请先确认替换当前会话的未保存输入。")
        drafts.bind(
            snapshot.key, document_id=snapshot.document_id, kind=snapshot.kind,
            record_id=snapshot.record_id, base_revision=snapshot.base_revision,
        )
        # Clear stale widget values, including fields not present in the disk draft.
        for key in list(st.session_state):
            if key.startswith(snapshot.key + "_"):
                st.session_state.pop(key, None)
        st.session_state.setdefault("materials_drafts", {})[snapshot.key] = deepcopy(
            snapshot.values,
        )
        st.session_state.setdefault("materials_draft_versions", {})[snapshot.key] = snapshot.version
        st.session_state.setdefault("materials_draft_status", {})[snapshot.key] = (
            "saved", "已恢复本地草稿；尚未提交为实验记录。",
        )
        for field, value in snapshot.values.items():
            st.session_state[f"{snapshot.key}_{field}"] = value
        st.session_state["materials_selected_doc"] = snapshot.document_id
        st.session_state["materials_document_focus"] = snapshot.document_id
        if snapshot.record_id:
            st.session_state.setdefault("materials_record_focus", {})[
                snapshot.document_id
            ] = snapshot.record_id
            st.session_state["materials_pending_record"] = snapshot.record_id
            st.session_state["materials_pending_record_document"] = snapshot.document_id
            st.session_state["materials_selected_record"] = snapshot.record_id
            st.session_state[f"materials_filter_{snapshot.document_id}"] = "all"
        st.session_state["materials_review_view"] = "record"
        st.session_state["materials_tabs"] = "提取与核对"
        if snapshot.kind in {"manual", "json"}:
            st.session_state[f"{snapshot.key}_panel"] = True
        st.session_state["materials_review_confirm"] = False
        st.session_state.pop("materials_recovery_replace_confirm", None)
        _notice("本地草稿已恢复，请核对后保存；未自动批准或修改实验记录。")
    except AppError as exc:
        _notice(str(exc), True)


def _delete(store, snapshot: DraftSnapshot) -> None:
    try:
        store.delete(snapshot.key, expected_version=snapshot.version)
        st.session_state.get("materials_draft_versions", {}).pop(snapshot.key, None)
        if snapshot.key in st.session_state.get("materials_drafts", {}):
            st.session_state.setdefault("materials_draft_status", {})[snapshot.key] = (
                "memory", "磁盘草稿已移除；当前输入未保存到磁盘，继续修改后会创建新的本地草稿。",
            )
        _notice("所选磁盘草稿已移除，实验记录未改变。")
    except AppError as exc:
        _notice(str(exc), True)


def render_recovery(service, store: MaterialDraftStore | None) -> None:
    if notice := st.session_state.pop("materials_draft_notice", None):
        (st.warning if notice[1] else st.success)(notice[0])
    if notice := st.session_state.pop("materials_recovery_notice", None):
        (st.warning if notice[1] else st.success)(notice[0])
    if store is None:
        return
    try:
        versions = st.session_state.get("materials_draft_versions", {})
        snapshots = [item for item in store.list() if versions.get(item.key) != item.version]
        if not snapshots:
            return
        documents = {doc.id: doc.name for doc in service.repo.documents()}
        choices = {f"{item.key}:{item.version}": item for item in snapshots}
        if st.session_state.get("materials_recovery_selected") not in choices:
            st.session_state.pop("materials_recovery_selected", None)
        with st.expander(
            f"恢复本地草稿（{len(snapshots)}）", expanded=True,
            key="materials_recovery_panel", on_change="rerun",
        ):
            st.caption("磁盘草稿不会自动加载或批准；选择后先查看，再决定恢复或移除。")
            selected = st.selectbox(
                "选择本地草稿", list(choices), key="materials_recovery_selected",
                format_func=lambda key: (
                    f"{documents.get(choices[key].document_id, '文献已移除')} · "
                    f"{KINDS.get(choices[key].kind, '其他')} · "
                    f"修订 {choices[key].base_revision or '—'} · "
                    f"{_saved_time(choices[key].updated_at)}"
                ),
            )
            snapshot = choices[selected]
            focus = (snapshot.key, snapshot.version)
            if st.session_state.get("materials_recovery_focus") != focus:
                st.session_state.pop("materials_recovery_replace_confirm", None)
                st.session_state.pop("materials_recovery_delete_confirm", None)
                st.session_state["materials_recovery_focus"] = focus
            issue = _recovery_issue(service, snapshot)
            if issue:
                st.warning(issue)
            st.json(snapshot.values, expanded=False)
            local = st.session_state.get("materials_drafts", {}).get(snapshot.key)
            replace_local = bool(local and local != snapshot.values)
            confirmed = not replace_local
            if replace_local:
                st.warning("当前会话还有不同的编辑输入，恢复会替换这一份草稿。")
                confirmed = st.checkbox(
                    "确认用所选磁盘草稿替换当前会话的未保存输入",
                    key="materials_recovery_replace_confirm",
                )
            st.button(
                "恢复所选草稿", key="materials_restore_draft", type="primary",
                disabled=bool(issue) or not confirmed,
                on_click=_restore, args=(service, store, snapshot),
            )
            remove = st.checkbox("确认移除所选磁盘草稿", key="materials_recovery_delete_confirm")
            st.button(
                "移除所选磁盘草稿", key="materials_delete_draft", disabled=not remove,
                on_click=_delete, args=(store, snapshot),
            )
    except AppError as exc:
        st.warning(f"本地草稿恢复区暂不可用，编辑区仍可使用：{exc}")
