"""Circuits (lap races) and cameras that see several separate stretches of track."""
import numpy as np

from marbletrack.detector import Detection
from marbletrack.fusion import FusionEngine
from marbletrack.track import Polyline, Projection
from marbletrack_app import calibration as C
from marbletrack_app import vmix
from marbletrack_app.config import CameraSetup, Project, VmixSetup


def _det(cam, t, s_local, k, n=2):
    d2 = np.full(n, 200.0)
    d2[k] = 1.0
    return Detection(cam, t, np.zeros(2), 400, d2, [Projection(s=float(s_local), dist=0.0, s_per_unit=0.001)], 1.0)


def test_lap_race_counts_laps_lets_the_leader_lap_others_and_times_the_last_lap():
    L, laps = 10.0, 4
    eng = FusionEngine(["fast", "slow"], (laps - 1) * L + 6.0, L, cameras={"cam": [(0.0, L)]},
                       laps=laps, lap_length=L)
    speeds = np.array([1.5, 1.0])
    for t in np.arange(0.0, 45.0, 1 / 30):
        s = speeds * t + 0.05
        dets = [_det("cam", t, np.mod(x, L), k) for k, x in enumerate(s) if x < (laps - 1) * L + 7.0]
        eng.process(t, dets, "cam")
        if abs(t - 22.0) < 1 / 60:
            board = {e.marble_id: e for e in eng.leaderboard(t)}
            assert board["fast"].lap == 4 and board["slow"].lap == 3  # fast is a lap ahead
            assert abs(board["fast"].progress_m - (1.5 * t + 0.05)) < 0.05
            assert [e.marble_id for e in eng.leaderboard(t)][0] == "fast"
    board = eng.leaderboard()
    assert [e.marble_id for e in board] == ["fast", "slow"]
    fin = {e.marble_id: e.finish_time for e in board}
    assert abs(fin["fast"] - (36.0 - 0.05) / 1.5) < 0.01 and abs(fin["slow"] - (36.0 - 0.05)) < 0.01


def test_blind_zones_repeat_every_lap():
    eng = FusionEngine(["a"], 25.0, 10.0, cameras={"c1": [(0.0, 4.0), (6.0, 10.0)]}, static_blind_zones=[(7.0, 7.5)],
                       laps=3, lap_length=10.0)
    zones = eng.blind_zones
    for k in range(3):
        assert any(a <= 4.0 + 10 * k + 1e-9 and b >= 6.0 + 10 * k - 1e-9 for a, b in zones)
        assert any(a <= 7.0 + 10 * k + 1e-9 and b >= 7.5 + 10 * k - 1e-9 for a, b in zones)


def test_polyline_gaps_never_match():
    pts = np.array([[0, 0], [10, 0], [20, 0], [20, 10], [30, 10]], float)
    line = Polyline(pts, [0, 0.1, 0.2, 5.0, 5.1], gaps=[False, False, True, False])
    assert line.pieces == [(0.0, 0.2), (5.0, 5.1)]
    assert line.project([20, 5], 3.0) == []  # on the joining segment: nothing
    assert abs(line.project([25, 10], 3.0)[0].s - 5.05) < 1e-9


def test_camera_with_two_stretches_calibrates_both():
    cam = CameraSetup(key="k", backend="demo", device="k", name="Hairpin cam",
                      markers=[[100, 100], [200, 100], [300, 100], [300, 300], [200, 300], [100, 300]],
                      marker_s=[1.0, 1.2, 1.4, 3.0, 3.2, 3.4])
    assert cam.stretches == [(0, 2), (3, 5)]  # the jump in distance starts a new stretch by itself
    assert cam.s_ranges == [(1.0, 1.4), (3.0, 3.4)]
    line = C.centerline(cam)
    assert [tuple(round(x, 3) for x in p) for p in line.pieces] == [(1.0, 1.4), (3.0, 3.4)]
    assert abs(line.project([250, 300], 20)[0].s - 3.1) < 0.01
    assert line.project([300, 200], 20) == []  # between the legs is not track
    p = Project.new()
    p.track_width_m = 0.05
    mask = C.track_mask(cam, p, (400, 400))
    assert mask[100, 200] and mask[300, 200] and not mask[200, 200]
    explicit = CameraSetup(key="e", backend="demo", device="e", name="E", markers=cam.markers,
                           marker_s=[1.0, 1.2, 1.4, 1.6, 1.8, 2.0], breaks=[3])
    assert explicit.stretches == [(0, 2), (3, 5)]


def test_vmix_lap_field():
    cfg = VmixSetup(lap_field="Lap.Text")
    board = [{"name": "A", "lap": 2, "status": "tracking", "finish_time": None, "gap_m": 0.0},
             {"name": "B", "lap": 1, "status": "tracking", "finish_time": None, "gap_m": 9.0}]
    assert vmix.board_fields(cfg, board, 10.0, "running", laps=3)["Lap.Text"] == ("SetText", "LAP 2/3")
    board[0]["lap"] = 3
    assert vmix.board_fields(cfg, board, 10.0, "running", laps=3)["Lap.Text"] == ("SetText", "FINAL LAP")
    assert "Lap.Text" not in vmix.board_fields(VmixSetup(), board, 10.0, "running", laps=3)


def test_detections_timed_by_their_image_row():
    """Rolling shutter: a marble low in the frame was exposed later than one high in the frame."""
    from dataclasses import replace

    readout, v = 0.0145, 2.5
    errs = {}
    for per_row in (False, True):
        eng = FusionEngine(["top", "bottom"], 50.0, 60.0, cameras={"cam": [(0.0, 60.0)]})
        for t in np.arange(0.0, 2.0, 1 / 60):
            row_dt = {"top": -0.45 * readout, "bottom": 0.45 * readout}  # rows near the top / bottom edge
            dets = []
            for k, m in enumerate(("top", "bottom")):
                tr = t + row_dt[m]  # when this marble's rows were exposed
                d = _det("cam", t, 1.0 + k * 0.1 + v * tr, k)
                dets.append(replace(d, t=tr) if per_row else d)
            eng.process(t, dets, "cam")
        b = {e.marble_id: e for e in eng.leaderboard(t)}
        errs[per_row] = max(abs(b["top"].progress_m - (1.0 + v * t)), abs(b["bottom"].progress_m - (1.1 + v * t)))
    assert errs[False] > 0.012  # ~1.6 cm at 2.5 m/s from timing alone
    assert errs[True] < 0.004


def test_operator_swaps_two_identities_and_the_board_follows():
    eng = FusionEngine(["a", "b"], 50.0, 60.0, cameras={"cam": [(0.0, 60.0)]})
    for t in np.arange(0.0, 1.0, 1 / 60):
        eng.process(t, [_det("cam", t, 2.0 + 1.0 * t, 0), _det("cam", t, 1.0 + 1.0 * t, 1)], "cam")
    before = {e.marble_id: e.progress_m for e in eng.leaderboard()}
    assert [e.marble_id for e in eng.leaderboard()] == ["a", "b"]
    eng.swap_identities("a", "b")
    after = {e.marble_id: e.progress_m for e in eng.leaderboard()}
    assert abs(after["a"] - before["b"]) < 1e-9 and abs(after["b"] - before["a"]) < 1e-9
    assert [e.marble_id for e in eng.leaderboard()] == ["b", "a"]
    assert eng.events[-1].kind == "identity_swap"


def test_vmix_shows_provisional_until_the_result_is_confirmed():
    cfg = VmixSetup()
    board = [{"name": "A", "status": "finished", "finish_time": 12.3, "gap_m": 0.0}]
    assert vmix.board_fields(cfg, board, 12.3, "complete", result="provisional")["Status.Text"] == ("SetText", "PROVISIONAL")
    assert vmix.board_fields(cfg, board, 12.3, "complete", result="confirmed")["Status.Text"] == ("SetText", "FINAL")
    assert vmix.board_fields(cfg, [], None, "hidden")["Status.Text"] == ("SetText", "")
    client = vmix.VmixClient(cfg)  # not started: only the fields it wants to send
    client.publish_board(board, 12.3, "complete")
    client.publish_board([], None, "hidden")
    assert client._want["P1Name.Text"] == ("SetText", "") and client._want["Clock.Text"] == ("SetText", "")
