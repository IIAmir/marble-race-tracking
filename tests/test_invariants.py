"""Property-based fuzzing of the fusion engine: invariants that must hold for ANY input.

Hypothesis generates chaotic streams -- clutter blobs, ambiguous colours, duplicate
detections, out-of-order and repeated timestamps, NaNs, silent cameras -- and every
invariant is checked after every frame.
"""
import math

import numpy as np
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from marbletrack.detector import Detection
from marbletrack.fusion import FusionEngine
from marbletrack.track import Projection

K_MAX = 5


@st.composite
def detection(draw, k):
    kind = draw(st.sampled_from(["clean", "clean", "ambiguous", "garbage", "nan"]))
    if kind == "clean":
        d2 = np.full(k, draw(st.floats(60, 500)))
        d2[draw(st.integers(0, k - 1))] = draw(st.floats(0, 5))
    elif kind == "ambiguous":
        d2 = np.array(draw(st.lists(st.floats(0, 60), min_size=k, max_size=k)))
    elif kind == "garbage":
        d2 = np.array(draw(st.lists(st.floats(0, 1e4), min_size=k, max_size=k)))
    else:
        d2 = np.full(k, math.nan)
    cands = [Projection(draw(st.floats(-1, 30) | st.just(math.nan)), 0.0, draw(st.floats(1e-4, 5e-3)))
             for _ in range(draw(st.integers(1, 2)))]
    return Detection("x", 0.0, np.zeros(2), 10, d2, cands, sigma_px=draw(st.floats(0.5, 5)))


@st.composite
def stream(draw):
    k = draw(st.integers(2, K_MAX))
    frames, t = [], 0.0
    for _ in range(draw(st.integers(1, 60))):
        t += draw(st.sampled_from([0.0, 0.004, 0.033, 0.033, 0.2, -0.05]))  # repeats, gaps, late frames
        cam = draw(st.sampled_from(["c1", "c2", "c3"]))
        frames.append((t, cam, draw(st.lists(detection(k), max_size=k + 2))))
    return k, frames


def check_board(engine, board, k):
    assert sorted(e.marble_id for e in board) == sorted(m.marble_id for m in engine.marbles)
    assert [e.rank for e in board] == list(range(1, k + 1))
    fin = [e for e in board if e.status == "finished"]
    assert board[: len(fin)] == fin, "finished marbles must lead the board"
    times = [e.finish_time for e in fin]
    assert times == sorted(times), "finished marbles ordered by finish time"
    for a, b in zip(fin, fin[1:]):
        if b.finish_time - a.finish_time < engine.photo_finish_s:
            assert a.photo_finish and b.photo_finish, "close finish must be flagged"


@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(stream(), st.floats(5, 25))
def test_invariants_under_chaos(data, s_finish):
    k, frames = data
    eng = FusionEngine([f"m{i}" for i in range(k)], s_finish=s_finish, track_length=30,
                       cameras={"c1": (0, 10), "c2": (10, 20), "c3": (20, 30)}, static_blind_zones=[(4, 5)])
    prev_board, prev_status, final_times = None, {}, {}
    for t, cam, dets in frames:
        before = [m.n_obs for m in eng.marbles]
        eng.process(t, dets, cam)
        gained = [m.n_obs - b for m, b in zip(eng.marbles, before)]
        # I1 one detection per marble per frame, never more updates than detections
        assert all(g in (0, 1) for g in gained) and sum(gained) <= len(dets)
        # I2 filter state stays finite and covariance stays symmetric positive semi-definite
        for m in eng.marbles:
            assert np.all(np.isfinite(m.x)) and np.all(np.isfinite(m.P))
            assert np.allclose(m.P, m.P.T, atol=1e-9) and np.linalg.eigvalsh(m.P).min() > -1e-9
        board = eng.leaderboard()
        check_board(eng, board, k)  # I3 board is a valid, consistently ordered ranking
        # I4 a confirmed finish time never changes again
        for m in eng.marbles:
            if m.finish_final:
                assert final_times.setdefault(m.marble_id, m.finish_time) == m.finish_time
        # I5 no unseen overtakes: whoever moves up past a rival was observed
        if prev_board is not None:
            pos0 = {e.marble_id: e.rank for e in prev_board}
            pos1 = {e.marble_id: e.rank for e in board}
            now_status = {e.marble_id: e.status for e in board}
            for x in pos1:
                for y in pos1:
                    statuses = {prev_status[x], prev_status[y], now_status[x], now_status[y]}
                    if x == y or "finished" in statuses:
                        continue  # finishing reorders by finish time, which is evidence
                    if pos0[x] > pos0[y] and pos1[x] < pos1[y]:
                        my = eng.marbles[int(y[1:])]
                        assert (now_status[x] == "tracking" or not my.initialized
                                or "unseen" in (prev_status[x], prev_status[y]))
        prev_board = board
        prev_status = {e.marble_id: e.status for e in board}


def test_nan_and_wrong_shape_input_is_ignored():
    eng = FusionEngine(["a", "b"], s_finish=10, track_length=20)
    bad = [Detection("c", 0, np.zeros(2), 10, np.array([math.nan, 1.0]), [Projection(1.0, 0, 1e-3)]),
           Detection("c", 0, np.zeros(2), 10, np.array([0.1]), [Projection(1.0, 0, 1e-3)]),
           Detection("c", 0, np.zeros(2), 10, np.array([0.1, 99.0]), [Projection(math.inf, 0, 1e-3)])]
    eng.process(0.0, bad, "c")
    eng.process(math.nan, bad, "c")
    assert all(m.n_obs == 0 for m in eng.marbles)
