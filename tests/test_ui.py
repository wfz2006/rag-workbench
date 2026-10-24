from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]


def finish_turn(app):
    for job in list(app.session_state["answer_jobs"].values()):
        job.wait()
        assert job.snapshot().done
    return app.run()


def test_all_pages_render_with_empty_kb_and_no_key(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path / "ui-kb"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not app.exception
    for page in ("知识库管理", "系统设置", "检索调试", "AI 聊天"):
        app.sidebar.radio[0].set_value(page).run()
        assert not app.exception, page
    app.chat_input[0].set_value("请介绍一下你自己").run()
    finish_turn(app)
    assert not app.exception
    assert any("DEEPSEEK_API_KEY" in error.value for error in app.error)


def test_chat_sources_and_new_session_work_with_backend_fakes(settings, monkeypatch):
    from rag_workbench.bootstrap import build_services
    from rag_workbench.ui import app as ui_app

    from .fakes import FakeEmbedder, FakeProvider

    services = build_services(settings, embedder=FakeEmbedder(), provider=FakeProvider())
    doc = services.knowledge.ingest("manual.txt", "资料保管期限为三年。".encode())
    monkeypatch.setattr(ui_app, "load_settings", lambda: settings)
    monkeypatch.setattr(ui_app, "resources", lambda *args: services)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.chat_input[0].set_value("保管期限？").run()
    finish_turn(app)
    assert not app.exception
    assert any("引用来源" in element.label for element in app.expander)
    services.knowledge.delete(doc.id)
    app.run()
    assert any("引用来源" in element.label for element in app.expander)
    app.sidebar.button[0].click().run()
    assert not app.exception
    assert len(app.chat_message) == 0


def test_sidebar_search_filters_real_sessions(settings, monkeypatch):
    from rag_workbench.bootstrap import build_services
    from rag_workbench.ui import app as ui_app

    from .fakes import FakeEmbedder, FakeProvider

    services = build_services(settings, embedder=FakeEmbedder(), provider=FakeProvider())
    first = services.repo.new_session()
    services.ask(first, "保管期限的讨论")
    second = services.repo.new_session()
    services.ask(second, "另一个话题")
    monkeypatch.setattr(ui_app, "load_settings", lambda: settings)
    monkeypatch.setattr(ui_app, "resources", lambda *args: services)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.sidebar.text_input[0].set_value("保管").run()
    assert not app.exception
    labels = [button.label for button in app.sidebar.button]
    assert "保管期限的讨论" in labels
    assert "另一个话题" not in labels
    app.sidebar.button(key=f"session_{first}").click().run()
    assert app.session_state["session_id"] == first


def test_pending_turn_can_stop_and_new_session_is_independent(settings, monkeypatch):
    from threading import Event

    from rag_workbench.bootstrap import build_services
    from rag_workbench.ui import app as ui_app

    from .fakes import FakeEmbedder, FakeProvider

    gate, entered = Event(), Event()

    class SlowProvider(FakeProvider):
        def stream(self, system, user, **kwargs):
            yield "真实增量"
            entered.set()
            gate.wait(5)
            yield "后续增量"

    services = build_services(settings, embedder=FakeEmbedder(), provider=SlowProvider())
    monkeypatch.setattr(ui_app, "load_settings", lambda: settings)
    monkeypatch.setattr(ui_app, "resources", lambda *args: services)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    first = app.session_state["session_id"]
    try:
        app.chat_input[0].set_value("继续回答").run()
        assert entered.wait(2)
        assert not app.exception
        assert app.button(key="stop_generation")
        assert app.chat_input[0].disabled
        job = app.session_state["answer_jobs"][first]
        app.button(key="stop_generation").click().run()
        app.sidebar.button[0].click().run()
        assert app.session_state["session_id"] != first
        assert first in app.session_state["answer_jobs"]
        gate.set()
        job.wait()
        assert services.repo.messages(first)[-1]["result"]
        assert job.snapshot().result.error_code == "cancelled"
        assert len(app.chat_message) == 0
    finally:
        gate.set()
