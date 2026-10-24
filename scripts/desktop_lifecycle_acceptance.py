"""Exercise the real EXE with a restricted PATH, hard termination, and recovery."""

import argparse
import ctypes
import json
import os
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit


def running(pid: int) -> bool:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        status = ctypes.c_ulong()
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(status))) and status.value == 259
    finally:
        kernel.CloseHandle(handle)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    args = parser.parse_args()
    bundle, profile = args.bundle.resolve(), args.profile.resolve()
    profile.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    env["PATH"] = str(Path(os.environ["WINDIR"]) / "System32")
    env["PYTHONPATH"] = "X:/deliberately-nonexistent-source"
    command = [str(bundle / "RAG Workbench.exe"), "--smoke-test", "--profile", str(profile)]
    process = subprocess.Popen(
        command + ["--smoke-external-process"], env=env, creationflags=subprocess.CREATE_NO_WINDOW
    )
    external_handle = None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    try:
        deadline = time.monotonic() + 45
        session = None
        while time.monotonic() < deadline:
            assert process.poll() is None, "EXE exited before local server startup"
            try:
                session = json.loads((profile / "desktop-session.json").read_text())
                break
            except (FileNotFoundError, json.JSONDecodeError):
                time.sleep(0.05)
        assert session is not None, "Local server did not start"
        external = json.loads((profile / "smoke-external.json").read_text())
        external_handle = kernel.OpenProcess(0x1001, False, external["pid"])
        assert external_handle, "Cannot inspect the test external process"
        process.terminate()  # Kill only this test's launcher, not arbitrary Python processes.
        process.wait(timeout=10)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and (
            running(session["pid"]) or running(session["shell_pid"])
        ):
            time.sleep(0.1)
        assert not running(session["pid"]), "Server leaked after forced launcher exit"
        assert not running(session["shell_pid"]), "Desktop Python shell leaked"
        status = ctypes.c_ulong()
        assert kernel.GetExitCodeProcess(external_handle, ctypes.byref(status))
        assert status.value == 259, "Closing the launcher killed an external editor process"
        with socket.socket() as connection:
            connection.settimeout(0.5)
            assert connection.connect_ex(("127.0.0.1", urlsplit(session["url"]).port)) != 0
        restarted = subprocess.Popen(command, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            assert restarted.wait(timeout=90) == 0
        finally:
            if restarted.poll() is None:
                restarted.terminate()
                restarted.wait(timeout=10)
        smoke = json.loads((profile / "smoke-result.json").read_text(encoding="utf-8"))
        assert smoke["passed"]
        assert not (profile / "desktop-session.json").exists()
        result = {
            "passed": True,
            "restricted_path": True,
            "forced_exit_cleanup": True,
            "recovery": True,
            "normal_exit_cleanup": True,
            "external_process_survives_launcher": True,
            "pages": smoke["pages"],
        }
        (profile / "lifecycle-result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=False))
    finally:
        if external_handle:
            kernel.TerminateProcess(
                external_handle, 0
            )  # The handle pins only our disposable test child.
            kernel.CloseHandle(external_handle)
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)


if __name__ == "__main__":
    main()
