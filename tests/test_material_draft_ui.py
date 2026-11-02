"""Real Streamlit callbacks exercise disk drafts and explicit version-safe recovery."""

from uuid import uuid4

import pytest
from streamlit.testing.v1 import AppTest

from rag_workbench.application.materials import MaterialsService
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.materials import MaterialRecord
from rag_workbench.infrastructure.material_draft_store import MaterialDraftStore


def _fixture(settings):
    service = MaterialsService(settings)
    document = service.ingest("paper.txt", b"Alpha mobility 0.12 0.13 0.14.")
    record = service.add_record(MaterialRecord(
        id=uuid4().hex, document_id=document.id, material_name="Alpha",
        property_name="mobility", value_raw="0.12",
    ))
    return service, document, record, MaterialDraftStore(service.repo.root)


def _app(service, store, record_id, monkeypatch):
    from rag_workbench.ui import material_recovery as recovery

    monkeypatch.setattr(recovery, "_test_service", service, raising=False)
    monkeypatch.setattr(recovery, "_test_store", store, raising=False)
    monkeypatch.setattr(recovery, "_test_record", record_id, raising=False)
    return AppTest.from_string(
        "import streamlit as st\n"
        "from rag_workbench.ui import material_drafts as drafts\n"
        "from rag_workbench.ui import material_recovery as recovery\n"
        "service = recovery._test_service\n"
        "drafts.configure(recovery._test_store)\n"
        "drafts.collect_resets()\n"
        "recovery.render_recovery(service, recovery._test_store)\n"
        "record = service.repo.record(recovery._test_record)\n"
        "prefix = f'materials_edit_{record.id}_{record.revision}'\n"
        "drafts.bind(prefix, document_id=record.document_id, kind='review', "
        "record_id=record.id, base_revision=record.revision)\n"
        "drafts.hydrate(prefix, {'value_raw': record.value_raw})\n"
        "st.text_input('原始数值', key=prefix+'_value_raw', "
        "on_change=drafts.remember, args=(prefix, 'value_raw'))\n"
        "drafts.status(prefix)\n"
        "st.button('放弃此草稿', key='discard', on_click=drafts.discard, args=(prefix,))\n"
        "if st.button('保存记录', key='commit'):\n"
        "    updated = record.model_copy(update={"
        "'value_raw': st.session_state[prefix+'_value_raw']})\n"
        "    service.review(record.id, updated, status='candidate')\n"
        "    st.session_state['materials_reset_draft'] = prefix\n"
        "    st.rerun()\n",
        default_timeout=30,
    ).run()


def test_fresh_session_requires_explicit_restore_and_does_not_save_record(settings, monkeypatch):
    service, document, record, store = _fixture(settings)
    prefix = f"materials_edit_{record.id}_1"
    first = _app(service, store, record.id, monkeypatch)
    first.text_input[0].set_value("0.13").run()
    assert not first.exception
    assert store.get(prefix).values == {"value_raw": "0.13"}
    assert any("已保存" in item.value for item in first.caption)

    reopened = _app(service, store, record.id, monkeypatch)
    assert reopened.text_input[0].value == "0.12"
    reopened.button(key="materials_restore_draft").click().run()
    assert not reopened.exception
    assert reopened.text_input[0].value == "0.13"
    assert service.repo.record(record.id).value_raw == "0.12"
    assert reopened.session_state["materials_document_focus"] == document.id
    assert reopened.session_state["materials_record_focus"][document.id] == record.id


def test_old_revision_draft_is_preview_only(settings, monkeypatch):
    service, _, record, store = _fixture(settings)
    first = _app(service, store, record.id, monkeypatch)
    first.text_input[0].set_value("0.13").run()
    service.review(record.id, record.model_copy(update={"value_raw": "0.14"}), status="candidate")
    reopened = _app(service, store, record.id, monkeypatch)
    assert not reopened.exception
    assert reopened.button(key="materials_restore_draft").disabled
    assert reopened.text_input[0].value == "0.14"
    assert any("修订" in item.value for item in reopened.warning)
    assert store.list()[0].values == {"value_raw": "0.13"}


def test_failed_autosave_retains_input_and_reports_memory_only(settings, monkeypatch):
    service, _, record, store = _fixture(settings)
    app = _app(service, store, record.id, monkeypatch)

    def fail(*args, **kwargs):
        raise AppError("material_draft_storage_error", "草稿磁盘写入失败测试")

    monkeypatch.setattr(store, "save", fail)
    app.text_input[0].set_value("0.13").run()
    assert not app.exception
    assert app.text_input[0].value == "0.13"
    assert any("未保存到磁盘" in item.value for item in app.warning)
    assert store.list() == []
    assert service.repo.record(record.id).value_raw == "0.12"


def test_discard_deletes_owned_disk_draft_and_resets_widget(settings, monkeypatch):
    service, _, record, store = _fixture(settings)
    app = _app(service, store, record.id, monkeypatch)
    app.text_input[0].set_value("0.13").run()
    app.button(key="discard").click().run()
    assert not app.exception
    assert app.text_input[0].value == "0.12"
    assert store.list() == []


def test_second_window_cannot_overwrite_or_discard_first_window_draft(settings, monkeypatch):
    service, _, record, store = _fixture(settings)
    first = _app(service, store, record.id, monkeypatch)
    second = _app(service, store, record.id, monkeypatch)
    first.text_input[0].set_value("0.13").run()
    second.text_input[0].set_value("0.14").run()
    assert not second.exception
    assert second.text_input[0].value == "0.14"
    assert store.list()[0].values == {"value_raw": "0.13"}
    assert any("未保存到磁盘" in item.value for item in second.warning)
    assert second.button(key="materials_restore_draft").disabled
    second.checkbox(key="materials_recovery_replace_confirm").check().run()
    second.button(key="materials_restore_draft").click().run()
    assert not second.exception
    assert second.text_input[0].value == "0.13"
    second.text_input[0].set_value("0.14").run()
    assert store.list()[0].version == 2
    first.button(key="discard").click().run()
    assert first.text_input[0].value == "0.13"
    assert store.list()[0].values == {"value_raw": "0.14"}
    assert any("未丢弃" in item.value for item in first.warning)


def test_cleanup_failure_does_not_turn_committed_record_into_failure(settings, monkeypatch):
    service, _, record, store = _fixture(settings)
    app = _app(service, store, record.id, monkeypatch)
    app.text_input[0].set_value("0.13").run()

    def fail(*args, **kwargs):
        raise AppError("material_draft_storage_error", "草稿清理失败测试")

    monkeypatch.setattr(store, "delete", fail)
    app.button(key="commit").click().run()
    assert not app.exception
    assert service.repo.record(record.id).value_raw == "0.13"
    assert service.repo.record(record.id).revision == 2
    assert any("记录已保存" in item.value and "草稿" in item.value for item in app.warning)
    assert store.list()[0].values == {"value_raw": "0.13"}


def test_configure_without_store_keeps_session_editor_working(settings, monkeypatch):
    service, _, record, _ = _fixture(settings)
    app = _app(service, None, record.id, monkeypatch)
    app.text_input[0].set_value("0.13").run()
    assert not app.exception
    assert app.text_input[0].value == "0.13"
    assert any("当前会话" in item.value for item in app.caption)


def test_workspace_reconfigure_never_leaks_same_prefix_draft(settings, monkeypatch):
    from rag_workbench.ui import material_recovery as recovery

    service, _, record, store = _fixture(settings)
    app = _app(service, store, record.id, monkeypatch)
    app.text_input[0].set_value("0.13").run()
    other_store = MaterialDraftStore(settings.data_dir / "other-materials")
    monkeypatch.setattr(recovery, "_test_store", other_store)
    app.run()
    assert not app.exception
    assert app.text_input[0].value == "0.12"
    assert store.list()[0].values == {"value_raw": "0.13"}
    assert other_store.list() == []


def test_restore_checks_presented_version_before_taking_ownership(settings, monkeypatch):
    service, document, record, store = _fixture(settings)
    prefix = f"materials_edit_{record.id}_1"
    first = _app(service, store, record.id, monkeypatch)
    first.text_input[0].set_value("0.13").run()
    reopened = _app(service, store, record.id, monkeypatch)
    store.save(
        prefix, {"value_raw": "0.14"}, document_id=document.id, kind="review",
        record_id=record.id, base_revision=1, expected_version=1,
    )
    reopened.button(key="materials_restore_draft").click().run()
    assert not reopened.exception
    assert reopened.text_input[0].value == "0.12"
    assert any("草稿已变化" in item.value for item in reopened.warning)
    assert reopened.session_state.get("materials_draft_versions", {}).get(prefix) is None
    assert store.get(prefix).values == {"value_raw": "0.14"}


def test_restore_confirmation_is_revoked_by_further_editing(settings, monkeypatch):
    service, _, record, store = _fixture(settings)
    first = _app(service, store, record.id, monkeypatch)
    second = _app(service, store, record.id, monkeypatch)
    first.text_input[0].set_value("0.13").run()
    second.text_input[0].set_value("0.14").run()
    second.checkbox(key="materials_recovery_replace_confirm").check().run()
    assert not second.button(key="materials_restore_draft").disabled
    second.text_input[0].set_value("0.15").run()
    assert not second.exception
    assert second.button(key="materials_restore_draft").disabled
    assert second.text_input[0].value == "0.15"


@pytest.mark.parametrize("kind", ["manual", "json"])
def test_manual_and_json_drafts_restore_only_their_own_editors(settings, monkeypatch, kind):
    service, document, record, store = _fixture(settings)
    if kind == "manual":
        prefix = f"materials_new_{document.id}"
        values = {"material_name": "Draft Alpha", "domain": "opv"}
        metadata = {"record_id": None, "base_revision": None}
    else:
        prefix = f"materials_json_{record.id}_1"
        values = {"record": '{"value_raw": "0.13", unfinished'}
        metadata = {"record_id": record.id, "base_revision": 1}
    store.save(prefix, values, document_id=document.id, kind=kind, **metadata)
    app = _app(service, store, record.id, monkeypatch)
    app.button(key="materials_restore_draft").click().run()
    assert not app.exception
    for key, value in values.items():
        assert app.session_state[f"{prefix}_{key}"] == value
    assert app.session_state["materials_drafts"][prefix] == values
    assert app.session_state[f"{prefix}_panel"] is True
    assert app.session_state["materials_tabs"] == "提取与核对"
    assert service.repo.record(record.id).value_raw == "0.12"
    assert service.repo.record(record.id).revision == 1


def test_incompatible_widget_payload_remains_preview_only(settings, monkeypatch):
    service, document, record, store = _fixture(settings)
    prefix = f"materials_edit_{record.id}_1"
    store.save(
        prefix, {"domain": "not-a-domain", "value_raw": 123}, document_id=document.id,
        kind="review", record_id=record.id, base_revision=1,
    )
    app = _app(service, store, record.id, monkeypatch)
    assert not app.exception
    assert app.button(key="materials_restore_draft").disabled
    assert app.text_input[0].value == "0.12"
    assert any("不兼容" in item.value for item in app.warning)


def test_configure_failure_retains_input_and_degrades_to_session_draft(settings, monkeypatch):
    service, _, record, store = _fixture(settings)
    app = _app(service, store, record.id, monkeypatch)
    app.text_input[0].set_value("0.13").run()

    class UnavailableRoot:
        def resolve(self):
            raise OSError("unavailable test root")

    monkeypatch.setattr(store, "root", UnavailableRoot())
    app.run()
    app.text_input[0].set_value("0.14").run()
    assert not app.exception
    assert app.text_input[0].value == "0.14"
    assert any("仅保留在本会话" in item.value for item in app.warning)
    assert store.list()[0].values == {"value_raw": "0.13"}


def test_removing_newer_disk_draft_releases_stale_owner_and_keeps_local_input(
    settings, monkeypatch,
):
    service, _, record, store = _fixture(settings)
    prefix = f"materials_edit_{record.id}_1"
    first = _app(service, store, record.id, monkeypatch)
    first.text_input[0].set_value("0.13").run()
    second = _app(service, store, record.id, monkeypatch)
    second.button(key="materials_restore_draft").click().run()
    second.text_input[0].set_value("0.14").run()
    newer_version = store.get(prefix).version

    first.run()
    first.checkbox(key="materials_recovery_delete_confirm").check().run()
    first.button(key="materials_delete_draft").click().run()
    assert not first.exception
    assert first.text_input[0].value == "0.13"
    assert store.get(prefix) is None
    assert prefix not in first.session_state.get("materials_draft_versions", {})
    assert any("未保存到磁盘" in item.value for item in first.caption)

    first.text_input[0].set_value("0.15").run()
    assert not first.exception
    assert store.get(prefix).values == {"value_raw": "0.15"}
    assert store.get(prefix).version > newer_version
