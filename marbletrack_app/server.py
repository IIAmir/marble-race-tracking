"""Local HTTP server between the UI and the controller (127.0.0.1 only).

* ``GET /``                     the app (static files from ``web/``)
* ``GET /api/state``            full state
* ``POST /api/<action>``        controller method with a JSON body of keyword arguments
* ``GET /api/stream/<cam>``     MJPEG preview (setup: the camera; live: the tracker's preview)
* ``GET /api/background/<cam>`` the captured empty-track image
* ``GET /api/live``             Server-Sent Events with the live status, 5 per second

Every API call needs the per-launch token (``X-Token`` header, or ``?k=`` for
image and event streams), so other software on the PC, or a web page open in a
browser, cannot drive the app.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .app import App, UserError

log = logging.getLogger("marbletrack.server")

ACTIONS = {
    "save", "new_project", "set_step", "set_demo_scene", "update_race", "set_marbles", "discover_cameras",
    "update_camera", "move_camera", "forget_camera", "add_marker", "undo_marker", "clear_markers",
    "set_marker_distance", "move_marker", "autofill_markers", "capture_background", "sample_colour",
    "clear_colour", "copy_colours", "autofill_colours", "update_server", "test_server", "update_vmix", "test_vmix", "list_maps", "open_map", "new_map", "export_map", "import_map",
    "check_views", "realign_cameras", "go_live", "stop_live",
    "new_race", "focus_camera",
}


def web_root() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))  # PyInstaller unpack dir
    for cand in (base / "marbletrack_app" / "web", Path(__file__).resolve().parent / "web"):
        if cand.exists():
            return cand
    raise FileNotFoundError("web folder missing")


class Server:
    def __init__(self, app: App, port: int = 0):
        self.app = app
        self.token = secrets.token_urlsafe(18)
        self.root = web_root()
        srv = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):
                log.debug(fmt, *args)

            # -- helpers -------------------------------------------------------
            def _authed(self, q) -> bool:
                tok = self.headers.get("X-Token") or (q.get("k") or [""])[0]
                return secrets.compare_digest(tok, srv.token)

            def _send(self, code, body: bytes, ctype="application/json", extra=None):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass  # the page went away (e.g. switched step) before the answer arrived

            def _json(self, code, obj):
                self._send(code, json.dumps(obj, default=_default).encode())

            # -- routes ----------------------------------------------------------
            def do_GET(self):
                u = urlparse(self.path)
                q = parse_qs(u.query)
                path = unquote(u.path)
                if not path.startswith("/api/"):
                    return self._static(path)
                if not self._authed(q):
                    return self._json(403, {"error": "forbidden"})
                try:
                    if path == "/api/state":
                        return self._json(200, srv.app.state())
                    if path.startswith("/api/stream/"):
                        return self._mjpeg(path[len("/api/stream/"):], q)
                    if path.startswith("/api/snapshot/"):
                        w = int((q.get("w") or ["320"])[0])
                        return self._send(200, srv.app.snapshot(path[len("/api/snapshot/"):], max(80, min(w, 1920))), "image/jpeg")
                    if path.startswith("/api/background/"):
                        return self._send(200, srv.app.background_jpeg(path[len("/api/background/"):]), "image/jpeg")
                    if path == "/api/live":
                        return self._sse()
                except UserError as e:
                    return self._json(409, {"error": str(e)})
                except (BrokenPipeError, ConnectionResetError):
                    return
                return self._json(404, {"error": "not found"})

            def do_POST(self):
                u = urlparse(self.path)
                if not self._authed(parse_qs(u.query)):
                    return self._json(403, {"error": "forbidden"})
                action = u.path[len("/api/"):] if u.path.startswith("/api/") else ""
                if action not in ACTIONS:
                    return self._json(404, {"error": f"unknown action {action}"})
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    if action == "import_map":  # raw zip body
                        return self._json(200, srv.app.import_map(self.rfile.read(n) if n else b""))
                    kwargs = json.loads(self.rfile.read(n) or b"{}") if n else {}
                    if not isinstance(kwargs, dict):
                        raise UserError("bad request")
                    result = getattr(srv.app, action)(**kwargs)
                    return self._json(200, result)
                except UserError as e:
                    return self._json(409, {"error": str(e)})
                except TypeError as e:
                    return self._json(400, {"error": f"bad arguments: {e}"})
                except Exception as e:
                    log.exception("action %s failed", action)
                    return self._json(500, {"error": f"Unexpected error: {e}"})

            def _static(self, path):
                if path in ("/", ""):
                    path = "/index.html"
                f = (srv.root / path.lstrip("/")).resolve()
                if srv.root.resolve() not in f.parents or not f.is_file():
                    return self._send(404, b"not found", "text/plain")
                ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                if ctype.startswith("text/") or ctype in ("application/javascript",):
                    ctype += "; charset=utf-8"
                self._send(200, f.read_bytes(), ctype)

            def _mjpeg(self, key, q):
                width = int((q.get("w") or ["960"])[0])
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                last = -1
                try:
                    while True:
                        if srv.app.mode == "live" and srv.app.runtime is not None:
                            rt = srv.app.runtime
                            with rt.preview_cond:
                                rt.preview_cond.wait_for(lambda: rt.previews.get(key, (0, 0, 0, -1))[3] != last, 2.0)
                                item = rt.previews.get(key)
                            if item is None:
                                continue
                            jpg, _, _, last = item
                        else:
                            if srv.app.mode != "setup":
                                time.sleep(0.2)
                                continue
                            lc = srv.app.live_camera(key)
                            lc.wait(last, timeout=2.0)
                            jpg, seq = lc.jpeg(width)
                            if jpg is None or seq == last:
                                if lc.error:
                                    time.sleep(0.5)
                                continue
                            last = seq
                            time.sleep(1 / 20)  # previews at <= 20 fps are plenty for setup
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                    return
                except UserError:
                    return

            def _sse(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    while True:
                        if srv.app.mode == "live":
                            data = srv.app.live_status()
                        else:
                            data = {"setup": {c.key: {"fps": round(lc.fps, 1), "error": lc.error} for c in srv.app.project.cameras
                                              if (lc := srv.app.hub.get(c.key))}, "toasts": srv.app.pop_toasts()}
                        data["mode"] = srv.app.mode
                        self.wfile.write(b"data: " + json.dumps(data, default=_default).encode() + b"\n\n")
                        self.wfile.flush()
                        time.sleep(0.2)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                    return

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True, name="http")

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?k={self.token}"

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.httpd.shutdown()


def _default(o):
    import numpy as np

    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, Path):
        return str(o)
    raise TypeError(type(o).__name__)
