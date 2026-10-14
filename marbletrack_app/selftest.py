"""Headless end-to-end check of a build (``MarbleTrack.exe --selftest``).

Runs the whole chain on the demo cameras: discovery, waypoints, empty-track
background, colour sampling, live tracking in camera processes, and WebSocket
streaming to an in-process server. Checks that it works, not how accurately
(build machines are slow and shared): accuracy is the stress suite's job.
"""
from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import threading
import time
import traceback
import urllib.request
from pathlib import Path

log = logging.getLogger("marbletrack.selftest")


def run_selftest(out: Path | None = None, live_seconds: float = 25.0, cameras: int | None = None) -> int:
    report = {"ok": False, "checks": []}

    def check(name, ok, detail=""):
        report["checks"].append({"name": name, "ok": bool(ok), "detail": str(detail)})
        log.info("selftest %-28s %s %s", name, "OK " if ok else "FAIL", detail)
        if not ok:
            raise AssertionError(f"{name}: {detail}")

    received = []
    try:
        import cv2, numpy, websockets  # noqa: F401  (bundled?)

        from . import __version__
        from .app import App
        from .server import Server

        check("imports", True, f"MarbleTrack {__version__}, OpenCV {cv2.__version__}, websockets {websockets.__version__}")
        port = _start_receiver(received)
        home = Path(tempfile.mkdtemp(prefix="marbletrack-selftest-"))
        app = App(home=home, demo=True)
        srv = Server(app).start()
        with urllib.request.urlopen(srv.url.replace("/?k=", "/api/state?k="), timeout=10) as r:
            check("local server", r.status == 200, srv.url.split("?")[0])
        with urllib.request.urlopen(srv.url, timeout=10) as r:
            check("user interface", b"MarbleTrack" in r.read(), "index.html served")
        t = time.time()
        st = app.discover_cameras()
        demo = [c for c in st["cameras"] if c["backend"] == "demo"]
        check("camera discovery", len(demo) == 15, f"{len(st['cameras'])} cameras ({len(demo)} demo) in {time.time() - t:.1f} s")
        if cameras:
            for c in app.project.cameras[cameras:]:
                app.update_camera(c.key, enabled=False)
        for c in app.project.active_cameras:
            app.autofill_markers(c.key)
        check("waypoints", not app.step_status()["waypoints"], app.project.coverage()["gaps"])
        r = app.capture_background()
        check("background", all(v["ok"] for v in r["results"].values()), "all cameras")
        t = time.time()
        for c in app.project.active_cameras:
            app.autofill_colours(c.key)
        check("colour calibration", not app.step_status()["colours"], f"{time.time() - t:.1f} s")
        app.update_server(url=f"ws://127.0.0.1:{port}", station_id="selftest")
        tr = app.test_server()
        check("server test", tr["ok"], tr["detail"])
        app.go_live()
        t0 = time.time()
        # camera processes start slowly on a busy build machine: wait until every one delivers
        while time.time() - t0 < 120:
            cams = app.live_status()["cameras"]
            if all(c.get("fps", 0) > 0 or c["error"] for c in cams.values()):
                break
            time.sleep(1)
        check("cameras started", True, f"{time.time() - t0:.0f} s")
        t1 = time.time()
        while time.time() - t1 < live_seconds:
            time.sleep(1)
        live = app.live_status()
        app.stop_live()
        errors = {k: v["error"] for k, v in live["cameras"].items() if v["error"]}
        check("camera processes", not errors, errors or f"{len(live['cameras'])} running")
        dets = sum(1 for v in live["cameras"].values() if v.get("fps", 0) > 0)
        check("frames processed", dets == len(live["cameras"]), f"{dets} cameras delivering")
        kinds = {}
        for m in received:
            k = m.get("event", m["type"])
            kinds[k] = kinds.get(k, 0) + 1
        check("leaderboard streamed", kinds.get("leaderboard", 0) > 10 and kinds.get("hello", 0) >= 1, kinds)
        seen = {e["marble_id"] for m in received if m["type"] == "leaderboard" for e in m["board"] if e["status"] != "unseen"}
        check("marbles tracked", len(seen) >= len(app.project.marbles) // 2, f"{len(seen)} of {len(app.project.marbles)} seen")
        srv.stop()
        app.shutdown()
        report["ok"] = True
    except Exception as e:
        report["error"] = f"{e}"
        report["trace"] = traceback.format_exc()
        log.error("selftest failed: %s", traceback.format_exc())
    if out:
        Path(out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1


def _start_receiver(store: list) -> int:
    from websockets.asyncio.server import serve

    ready = threading.Event()
    port_box = []

    async def handler(ws):
        async for raw in ws:
            store.append(json.loads(raw))

    async def main():
        async with serve(handler, "127.0.0.1", 0) as server:
            port_box.append(server.sockets[0].getsockname()[1])
            ready.set()
            await asyncio.Future()

    threading.Thread(target=lambda: asyncio.run(main()), daemon=True).start()
    ready.wait(10)
    return port_box[0]
