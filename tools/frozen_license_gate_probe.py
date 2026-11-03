"""Guarded CI-only probe: frozen EXE must show its real missing/invalid-license UI.

No license bypass, no mocked COM, no print. This uses an intentionally invalid
test license in a disposable installer directory, then closes the native
license window and expects the frozen application to exit with code 1.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path


TEST_LICENSE = b"SYNTHETIC-INVALID-LICENSE-NOT-A-SECRET"


def main() -> int:
    if (
        os.name != "nt"
        or os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_OS") != "Windows"
        or os.environ.get("AUTOPRINT_PACKAGED_SMOKE") != "1"
    ):
        raise RuntimeError("Frozen GUI probe is CI-only, never run on production")
    if len(sys.argv) != 2:
        raise RuntimeError("Specify the isolated installer directory")
    install = Path(sys.argv[1]).resolve(strict=True)
    runner = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    if runner not in install.parents:
        raise RuntimeError("Refusing executable outside disposable runner storage")
    exe = install / "AutoPrint.exe"
    license_file = install / "config" / "license.json"
    if not exe.is_file() or license_file.read_bytes() != TEST_LICENSE:
        raise RuntimeError("Frozen probe requires a disposable unlicensed application")
    user_data = Path(os.environ["AUTOPRINT_USER_DATA_DIR"]).resolve()
    if runner not in user_data.parents:
        raise RuntimeError("User-data override not isolated")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    enum_fn = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = (enum_fn, wintypes.LPARAM)
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.PostMessageW.restype = wintypes.BOOL

    proc = subprocess.Popen([str(exe)], cwd=str(install))
    print(f"Frozen license-gate probe started isolated pid={proc.pid}", flush=True)
    seen = set()
    found = False
    try:
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline and not found:
            if proc.poll() is not None:
                raise RuntimeError(
                    f"Frozen executable exited before a license window (exit={proc.returncode})"
                )
            windows = []

            @enum_fn
            def callback(hwnd, _arg):
                pid = wintypes.DWORD(0)
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value == proc.pid:
                    buf = ctypes.create_unicode_buffer(260)
                    user32.GetWindowTextW(hwnd, buf, len(buf))
                    if buf.value:
                        windows.append((hwnd, buf.value))
                return True

            user32.EnumWindows(callback, 0)
            for hwnd, title in windows:
                if title not in seen:
                    print(f"Isolated frozen window: {title!r}", flush=True)
                    seen.add(title)
                if title == "AutoPrint — лицензия":
                    found = True
                    if not user32.PostMessageW(hwnd, 0x0010, 0, 0):
                        raise RuntimeError("Could not close license window safely")
                    break
            if not found:
                time.sleep(0.35)
        if not found:
            raise RuntimeError(
                "Frozen EXE did not expose its expected license window on Windows CI "
                f"(observed: {sorted(seen)})"
            )
        try:
            result = proc.wait(timeout=15)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Frozen app did not exit after closing license window") from exc
        if result != 1:
            raise RuntimeError(f"Unexpected exit after rejecting invalid license: {result}")
        print("PASS: actual PyInstaller EXE opened native invalid-license dialog and exited 1")
        print("NO full AutoPrint GUI, COM preview or print submitted (license gate only)")
        return 0
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=8)


if __name__ == "__main__":
    raise SystemExit(main())
