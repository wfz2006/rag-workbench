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
    for page in ("知识库管理", "知识包", "系统设置", "检索调试", "AI 聊天"):
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


def test_pack_page_lists_and_activates_existing_pack(tmp_path, monkeypatch):
    from rag_workbench.application.packs import PackBuilder, PackSpec
    from rag_workbench.bootstrap import build_services
    from rag_workbench.config import Settings
    from rag_workbench.ui import app as ui_app

    from .fakes import FakeEmbedder, FakeProvider

    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "active",
        packs_dir=tmp_path / "packs",
        packs_data_dir=tmp_path / "data",
        chunk_size=100,
        chunk_overlap=10,
    )
    PackBuilder(settings, FakeEmbedder()).build(
        PackSpec(
            pack_id="demo-pack",
            title="示例知识包",
            license_status="internal_only",
            chunk_size=100,
            chunk_overlap=10,
            similarity_threshold=0.0,
        ),
        [("a.txt", "资料正文。".encode())],
    )
    services = build_services(settings, embedder=FakeEmbedder(), provider=FakeProvider())
    monkeypatch.setenv("RAG_PACKS_DIR", str(settings.packs_dir))
    monkeypatch.setenv("RAG_PACKS_DATA_DIR", str(settings.packs_data_dir))
    monkeypatch.setattr(ui_app, "load_settings", lambda: settings)
    monkeypatch.setattr(ui_app, "resources", lambda *args: services)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.sidebar.radio[0].set_value("知识包").run()
    assert not app.exception
    labels = [button.label for button in app.button]
    assert "用 LLM 起草 profile" in labels
    assert "用 LLM 起草评测题" in labels
    assert "冻结评测集" not in labels
    app.button(key="switch_existing_pack").click().run()
    assert not app.exception
    assert app.session_state["active_pack"] == "demo-pack"
    assert app.session_state["workspace_page"] == "AI 聊天"


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


def test_sidebar_css_covers_all_five_navigation_icons():
    from rag_workbench.ui.shell import PAGES

    assert len(PAGES) == 6
    css = (ROOT / "src" / "rag_workbench" / "ui" / "assets" / "workbench.css").read_text(
        encoding="utf-8"
    )
    for idx in range(1, len(PAGES)):
        assert f'input[value="{idx}"]' in css


def test_pack_builder_form_immediately_refreshes_sidebar_pack_selector(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from rag_workbench.bootstrap import build_services
    from rag_workbench.config import Settings
    from rag_workbench.ui import app as ui_app
    from rag_workbench.ui import pages as ui_pages

    from .fakes import FakeEmbedder, FakeProvider

    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "active",
        packs_dir=tmp_path / "packs",
        packs_data_dir=tmp_path / "data",
        chunk_size=100,
        chunk_overlap=10,
    )
    services = build_services(settings, embedder=FakeEmbedder(), provider=FakeProvider())
    monkeypatch.setenv("RAG_PACKS_DIR", str(settings.packs_dir))
    monkeypatch.setenv("RAG_PACKS_DATA_DIR", str(settings.packs_data_dir))
    monkeypatch.setattr(ui_app, "load_settings", lambda: settings)
    monkeypatch.setattr(ui_app, "resources", lambda *args: services)
    monkeypatch.setattr(
        ui_pages.st,
        "file_uploader",
        lambda *args, **kwargs: [
            SimpleNamespace(name="doc.txt", getvalue=lambda: "内容。".encode())
        ],
    )
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.sidebar.radio[0].set_value("知识包").run()
    assert not app.exception
    assert len(app.sidebar.selectbox) == 0
    app.text_input(key="pack_id_input").set_value("fresh-pack")
    app.text_input(key="pack_title_input").set_value("新建知识包")
    app.button[0].click().run()
    assert not app.exception
    assert len(app.sidebar.selectbox) == 1
    assert any("新建知识包" in opt for opt in app.sidebar.selectbox[0].options)
    assert app.session_state["pack_manage_select"] == "fresh-pack"


def test_pack_switch_and_return_to_default_workspace_syncs_selector_and_buttons(
    tmp_path, monkeypatch
):
    from rag_workbench.application.packs import PackBuilder, PackSpec
    from rag_workbench.bootstrap import build_services
    from rag_workbench.config import Settings
    from rag_workbench.ui import app as ui_app

    from .fakes import FakeEmbedder, FakeProvider

    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "active",
        packs_dir=tmp_path / "packs",
        packs_data_dir=tmp_path / "data",
        chunk_size=100,
        chunk_overlap=10,
    )
    PackBuilder(settings, FakeEmbedder()).build(
        PackSpec(
            pack_id="demo-pack",
            title="示例知识包",
            license_status="internal_only",
            chunk_size=100,
            chunk_overlap=10,
            similarity_threshold=0.0,
        ),
        [("a.txt", "资料正文。".encode())],
    )
    services = build_services(settings, embedder=FakeEmbedder(), provider=FakeProvider())
    monkeypatch.setenv("RAG_PACKS_DIR", str(settings.packs_dir))
    monkeypatch.setenv("RAG_PACKS_DATA_DIR", str(settings.packs_data_dir))
    monkeypatch.setattr(ui_app, "load_settings", lambda: settings)
    monkeypatch.setattr(ui_app, "resources", lambda *args: services)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.sidebar.radio[0].set_value("知识包").run()
    assert not app.button(key="switch_existing_pack").disabled
    app.button(key="switch_existing_pack").click().run()
    assert app.session_state["active_pack"] == "demo-pack"
    assert app.session_state["active_pack_select"] == "demo-pack"
    app.sidebar.radio[0].set_value("知识包").run()
    assert app.button(key="switch_existing_pack").disabled
    app.button(key="switch_default_workspace").click().run()
    assert app.session_state["active_pack"] is None
    assert app.session_state["active_pack_select"] is None


def test_debug_page_ignores_whitespace_submission_and_preserves_inspection(settings, monkeypatch):
    from rag_workbench.bootstrap import build_services
    from rag_workbench.ui import app as ui_app

    from .fakes import FakeEmbedder, FakeProvider

    services = build_services(settings, embedder=FakeEmbedder(), provider=FakeProvider())
    services.knowledge.ingest("manual.txt", "资料保管期限为三年。".encode())
    monkeypatch.setattr(ui_app, "load_settings", lambda: settings)
    monkeypatch.setattr(ui_app, "resources", lambda *args: services)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.sidebar.radio[0].set_value("检索调试").run()
    app.text_area[0].set_value("保管期限？")
    app.button[0].click().run()
    assert app.session_state["inspection"].question == "保管期限？"
    app.text_area[0].set_value("   ")
    app.button[0].click().run()
    assert any("请输入" in w.value for w in app.warning)
    assert app.session_state["inspection"].question == "保管期限？"
