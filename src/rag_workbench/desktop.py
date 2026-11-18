"""Windows desktop shell. The backend and UI remain ordinary, testable Python modules."""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from filelock import FileLock, Timeout

ASSETS = Path(__file__).parent / "ui" / "assets"


class DesktopError(RuntimeError):
    """Only locally authored, non-sensitive messages may be shown verbatim."""


def prepare_profile(bundle: Path, profile: Path) -> None:
    """Seed missing public defaults; never replace user configuration or existing data."""
    profile.mkdir(parents=True, exist_ok=True)
    env_file = profile / ".env"
    if not env_file.exists():
        with env_file.open("x", encoding="utf-8") as target:
            target.write(
                "# Desktop profile. Restart the app after editing. Never share this file.\n"
                "DEEPSEEK_API_KEY=\nRAG_DATA_DIR=knowledge\nRAG_MODEL_CACHE=models\n"
            )
    config = profile / ".streamlit" / "config.toml"
    if not config.exists():
        config.parent.mkdir(exist_ok=True)
        shutil.copyfile(bundle / "resources" / "config.toml", config)
    model = "models--BAAI--bge-small-zh-v1.5"
    source_models, profile_models = bundle / "models", profile / "models"
    seed = source_models / model
    if (
        stat.S_ISDIR(_seed_entry_mode(source_models) or 0)
        and stat.S_ISDIR(_seed_entry_mode(seed) or 0)
        and stat.S_ISDIR(_seed_entry_mode(profile) or 0)
        and _ensure_seed_directory(profile_models)
    ):
        _seed_missing_model_files(seed, profile_models / model)


def _seed_entry_mode(path: Path) -> int | None:
    try:
        entry = path.lstat()
    except FileNotFoundError:
        return None
    if getattr(entry, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        return 0  # An occupied name, but never a regular file or directory to traverse.
    return entry.st_mode


def _ensure_seed_directory(path: Path) -> bool:
    with contextlib.suppress(FileExistsError):
        path.mkdir()
    return stat.S_ISDIR(_seed_entry_mode(path) or 0)


def _seed_missing_model_files(seed: Path, destination: Path) -> None:
    if not _ensure_seed_directory(destination):
        return
    for source in seed.iterdir():
        target = destination / source.name
        mode = _seed_entry_mode(source) or 0
        if stat.S_ISDIR(mode):
            _seed_missing_model_files(source, target)
        elif stat.S_ISREG(mode) and _seed_entry_mode(target) is None:
            _publish_seed_file(source, target)


def _publish_seed_file(source: Path, target: Path) -> None:
    # Stage on the same volume. A stopped or failed copy never bears the cache filename.
    staging = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=".model-seed-", suffix=".copying", delete=False
        ) as temporary:
            staging = Path(temporary.name)
            with source.open("rb") as file:
                shutil.copyfileobj(file, temporary)
        with contextlib.suppress(FileExistsError):
            if os.name == "nt":
                staging.rename(target)  # Windows rename refuses to replace an existing name.
            else:
                os.link(staging, target)  # POSIX rename would replace an existing file.
    finally:
        if staging is not None:
            staging.unlink(missing_ok=True)


def server_environment() -> dict[str, str]:
    """A desktop profile must not silently use the developer shell's KB or API key."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("RAG_", "STREAMLIT_", "PYTHON"))
        and key.upper() != "DEEPSEEK_API_KEY"
    }
    env.update(
        STREAMLIT_BROWSER_GATHER_USAGE_STATS="false",
        STREAMLIT_SERVER_HEADLESS="true",
        STREAMLIT_SERVER_ADDRESS="127.0.0.1",
        STREAMLIT_SERVER_FILE_WATCHER_TYPE="none",
        TOKENIZERS_PARALLELISM="false",
    )
    return env


def available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def listener_owned_by(port: int, pid: int) -> bool:
    if sys.platform != "win32":
        return True
    api = ctypes.WinDLL("iphlpapi").GetExtendedTcpTable
    size = ctypes.c_ulong()
    if api(None, ctypes.byref(size), False, 2, 3, 0) not in (0, 122):
        return False
    for _ in range(5):
        buffer = ctypes.create_string_buffer(max(size.value, 4))
        status = api(buffer, ctypes.byref(size), False, 2, 3, 0)
        if status == 0:
            break
        if status != 122:
            return False
    else:
        return False
    count = ctypes.c_uint32.from_buffer(buffer).value
    if 4 + count * 24 > len(buffer):
        return False
    for row in range(count):
        values = (ctypes.c_uint32 * 6).from_buffer(buffer, 4 + row * 24)
        if values[0] == 2 and values[5] == pid and socket.ntohs(values[2] & 0xFFFF) == port:
            return True
    return False


def wait_for_server(process: subprocess.Popen, port: int, timeout: float = 45) -> None:
    deadline = time.monotonic() + timeout
    # Do not send localhost readiness probes through a user's HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise DesktopError("本地服务启动失败，请查看工作空间中的 desktop.log。")
        try:
            with opener.open(f"http://127.0.0.1:{port}/_stcore/health", timeout=0.5) as response:
                if response.read() == b"ok" and listener_owned_by(port, process.pid):
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.1)
    raise TimeoutError("本地服务启动超时，请查看 user-data/desktop.log。")


def stop_server(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _message(message: str) -> None:
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(None, message, "RAG Workbench", 0x10)
    else:
        print(message, file=sys.stderr)


def open_external_program(command: list[str]) -> subprocess.Popen:
    """Editors belong to the user, not to the disposable backend process job."""
    return subprocess.Popen(
        command, creationflags=getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
    )


def open_config_file(path: Path) -> None:
    try:
        open_external_program(["notepad.exe", str(path)])
    except OSError:
        # Plain Popen would put the unsaved editor back in the kill-on-close job.
        _message(
            "当前启动器限制了外部编辑器的独立启动，未打开记事本。"
            "请在资源管理器中手动打开以下文件，修改后重启应用：\n" + str(path)
        )


def start_server(bundle: Path, profile: Path, log) -> tuple[subprocess.Popen, int]:
    for attempt in range(3):
        port = available_port()
        process = subprocess.Popen(
            [
                str(Path(sys.executable).with_name("python.exe")),
                "-I",
                "-B",
                "-m",
                "streamlit",
                "run",
                str(bundle / "resources/app.py"),
                "--server.port",
                str(port),
            ],
            cwd=profile,
            env=server_environment(),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            wait_for_server(process, port)
            return process, port
        except (DesktopError, TimeoutError):
            stop_server(process)
            if attempt == 2:
                raise
    raise DesktopError("本地服务未能启动。")


def run(bundle: Path, profile: Path, *, smoke: bool = False, smoke_external: bool = False) -> int:
    import webview
    from webview.menu import Menu, MenuAction, MenuSeparator

    profile.mkdir(parents=True, exist_ok=True)
    # Prevent two independent service processes from sharing the same SQLite/index workspace.
    lock = FileLock(profile / "desktop.lock", timeout=0)
    try:
        lock.acquire()
    except Timeout:
        raise DesktopError("此工作空间已在另一个窗口中打开，请先关闭原窗口。") from None
    process = None
    session_file = profile / "desktop-session.json"
    try:
        prepare_profile(bundle, profile)
        with (profile / "desktop.log").open("w", encoding="utf-8") as log:
            process, port = start_server(bundle, profile, log)
            if smoke and smoke_external:
                external = open_external_program(
                    [str(Path(sys.executable)), "-I", "-B", "-c", "import time; time.sleep(90)"]
                )
                (profile / "smoke-external.json").write_text(json.dumps({"pid": external.pid}))
            url = f"http://127.0.0.1:{port}"
            session_file.write_text(
                json.dumps({"pid": process.pid, "shell_pid": os.getpid(), "url": url}),
                encoding="utf-8",
            )
            webview.settings["ALLOW_DOWNLOADS"] = True
            webview.settings["ALLOW_FILE_URLS"] = False
            menu = [
                Menu(
                    "工作空间",
                    [
                        MenuAction(
                            "配置 API Key（打开 .env）",
                            lambda: open_config_file(profile / ".env"),
                        ),
                        MenuAction("打开数据文件夹", lambda: os.startfile(profile)),
                        MenuAction("刷新界面", lambda: window.load_url(url)),
                        MenuSeparator(),
                        MenuAction("退出", lambda: window.destroy()),
                    ],
                )
            ]
            window = webview.create_window(
                "RAG Workbench",
                url,
                width=1360,
                height=900,
                min_size=(720, 520),
                background_color="#141414",
                text_select=True,
                zoomable=True,
                menu=menu,
                hidden=smoke,
            )
            result: dict = {}

            def check_window() -> None:
                # Explicit acceptance mode only, never used by the normal desktop experience.
                try:
                    deadline = time.monotonic() + 60
                    while time.monotonic() < deadline:
                        state = window.evaluate_js(
                            "JSON.stringify({ready:!!document.querySelector('textarea'),"
                            "errors:document.querySelectorAll('[data-testid=stException]').length,"
                            "title:document.title})"
                        )
                        if state:
                            result.update(json.loads(state))
                            if result.get("ready"):
                                break
                        time.sleep(0.2)
                    if not result.get("ready"):
                        raise TimeoutError("Desktop UI did not become ready.")
                    result["pages"] = []
                    pages = (
                        "知识库管理", "知识包", "系统设置", "检索调试",
                        "材料文献", "知识星图", "AI 聊天",
                    )
                    for page in pages:
                        page_json = json.dumps(page)
                        clicked = window.evaluate_js(
                            "(()=>{const label=[...document.querySelectorAll("
                            "'[data-testid=stSidebar] label')].find(e=>e.textContent.trim()==="
                            + page_json
                            + ");if(!label)return false;label.click();return true})()"
                        )
                        if not clicked:
                            raise RuntimeError("Desktop page navigation is unavailable.")
                        page_deadline = time.monotonic() + 20
                        ready = False
                        while time.monotonic() < page_deadline:
                            ready = window.evaluate_js(
                                "document.querySelector('.interaction-scope')?.dataset.page==="
                                + page_json
                                + " && !document.querySelector('[data-testid=stException]')"
                            )
                            if ready:
                                break
                            time.sleep(0.2)
                        if not ready:
                            raise TimeoutError("Desktop page did not become ready.")
                        result["pages"].append(page)
                    result["passed"] = result.get("errors") == 0 and len(result["pages"]) == len(
                        pages
                    )
                    result["engine"] = webview.renderer
                except Exception as exc:
                    result.update(passed=False, error=type(exc).__name__)
                finally:
                    (profile / "smoke-result.json").write_text(
                        json.dumps(result, ensure_ascii=False, default=str), encoding="utf-8"
                    )
                    window.destroy()

            webview.start(
                check_window if smoke else None,
                gui="edgechromium",
                debug=False,
                private_mode=False,
                storage_path=str(profile / "browser"),
                icon=str(ASSETS / "brand" / "workbench.ico"),
            )
            return 0 if not smoke or result.get("passed") else 1
    finally:
        if process is not None:
            stop_server(process)
        with contextlib.suppress(FileNotFoundError):
            session_file.unlink()
        lock.release()


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG Workbench Windows desktop")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--smoke-external-process", action="store_true")
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    profile = (args.profile or bundle / "user-data").resolve()
    try:
        if sys.platform == "win32":
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("RAGWorkbench.Desktop")
        return run(
            bundle, profile, smoke=args.smoke_test, smoke_external=args.smoke_external_process
        )
    except Exception as exc:
        # Do not print exception values: providers may include sensitive request text.
        message = (
            f"桌面窗口无法启动（{type(exc).__name__}）。\n"
            "请确认解压目录可写，并已安装 Microsoft Edge WebView2 Runtime。\n"
            f"配置与诊断文件夹：{profile}"
        )
        if isinstance(exc, DesktopError):
            message = f"{exc}\n工作空间：{profile}"
        if args.smoke_test:
            profile.mkdir(parents=True, exist_ok=True)
            (profile / "smoke-result.json").write_text(
                json.dumps({"passed": False, "error": type(exc).__name__}), encoding="utf-8"
            )
        else:
            _message(message)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
