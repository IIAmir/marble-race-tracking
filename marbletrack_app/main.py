"""MarbleTrack desktop app entry point.

    MarbleTrack.exe                 normal start (native window)
    MarbleTrack.exe --demo          also offer the simulator's virtual cameras
    MarbleTrack.exe --browser       open the UI in the default browser instead
    MarbleTrack.exe --selftest      headless end-to-end check (used by the build pipeline)
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import multiprocessing
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path


def _setup_logging(home: Path, debug: bool) -> Path:
    logdir = home / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    path = logdir / "marbletrack.log"
    handlers = [logging.handlers.RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")]
    if sys.stderr is not None and not getattr(sys, "frozen", False):
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return path


def main(argv=None) -> int:
    multiprocessing.freeze_support()  # camera processes in the frozen .exe re-enter here
    ap = argparse.ArgumentParser(prog="MarbleTrack", description="Marble race tracking: setup wizard and live leaderboard")
    ap.add_argument("--demo", action="store_true", help="offer virtual demo cameras (no hardware needed)")
    ap.add_argument("--browser", action="store_true", help="use the default web browser instead of the app window")
    ap.add_argument("--port", type=int, default=0, help="local UI port (default: any free port)")
    ap.add_argument("--home", type=Path, default=None, help="data folder (default: %%APPDATA%%\\MarbleTrack)")
    ap.add_argument("--no-open", action="store_true", help="start the server only (for testing)")
    ap.add_argument("--selftest", action="store_true", help="run a headless end-to-end check and exit")
    ap.add_argument("--selftest-out", type=Path, default=None)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args(argv)
    if args.home:
        os.environ["MARBLETRACK_HOME"] = str(args.home)

    from .config import app_dir

    home = app_dir()
    home.mkdir(parents=True, exist_ok=True)
    logfile = _setup_logging(home, args.debug)
    log = logging.getLogger("marbletrack")
    from . import __version__

    log.info("MarbleTrack %s starting (log: %s)", __version__, logfile)

    if args.selftest:
        from .selftest import run_selftest

        return run_selftest(args.selftest_out)

    from .app import App
    from .server import Server

    app = App(home=home, demo=args.demo)
    server = Server(app, args.port).start()
    log.info("UI at %s", server.url.split("?")[0])
    try:
        if args.no_open:
            _say(server.url)
            threading.Event().wait()
        elif args.browser or not _open_window(server.url, home, log):
            webbrowser.open(server.url)
            _say(f"MarbleTrack is running at {server.url}\nClose this window or press Ctrl+C to stop.")
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        app.shutdown()
        server.stop()
    return 0


def _say(text: str):
    if sys.stdout is not None:  # a windowed .exe has no console
        print(text, flush=True)


def _open_window(url: str, home: Path, log) -> bool:
    """Native window (Edge WebView2 on Windows). False if unavailable, so the caller falls back to the browser."""
    try:
        import webview  # pywebview
    except Exception as e:
        log.warning("pywebview not available (%s); using the browser", e)
        return False
    try:
        webview.create_window("MarbleTrack", url, width=1440, height=920, min_size=(1100, 720),
                              background_color="#0e131a", confirm_close=True, text_select=True)
        webview.start(gui="edgechromium" if sys.platform == "win32" else None, private_mode=False,
                      storage_path=str(home / "webview"))
        return True
    except Exception as e:
        log.warning("app window failed (%s); using the browser. Installing the Microsoft Edge WebView2 Runtime fixes this.", e)
        return False


if __name__ == "__main__":
    sys.exit(main())
