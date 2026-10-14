"""Desktop app: configuration, calibration from clicks, streaming, local server, end to end."""
import asyncio
import json
import threading
import time
import urllib.error
import urllib.request

import numpy as np
import pytest

from marbletrack.color import bgr_to_lab
from marbletrack.detector import ColorDetector
from marbletrack_app import calibration as C
from marbletrack_app.config import CameraSetup, ColorSample, MarbleDef, Project, load_project, save_project


# ---------------------------------------------------------------- configuration
def test_project_roundtrip(tmp_path):
    p = Project.new()
    p.track_length, p.finish_s = 13.5, 13.3
    cam = CameraSetup(key="opencv:0", backend="opencv", device="0", name="Gate cam", markers=[[1, 2], [3, 4]], marker_s=[0.0, 0.2])
    cam.palette["ruby"] = ColorSample([50.0, 60.0, 40.0], np.eye(3).tolist(), [50, 60, 40], 120, 1)
    p.cameras.append(cam)
    save_project(p, tmp_path)
    q = load_project(tmp_path)
    assert q.to_dict() == p.to_dict()
    assert q.camera("opencv:0").s_range == (0.0, 0.2)


def test_coverage_reports_gaps():
    p = Project.new()
    p.track_length = 3.0
    p.cameras = [CameraSetup(key="a", backend="demo", device="a", name="A", marker_s=[0.0, 1.0], markers=[[0, 0], [1, 1]]),
                 CameraSetup(key="b", backend="demo", device="b", name="B", marker_s=[1.5, 3.0], markers=[[0, 0], [1, 1]])]
    gaps = p.coverage()["gaps"]
    assert len(gaps) == 1 and abs(gaps[0][0] - 1.01) < 0.02 and abs(gaps[0][1] - 1.5) < 0.02


# ---------------------------------------------------------------- calibration
def test_projective_interpolation_handles_perspective():
    """A straight stretch seen along its length: marks are compressed with distance.

    Linear interpolation between marks 20 cm apart is off by millimetres; the
    projective fit is exact for a straight line.
    """
    s_true = np.linspace(0, 1.0, 201)
    u = 400 * s_true / (s_true + 0.8)  # 1-D perspective projection of a line
    marks = np.arange(0, 1.0001, 0.2)
    um = 400 * marks / (marks + 0.8)
    cam = CameraSetup(key="k", backend="demo", device="k", name="k", markers=[[x, 100.0] for x in um], marker_s=list(marks))
    line = C.centerline(cam)
    est = np.array([line.project([x, 100.0], 5)[0].s for x in u])
    lin = np.interp(u, um, marks)
    assert np.abs(est - s_true).max() < 0.0005
    assert np.abs(lin - s_true).max() > 0.004  # what the projective fit avoids


def test_waypoints_must_increase():
    cam = CameraSetup(key="k", backend="demo", device="k", name="k", markers=[[0, 0], [10, 0]], marker_s=[0.4, 0.2])
    with pytest.raises(C.CalibrationError):
        C.centerline(cam)


@pytest.fixture(scope="module")
def world():
    from marbletrack_app.cameras import demo_world

    return demo_world()


def _calibrate(world, cid, project):
    cam = world.camera(cid)
    uv, s = world.waypoints(cid, 0.2 if cid != "finish" else 0.05, jitter_px=1.0, seed=3)
    cs = CameraSetup(key="demo:" + cid, backend="demo", device=cid, name=cid, markers=uv, marker_s=s)
    bg = C.median_background([world.render(cam, 0, "empty") for _ in range(5)])
    mask = C.track_mask(cs, project, cam.size)
    r_exp = float(np.median(C.marble_radius_px(cs, project)))
    rng = np.random.default_rng(0)
    radii = []
    for k, m in enumerate(project.marbles):
        tp = next(t for t in np.arange(0, 60, 1.0) if world.parked_positions(cam, t)[1][k])
        frame = world.render(cam, tp, "parked")
        xy, _ = world.parked_positions(cam, tp)
        uvp, _ = cam.project(np.c_[xy, np.full(len(xy), world.cfg.marble_radius)])
        seg = C.segment_click(frame, bg, uvp[k] + rng.normal(0, 1.5, 2), r_exp, mask)  # a human click, not pixel-exact
        cs.palette[m.id] = C.fit_sample(seg["pixels"], 1, [np.median(bgr_to_lab(seg["pixels"]), axis=0)])
        radii.append(seg["radius"] / r_exp)
    cs.radius_scale = float(np.median(radii))
    return cs, bg


@pytest.mark.parametrize("cid", ["cam01", "cam05", "cam10", "finish"])
def test_calibration_from_clicks_tracks_accurately(world, cid):
    """Waypoints clicked with 1 px of hand jitter, colours from one click per marble:
    every detection gets the right identity, and progress is within millimetres.

    The demo cameras are 640x480 for about a metre of track; where they see the
    track far away 1 px is ~5 mm, which bounds the 95th percentile here."""
    project = Project.new()
    project.marbles = [MarbleDef(m.marble_id.lower(), m.marble_id, "#000000") for m in world.marbles]
    cs, bg = _calibrate(world, cid, project)
    assert C.palette_issues(cs, project) == []
    det = ColorDetector(C.build_calibration(cs, project, bg))
    cam = world.camera(cid)
    errs, wrong = [], 0
    for t in np.arange(5, 40, 0.29):
        sim, rt = world.race_at(t)
        if sim is None:
            continue
        truth = sim.progress(rt)
        for d in det.detect(world.render(cam, t, "race"), t):
            k = int(np.argmin(d.color_d2))
            e = d.candidates[0].s - truth[k]
            if abs(e) > 0.05:
                wrong += 1
            else:
                errs.append(abs(e))
    assert len(errs) >= 6 and wrong == 0
    assert np.median(errs) < 0.004 and np.percentile(errs, 95) < 0.02


def test_palette_check_flags_lookalikes():
    project = Project.new()
    project.marbles = [MarbleDef("a", "A", "#ff0000"), MarbleDef("b", "B", "#ff1010"), MarbleDef("c", "C", "#0000ff")]
    rng = np.random.default_rng(1)
    cam = CameraSetup(key="k", backend="demo", device="k", name="k")
    for mid, bgr in (("a", (30, 30, 200)), ("b", (34, 33, 204)), ("c", (200, 40, 30))):
        px = np.clip(rng.normal(bgr, 6, (400, 3)), 0, 255).astype(np.uint8)
        cam.palette[mid] = C.fit_sample(px, 1)
    issues = C.palette_issues(cam, project)
    assert {tuple(sorted((i["a"], i["b"]))) for i in issues} == {("a", "b")}


def test_click_on_empty_track_is_explained(world):
    cam = world.camera("cam03")
    bg = world.render(cam, 0, "empty")
    with pytest.raises(C.CalibrationError, match="Nothing found"):
        C.segment_click(world.render(cam, 0, "empty"), bg, (100, 100), 5.0)


# ---------------------------------------------------------------- streaming
def _receiver(store, token=""):
    from websockets.asyncio.server import serve
    from websockets.datastructures import Headers
    from websockets.http11 import Response

    ready, box = threading.Event(), {}

    def auth(conn, req):
        if token and req.headers.get("Authorization") != f"Bearer {token}":
            return Response(401, "Unauthorized", Headers([("Content-Length", "0")]), b"")

    async def handler(ws):
        async for raw in ws:
            store.append(json.loads(raw))

    async def main():
        async with serve(handler, "127.0.0.1", box.get("port", 0), process_request=auth) as srv:
            box["port"] = srv.sockets[0].getsockname()[1]
            box["stop"] = asyncio.get_running_loop().create_future()
            box["loop"] = asyncio.get_running_loop()
            ready.set()
            await box["stop"]

    def start():
        ready.clear()
        threading.Thread(target=lambda: asyncio.run(main()), daemon=True).start()
        ready.wait(5)

    def stop():
        box["loop"].call_soon_threadsafe(box["stop"].set_result, None)
        time.sleep(0.3)

    start()
    return box, start, stop


def _wait(pred, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_streamer_sends_hello_events_and_latest_board_and_survives_restart():
    from marbletrack_app.ws_client import LeaderboardStreamer

    got = []
    box, start, stop = _receiver(got, token="s3cret")
    st = LeaderboardStreamer(f"ws://127.0.0.1:{box['port']}", "s3cret", "track-9", "test").start()
    assert _wait(lambda: st.status()["state"] == "connected")
    st.publish_event("race_started", {"race_id": "r1"})
    for k in range(50):  # many snapshots in a burst: only the newest needs to arrive
        st.publish_board("r1", "running", k * 0.1, [{"rank": 1, "marble_id": "a", "name": "A"}], {})
    assert _wait(lambda: any(m["type"] == "leaderboard" and m["race_time"] == 4.9 for m in got))
    assert got[0]["type"] == "hello" and got[0]["station_id"] == "track-9" and got[0]["schema"] == "marbletrack.v1"
    assert [m["event"] for m in got if m["type"] == "race_event"] == ["race_started"]
    seqs = [m["seq"] for m in got]
    assert seqs == sorted(seqs)
    # server goes away: events are kept and delivered, in order, after reconnecting
    stop()
    assert _wait(lambda: st.status()["state"] == "reconnecting")
    for i in range(3):
        st.publish_event("finish", {"race_id": "r1", "rank": i + 1})
    got.clear()
    start()
    assert _wait(lambda: len([m for m in got if m.get("event") == "finish"]) == 3, 20)
    assert [m["rank"] for m in got if m.get("event") == "finish"] == [1, 2, 3]
    assert got[0]["type"] == "hello"
    st.stop()
    stop()


def test_connection_test_explains_failures():
    from marbletrack_app.ws_client import test_connection

    assert "ws://" in test_connection("http://x")["detail"]
    r = test_connection("ws://127.0.0.1:1")
    assert not r["ok"] and "refused" in r["detail"]
    got = []
    box, _, stop = _receiver(got, token="right")
    r = test_connection(f"ws://127.0.0.1:{box['port']}", token="wrong")
    assert not r["ok"] and "token" in r["detail"]
    assert test_connection(f"ws://127.0.0.1:{box['port']}", token="right")["ok"]
    stop()


# ---------------------------------------------------------------- local server
def test_local_server_requires_the_launch_token(tmp_path):
    from marbletrack_app.app import App
    from marbletrack_app.server import Server

    srv = Server(App(home=tmp_path)).start()
    base = f"http://127.0.0.1:{srv.port}"
    try:
        with urllib.request.urlopen(base + "/") as r:
            assert b"MarbleTrack" in r.read()
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/api/state")
        assert e.value.code == 403
        req = urllib.request.Request(base + "/api/state", headers={"X-Token": srv.token})
        with urllib.request.urlopen(req) as r:
            st = json.loads(r.read())
        assert st["mode"] == "setup" and st["steps"]["race"]
        req = urllib.request.Request(base + "/api/update_race", data=json.dumps({"track_length": 12.5, "finish_s": 12.0}).encode(),
                                     headers={"X-Token": srv.token, "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req) as r:
            st = json.loads(r.read())
        assert st["project"]["track_length"] == 12.5 and not st["steps"]["race"]
        req = urllib.request.Request(base + "/api/shutdown", data=b"{}", headers={"X-Token": srv.token}, method="POST")
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 404  # only whitelisted actions
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/../config.py")
        assert e.value.code == 404
    finally:
        srv.stop()


# ---------------------------------------------------------------- end to end (camera processes + streaming)
@pytest.mark.slow
def test_selftest_end_to_end(tmp_path):
    """Wizard actions on demo cameras, live tracking in camera processes, WebSocket streaming."""
    from marbletrack_app.selftest import run_selftest

    out = tmp_path / "selftest.json"
    code = run_selftest(out, live_seconds=12.0, cameras=4)
    report = json.loads(out.read_text())
    assert code == 0, report
