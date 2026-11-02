"""Widget-independent editor drafts with optional, revision-checked local autosave."""

import streamlit as st

from rag_workbench.domain.errors import AppError
from rag_workbench.infrastructure.material_draft_store import MaterialDraftStore


def configure(store: MaterialDraftStore | None) -> None:
    """Configure optional persistence without automatically loading any disk draft."""
    if store is not None:
        try:
            identity = str(store.root.resolve())
        except (OSError, RuntimeError, AppError):
            st.session_state["materials_draft_store"] = None
            st.session_state["materials_draft_notice"] = (
                "本地草稿存储暂不可用，当前输入仅保留在本会话；请先保存实验记录。", True,
            )
            return
        previous = st.session_state.get("materials_draft_store_root")
        if previous is not None and previous != identity:
            prefixes = set(st.session_state.get("materials_drafts", {})) | set(
                st.session_state.get("materials_draft_bindings", {})
            )
            for prefix in prefixes:
                _clear_session(prefix)
            for key in ("materials_draft_bindings", "materials_draft_versions",
                        "materials_draft_status", "materials_draft_notice"):
                st.session_state.pop(key, None)
        st.session_state["materials_draft_store_root"] = identity
    st.session_state["materials_draft_store"] = store


def bind(
    prefix: str, *, document_id: str, kind: str, record_id: str | None = None,
    base_revision: int | None = None,
) -> None:
    st.session_state.setdefault("materials_draft_bindings", {})[prefix] = {
        "document_id": document_id, "kind": kind,
        "record_id": record_id, "base_revision": base_revision,
    }


def hydrate(prefix: str, defaults: dict) -> None:
    draft = st.session_state.get("materials_drafts", {}).get(prefix, {})
    for field, default in defaults.items():
        key = f"{prefix}_{field}"
        if key not in st.session_state:
            st.session_state[key] = draft.get(field, default)


def remember(prefix: str, field: str) -> None:
    drafts = st.session_state.setdefault("materials_drafts", {})
    drafts.setdefault(prefix, {})[field] = st.session_state[f"{prefix}_{field}"]
    # A changed field always requires fresh human confirmation.
    st.session_state["materials_review_confirm"] = False
    st.session_state["materials_recovery_replace_confirm"] = False
    store = st.session_state.get("materials_draft_store")
    binding = st.session_state.get("materials_draft_bindings", {}).get(prefix)
    statuses = st.session_state.setdefault("materials_draft_status", {})
    if store is None or binding is None:
        statuses[prefix] = ("memory", "草稿仅保留在当前会话；关闭或刷新前请保存记录。")
        return
    versions = st.session_state.setdefault("materials_draft_versions", {})
    try:
        snapshot = store.save(
            prefix, drafts[prefix], expected_version=versions.get(prefix, 0), **binding,
        )
        versions[prefix] = snapshot.version
        statuses[prefix] = ("saved", "本地草稿已保存，刷新后可在草稿恢复区继续编辑。")
    except AppError as exc:
        statuses[prefix] = (
            "failed", f"草稿未保存到磁盘，当前输入仍保留在本会话：{exc}",
        )


def set_field(prefix: str, field: str, value) -> None:
    st.session_state[f"{prefix}_{field}"] = value
    remember(prefix, field)


def dirty(prefix: str, defaults: dict) -> bool:
    draft = st.session_state.get("materials_drafts", {}).get(prefix, {})
    return any(value != defaults.get(field) for field, value in draft.items())


def status(prefix: str) -> None:
    item = st.session_state.get("materials_draft_status", {}).get(prefix)
    if item:
        (st.warning if item[0] == "failed" else st.caption)(item[1])
    elif st.session_state.get("materials_draft_store") is None:
        st.caption("草稿仅保留在当前会话；关闭或刷新前请保存记录。")
    else:
        st.caption("修改字段并离开输入框后保存本地草稿；批准记录仍需明确操作。")


def _clear_session(prefix: str) -> None:
    st.session_state.get("materials_drafts", {}).pop(prefix, None)
    st.session_state.get("materials_draft_versions", {}).pop(prefix, None)
    st.session_state.get("materials_draft_status", {}).pop(prefix, None)
    for key in list(st.session_state):
        if key.startswith(prefix + "_"):
            st.session_state.pop(key, None)
    st.session_state["materials_review_confirm"] = False


def _delete_owned(prefix: str) -> None:
    version = st.session_state.get("materials_draft_versions", {}).get(prefix)
    if not version:
        return
    store = st.session_state.get("materials_draft_store")
    if store is None:
        raise AppError("material_draft_unavailable", "本地草稿存储暂不可用，请稍后重试。")
    store.delete(prefix, expected_version=version)


def discard(prefix: str) -> None:
    try:
        _delete_owned(prefix)
    except AppError as exc:
        st.session_state.setdefault("materials_draft_status", {})[prefix] = (
            "failed", f"草稿未丢弃，当前输入仍保留：{exc}",
        )
        return
    _clear_session(prefix)


def collect_resets() -> None:
    if prefix := st.session_state.pop("materials_reset_draft", None):
        try:
            _delete_owned(prefix)
        except AppError as exc:
            st.session_state["materials_draft_notice"] = (
                f"记录已保存，但本地草稿未能清理；磁盘中的草稿仍保留，请核对后移除：{exc}",
                True,
            )
        # A successful record commit remains successful even if draft cleanup failed.
        _clear_session(prefix)
