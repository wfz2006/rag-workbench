"""Automatic conversation titles refresh the rename field without losing drafts."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from rag_workbench.bootstrap import build_services
from rag_workbench.domain.models import AnswerResult, Inspection

from .fakes import FakeEmbedder, FakeProvider

ROOT = Path(__file__).resolve().parents[1]


def _app(settings, monkeypatch):
    from rag_workbench.ui import app as ui_app

    services = build_services(settings, embedder=FakeEmbedder(), provider=FakeProvider())
    monkeypatch.setattr(ui_app, "load_settings", lambda: settings)
    monkeypatch.setattr(ui_app, "resources", lambda *args: services)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not app.exception
    return app, services, app.session_state["session_id"]


def _result(question, *, failed=False):
    return AnswerResult(
        answer="测试回答。",
        mode="error" if failed else "general",
        error_code="missing_api_key" if failed else None,
        debug=Inspection(question=question),
    )


def test_first_automatic_title_updates_untouched_rename_field(settings, monkeypatch):
    app, services, session_id = _app(settings, monkeypatch)
    assert app.text_input(key=f"rename_{session_id}").value == "新会话"
    services.repo.save_turn(session_id, "新的会话主题", _result("新的会话主题"))

    app.run()

    assert not app.exception
    assert app.text_input(key=f"rename_{session_id}").value == "新的会话主题"


def test_first_failed_turn_replacement_refreshes_rename_field(settings, monkeypatch):
    app, services, session_id = _app(settings, monkeypatch)
    services.repo.save_turn(
        session_id, "第一次失败的问题", _result("第一次失败的问题", failed=True)
    )
    app.run()
    assistant_id = services.repo.messages(session_id)[-1]["id"]
    services.repo.replace_failed_turn(
        session_id, assistant_id, "重新编辑后的问题", _result("重新编辑后的问题")
    )

    app.run()

    assert not app.exception
    assert app.text_input(key=f"rename_{session_id}").value == "重新编辑后的问题"


def test_automatic_title_change_preserves_custom_rename_draft(settings, monkeypatch):
    app, services, session_id = _app(settings, monkeypatch)
    app.text_input(key=f"rename_{session_id}").set_value("尚未保存的命名草稿").run()
    services.repo.save_turn(session_id, "后台生成的会话主题", _result("后台生成的会话主题"))

    app.run()

    assert not app.exception
    assert app.text_input(key=f"rename_{session_id}").value == "尚未保存的命名草稿"
    assert any("当前名称：后台生成的会话主题" in item.value for item in app.caption)
    next(button for button in app.button if button.label == "重命名会话").click().run()
    assert not app.exception
    assert services.repo.sessions()[0]["title"] == "尚未保存的命名草稿"
