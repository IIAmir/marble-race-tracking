"""Regression tests for the bugs found in the pre-release review."""
import io
import json
import zipfile

import numpy as np
import pytest

from marbletrack.detector import Detection
from marbletrack.fusion import FusionEngine
from marbletrack.track import Projection
from marbletrack_app.app import App, UserError
from marbletrack_app.config import file_key, slug
from marbletrack_app.imageio import imread, imwrite
from marbletrack_app.ndi import SenderClock


def _det(cam, t, s_local, k, n=2):
    d2 = np.full(n, 200.0)
    d2[k] = 1.0
    return Detection(cam, t, np.zeros(2), 400, d2, [Projection(s=float(s_local), dist=0.0, s_per_unit=0.001)], 1.0)


@pytest.mark.parametrize("finish_s", [6.0, 10.0])
def test_lap_race_finishes_also_when_the_line_is_at_the_end_of_the_lap(finish_s):
    L, laps = 10.0, 2
    eng = FusionEngine(["a", "b"], (laps - 1) * L + finish_s, L, cameras={"cam": [(0.0, L)]}, laps=laps, lap_length=L)
    for t in np.arange(0.0, 20.0, 1 / 60):
        s = np.array([1.5, 1.2]) * t + 0.05
        eng.process(t, [_det("cam", t, np.mod(x, L), k) for k, x in enumerate(s) if x < laps * L + 2], "cam")
    board = eng.leaderboard()
    assert [e.status for e in board] == ["finished", "finished"]
    goal = (laps - 1) * L + finish_s
    assert abs(board[0].finish_time - (goal - 0.05) / 1.5) < 0.02


def test_new_race_does_not_report_cameras_offline_before_their_first_frame():
    cams = {f"c{i}": (i * 2.0, i * 2.0 + 2.2) for i in range(13)}
    eng = FusionEngine(["a", "b"], 20.0, 26.0, cameras=cams)
    eng.process(3600.0, [], "c0")  # a race started an hour into the session: one camera's frame first
    eng.tick(3600.0)
    for i in range(1, 13):
        eng.process(3600.0 + 0.001 * i, [], f"c{i}")
    assert not [e for e in eng.events if e.kind.startswith("camera_")]
    assert not eng.blind_zones


@pytest.mark.parametrize("step", [-0.3, -0.6, 0.3])
def test_sender_clock_recovers_from_a_camera_clock_step(step):
    rng = np.random.default_rng(1)
    clock, bad = SenderClock(), 0
    for k in range(40 * 60):
        true = k / 60
        sender = 1000.0 + true + (step if true >= 20.0 else 0.0)
        arrival = true + 0.05 + rng.exponential(0.004)
        err = clock.map(sender, arrival) - (true + 0.05)
        if true >= 20.0 and abs(err) > 0.02:
            bad += 1
    assert bad / 60 < 1.0  # back on time within a second


def _zip(files):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_STORED) as zf:
        for n, d in files.items():
            zf.writestr(n, d)
    return b.getvalue()


def test_damaged_or_foreign_map_zips_are_refused_and_leave_nothing(tmp_path):
    app = App(home=tmp_path)
    good = json.dumps({"name": "x"})
    bad = _zip({"project.json": good, "background-x.png": b"PNGDATA" * 100})
    i = bad.find(b"PNGDATAPNGDATA")
    bad = bad[:i] + bytes([bad[i] ^ 0xFF]) + bad[i + 1:]
    for data in (_zip({"project.json": json.dumps({"name": "x", "marbles": [{"id": "a", "name": "A"}]})}),
                 _zip({"project.json": "[]"}), _zip({"project.json": good, "CON.png": b"x"}), bad, b"not a zip"):
        with pytest.raises(UserError):
            app.import_map(data)
    assert app.list_maps()["maps"] == []
    assert not [p for p in (tmp_path / "projects").glob("*")] if (tmp_path / "projects").exists() else True


def test_a_map_that_cannot_be_loaded_is_never_overwritten(tmp_path):
    folder = tmp_path / "projects" / "Grand-Prix"
    folder.mkdir(parents=True)
    saved = {"schema": 99, "name": "Grand Prix", "track_length": 42.0, "marbles": [], "cameras": []}
    (folder / "project.json").write_text(json.dumps(saved))
    (tmp_path / "settings.json").write_text(json.dumps({"last_project": str(folder)}))
    app = App(home=tmp_path)
    assert app.folder is None
    app.update_race(notes="x")
    app.save()
    assert json.loads((folder / "project.json").read_text()) == saved


def test_file_names_are_safe_on_windows_and_images_work_in_non_ascii_folders(tmp_path):
    assert slug("CON") != "CON" and slug("aux.txt").split(".")[0].upper() != "AUX"
    assert slug("مسیر ۱")  # Persian names are kept, not emptied
    assert file_key("دوربین ۱").isascii() and file_key("a") != file_key("b")
    folder = tmp_path / "نقشهٔ من"
    folder.mkdir()
    img = np.random.default_rng(0).integers(0, 255, (20, 30, 3), dtype=np.uint8)
    assert imwrite(folder / "x.png", img, atomic=True)
    assert np.array_equal(imread(folder / "x.png"), img)
    assert imread(folder / "missing.png") is None
    assert not list(folder.glob(".*.tmp"))
