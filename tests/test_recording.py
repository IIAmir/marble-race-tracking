"""Recording a live session and replaying it through the tracker without cameras."""
import json
import tempfile
import time
from pathlib import Path

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
    time.sleep(8)
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
    assert any(e["kind"] == "camera_recorded" for e in read_jsonl(session / "events.jsonl"))

    res = replay(session, workers=3)
    out = Path(res["out"])
    boards = read_jsonl(out / "boards.jsonl")
    assert len(boards) > 20
    seen_live = {e["marble_id"] for b in live for e in b["board"] if e["status"] != "unseen"}
    seen_replay = {e["marble_id"] for b in boards for e in b["board"] if e["status"] != "unseen"}
    assert seen_live and seen_replay >= seen_live - {None}  # the replay sees every marble live saw
    # the recorded run, tracked again, ends in the same order as on air
    assert [e["marble_id"] for e in boards[-1]["board"]][:3] == [e["marble_id"] for e in live[-1]["board"]][:3]
