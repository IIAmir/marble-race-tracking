"""Recording a live session and replaying it through the tracker without cameras."""
import json
import tempfile
import time
from pathlib import Path

import numpy as np
import pytest

from marbletrack_app.recording import read_frames_csv, read_jsonl


@pytest.mark.slow
def test_live_session_is_recorded_and_replays_like_live():
    from marbletrack_app.app import App
    from marbletrack_app.replay import replay

    home = Path(tempfile.mkdtemp(prefix="marbletrack-rec-"))
    app = App(home=home, demo=True)
    app.discover_cameras()
    for c in app.project.cameras[3:]:
        app.update_camera(c.key, enabled=False)
    for c in app.project.active_cameras:
        app.autofill_markers(c.key)
    app.capture_background()
    for c in app.project.active_cameras:
        app.autofill_colours(c.key)
    app.project.server.enabled = False
    app.project.record.enabled = True
    app.go_live()
    t0 = time.time()
    while time.time() - t0 < 90:
        if all(c.get("fps", 0) > 0 or c["error"] for c in app.live_status()["cameras"].values()):
            break
        time.sleep(0.5)
    ids = [m.id for m in app.project.marbles]
    app.live_harvest(ids[0])  # training crops (the demo has every marble on the track: only the mechanism is tested)
    time.sleep(6)
    # race control: a correction and a confirmation go into the session's event log
    app.live_operator("swap", a=ids[0], b=ids[1])
    app.live_operator("freeze", on=True)
    app.live_operator("freeze", on=False)
    app.live_operator("confirm", confirmed=True)
    time.sleep(2)
    app.live_harvest(None)
    time.sleep(1)
    session = app.runtime.record_dir
    app.stop_live()
    app.shutdown()

    manifest = json.loads((session / "manifest.json").read_text())
    assert len(manifest["cameras"]) == 3
    for name in manifest["cameras"].values():
        rows = read_frames_csv(session / "cameras" / f"{name}.csv")
        assert len(rows) > 50 and (session / "cameras" / f"{name}.avi").stat().st_size > 0
        caps = [r[1] for r in rows]
        assert caps == sorted(caps)
    live = read_jsonl(session / "boards.jsonl")
    assert len(live) > 20
    crops = list((app._project_folder() / "training" / ids[0]).glob("*.png"))
    assert len(crops) >= 5
    import cv2

    assert cv2.imread(str(crops[0])).shape == (64, 128, 3)  # frame crop | empty-track crop
    events = read_jsonl(session / "events.jsonl")
    assert any(e["kind"] == "camera_recorded" for e in events)
    ops = [e["action"] for e in events if e["kind"] == "operator"]
    assert ops == ["swap", "freeze", "freeze", "confirm"]
    assert [e["label"] for e in events if e["kind"] == "harvest"] == [ids[0], None]
    assert live[-1]["result"] == "confirmed"

    res = replay(session, workers=3)
    out = Path(res["out"])
    boards = read_jsonl(out / "boards.jsonl")
    assert len(boards) > 20
    seen_live = {e["marble_id"] for b in live for e in b["board"] if e["status"] != "unseen"}
    seen_replay = {e["marble_id"] for b in boards for e in b["board"] if e["status"] != "unseen"}
    assert seen_live and seen_replay >= seen_live - {None}  # the replay sees every marble live saw
    # (operator corrections are not replayed: the replay shows what the tracker alone does)

    # gate labels proposed from the replay, with picture strips, then the live boards scored against them
    from marbletrack_app.labels import propose, score

    gates = [0.6, 2.5]  # inside the three cameras' view
    path = propose(session, gates, out / "boards.jsonl")
    labels = json.loads(path.read_text())
    assert [g["s"] for g in labels["gates"]] == gates and all(g["order"] for g in labels["gates"])
    assert list((session / "labels").glob("gate-*.jpg"))
    res = score(live, labels)
    # live and replay agree up to near-ties in the starting pack (2 gates x 45 pairs)
    assert res["gates"] == 2 and res["gate_pairs_wrong"] <= 4


def _boards(paths, t_end=10.0, dt=0.1, swap=None):
    """Boards for marbles moving along s(t) = paths[m](t); ``swap`` = (t0, t1, a, b) shows b ahead of a then."""
    out = []
    for t in np.arange(0.0, t_end, dt):
        pos = {m: f(t) for m, f in paths.items()}
        ids = sorted(pos, key=lambda m: -pos[m])
        if swap and swap[0] <= t < swap[1]:
            i, j = ids.index(swap[2]), ids.index(swap[3])
            ids[i], ids[j] = ids[j], ids[i]
        out.append({"now": round(float(t), 3), "board": [{"marble_id": m, "progress_m": pos[m], "status": "tracking"} for m in ids]})
    return out


def test_gate_scoring_counts_wrong_orders_and_time_shown_wrong():
    from marbletrack_app.labels import gate_orders, score

    paths = {"a": lambda t: 1.0 * t, "b": lambda t: 0.9 * t, "c": lambda t: 0.5 + 0.7 * t}  # c leads, a overtakes it
    boards = _boards(paths)
    got = gate_orders(boards, [1.0, 6.0])
    assert got[0]["order"] == ["c", "a", "b"] and got[1]["order"] == ["a", "b", "c"]
    assert abs(got[0]["times"]["a"] - 1.0) < 0.02  # crossing time interpolated between boards
    labels = {"gates": [{"s": 1.0, "order": ["c", "a", "b"]}, {"s": 6.0, "order": ["a", "b", "c"]}],
              "final_order": ["a", "b", "c"]}
    good = score(boards, labels)
    assert good["gate_order_exact"] == 2 and good["gate_pairs_wrong"] == 0 and good["inverted_seconds"] == 0
    assert good["final_order_correct"]
    # the board shows b ahead of a for half a second although a stays ahead between the gates
    bad = score(_boards(paths, swap=(3.0, 3.5, "a", "b")), labels)
    assert 0.4 <= bad["inverted_seconds"] <= 0.6 and bad["gate_order_exact"] == 2
    wrong = score(boards, {**labels, "gates": [{"s": 1.0, "order": ["a", "c", "b"]}]})
    assert wrong["gate_pairs_wrong"] == 1 and wrong["gate_order_exact"] == 0
