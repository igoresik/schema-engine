"""The desktop app: the API and its UI in a native window (WebView2). Closing it stops both."""

import ctypes
import json
import logging
import socket
import sys
import threading
import time
import urllib.request
from contextlib import suppress
from logging.handlers import RotatingFileHandler
from pathlib import Path

import webview

from schema_engine.app import DATA_DIR  # light import, so the window opens before pandas loads

NAME = "Schema Engine"
STATE = DATA_DIR / "window.json"
LOADING = """<!doctype html>
<style>
  body { margin: 0; height: 100vh; display: grid; place-content: center; gap: 8px;
    text-align: center; background: #e9eeec; color: #4a5956; font: 13px "Segoe UI", sans-serif }
  @media (prefers-color-scheme: dark) { body { background: #111817; color: #a0afac } }
</style>
<div style="font-size:15px">Opening Schema Engine&hellip;</div>
<div id="slow" style="visibility:hidden;max-width:380px">The first start after installing takes
  longer while Windows checks the new files. It is quick from then on.</div>
<script>
  setTimeout(() => document.getElementById("slow").style.visibility = "visible", 5000)
</script>"""
FILE_TYPES = {
    "data": ("Spreadsheets (*.csv;*.tsv;*.txt;*.xlsx;*.xlsm)", "All files (*.*)"),
    "transform": ("Python transform (*.py)", "All files (*.*)"),
}
SAVE_TYPES = {
    ".csv": "CSV (*.csv)",
    ".tsv": "TSV (*.tsv)",
    ".txt": "Text (*.txt)",
    ".xlsx": "Excel (*.xlsx)",
    ".sql": "SQLite script (*.sql)",
    ".py": "Python transform (*.py)",
}
_mutex = None  # held for the life of the process


class Dialogs:
    """Native open and save dialogs for the API."""

    def __init__(self, window):
        self.window = window

    def open(self, kind):
        picked = self.window.create_file_dialog(
            webview.FileDialog.OPEN, file_types=FILE_TYPES[kind]
        )
        return picked[0] if picked else None

    def save(self, kind, suggested: Path):
        # the suggested type first, so the dialog keeps its extension
        others = [t for s, t in SAVE_TYPES.items() if s not in (suggested.suffix, ".py")]
        types = (SAVE_TYPES[suggested.suffix], *(others if kind == "data" else []))
        picked = self.window.create_file_dialog(
            webview.FileDialog.SAVE,
            directory=str(suggested.parent),
            save_filename=suggested.name,
            file_types=types,
        )
        if isinstance(picked, (tuple, list)):
            picked = picked[0] if picked else None
        return picked or None


def already_open():
    """A second launch brings the open window forward instead of starting another copy."""
    global _mutex
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _mutex = kernel32.CreateMutexW(None, False, "SchemaEngine.SingleInstance")
    if ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
        return False
    if window := ctypes.windll.user32.FindWindowW(None, NAME):
        ctypes.windll.user32.ShowWindow(window, 9)  # SW_RESTORE
        ctypes.windll.user32.SetForegroundWindow(window)
    return True


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _saved_shape():
    """The window's last normal size and whether it was maximized. A first start opens maximized."""
    try:
        saved = json.loads(STATE.read_text(encoding="utf-8"))
        maximized = saved.get("maximized") is not False
        return max(1000, int(saved["width"])), max(640, int(saved["height"])), maximized
    except (OSError, ValueError, KeyError, TypeError):
        return 1400, 880, True


def main():
    if already_open():
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log = RotatingFileHandler(
        DATA_DIR / "app.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8"
    )
    logging.basicConfig(
        level=logging.INFO,
        handlers=[log],
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if sys.stdout is None:  # a windowed exe has no console to print to
        sys.stdout = sys.stderr = open(DATA_DIR / "console.log", "w", encoding="utf-8")  # noqa: SIM115

    width, height, maximized = _saved_shape()
    # a normal window fits on the main screen (Windows puts it at 0, 0) and starts in its middle
    screen = next((s for s in webview.screens if s.x == 0 and s.y == 0), None)
    if screen:
        width, height = min(width, screen.width - 40), min(height, screen.height - 100)
    window = webview.create_window(
        NAME,
        html=LOADING,
        width=width,
        height=height,
        min_size=(1000, 640),
        screen=screen,
        maximized=maximized,
    )
    shape = {"width": width, "height": height, "maximized": maximized, "minimized": False}

    def resized(width, height):  # state events come first, so only a normal window's size is kept
        if not shape["maximized"] and not shape["minimized"]:
            shape.update(width=width, height=height)

    window.events.maximized += lambda: shape.update(maximized=True, minimized=False)
    window.events.minimized += lambda: shape.update(minimized=True)
    window.events.restored += lambda: shape.update(maximized=False, minimized=False)
    window.events.resized += resized
    engine = {}

    def start_engine():  # runs once the window is up
        import uvicorn

        from schema_engine.app.api import create_app

        port = _free_port()
        engine["server"] = uvicorn.Server(
            uvicorn.Config(
                create_app(Dialogs(window)),
                host="127.0.0.1",
                port=port,
                log_config=None,
                access_log=False,
            )
        )
        threading.Thread(target=engine["server"].run, daemon=True).start()
        for _ in range(600):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        window.load_url(f"http://127.0.0.1:{port}/")

    def remember():
        with suppress(OSError):
            STATE.write_text(
                json.dumps({k: shape[k] for k in ("width", "height", "maximized")}),
                encoding="utf-8",
            )

    window.events.closing += remember
    try:
        webview.start(
            start_engine,
            gui="edgechromium",
            private_mode=False,
            storage_path=str(DATA_DIR / "webview"),
        )
    except Exception:
        logging.exception("the window could not open")
        ctypes.windll.user32.MessageBoxW(
            None,
            f"{NAME} needs the Microsoft Edge WebView2 Runtime. Install it from Microsoft's "
            f"website, then open {NAME} again.",
            NAME,
            0x10,
        )
    if "server" in engine:
        engine["server"].should_exit = True
