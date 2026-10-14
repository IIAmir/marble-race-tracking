import numpy as np

from marbletrack.detector import Detection
from marbletrack.fusion import FusionEngine, ReorderBuffer
from marbletrack.track import Projection


def det(s, best, k=3, cam="c", t=0.0, other=200.0):
    d2 = np.full(k, other)
    d2[best] = 0.5
    return Detection(cam, t, np.zeros(2), 100, d2, [Projection(s, 0.0, 0.001)])


def run_constant_speed(engine, speeds, t_end, fps=30, hidden=lambda i, t: False, start=None):
    start = start or [0.1 * (len(speeds) - i) for i in range(len(speeds))]
    for n in range(int(t_end * fps)):
        t = n / fps
        dets = [det(start[i] + v * t, i, len(speeds), t=t) for i, v in enumerate(speeds) if not hidden(i, t)]
        engine.process(t, dets, "c")


def test_identity_by_colour_and_overtake_ranking():
    eng = FusionEngine(["a", "b", "c"], s_finish=100, track_length=200)
    run_constant_speed(eng, [1.0, 1.5, 2.0], t_end=2.0)
    board = eng.leaderboard()
    assert [e.marble_id for e in board] == ["c", "b", "a"]
    assert all(e.status == "tracking" for e in board)
    assert abs(board[0].speed_mps - 2.0) < 0.05


def test_coasting_marble_cannot_overtake_and_is_capped_outside_blind_zones():
    eng = FusionEngine(["a", "b"], s_finish=100, track_length=200)
    # 'b' is just behind 'a' and faster, then vanishes where cameras can see.
    run_constant_speed(eng, [1.0, 1.4], t_end=1.0, start=[0.3, 0.25],
                       hidden=lambda i, t: i == 1 and t > 0.1)
    board = eng.leaderboard()
    b = next(e for e in board if e.marble_id == "b")
    assert b.status == "coasting"
    assert board[0].marble_id == "a"  # prediction alone is no evidence of an overtake
    assert b.progress_m < 0.25 + 1.4 * 0.1 + 0.3  # not extrapolated freely


def test_blind_zone_allows_coasting_to_its_exit():
    eng = FusionEngine(["a"], s_finish=100, track_length=200, static_blind_zones=[(1.0, 2.0)])
    run_constant_speed(eng, [1.0], t_end=1.6, start=[0.0], hidden=lambda i, t: 0.95 < t)
    m = eng.marbles[0]
    assert 1.4 < m.x[0] <= 2.0 + eng.coast_tolerance + 1e-9


def test_camera_timeout_creates_blind_zone_and_recovers():
    eng = FusionEngine(["a"], s_finish=100, track_length=10,
                       cameras={"c1": (0, 5), "c2": (5, 10)}, camera_timeout=0.2)
    for n in range(30):
        t = n / 30
        eng.process(t, [], "c1")
        if t < 0.3:
            eng.process(t, [], "c2")
    assert (5, 10) in eng.blind_zones
    assert any(e.kind == "camera_offline" and e.subject == "c2" for e in eng.events)
    eng.process(1.1, [], "c2")
    assert (5, 10) not in eng.blind_zones


def test_finish_time_interpolated_between_frames():
    eng = FusionEngine(["a"], s_finish=1.0, track_length=2)
    for n in range(40):
        t = n / 30
        eng.process(t, [det(0.5 + 0.7 * t, 0, k=1, t=t)], "c")
    true_t = 0.5 / 0.7
    assert abs(eng.marbles[0].finish_time - true_t) < 1e-6
    assert eng.leaderboard()[0].status == "finished"


def test_colour_ratio_test_blocks_wrong_identity():
    eng = FusionEngine(["a", "b"], s_finish=100, track_length=200)
    eng.process(0.0, [det(1.0, 0, k=2), det(2.0, 1, k=2)], "c")
    # A blob clearly coloured 'a' appears exactly where 'b' should be: only 'a' may take it,
    # and 'a' is too far away, so it is dropped rather than corrupting 'b'.
    d = det(2.0, 0, k=2, t=0.03, other=30.0)
    eng.process(0.03, [d], "c")
    assert eng.marbles[1].n_obs == 1


def test_late_measurement_is_retrodicted_not_treated_as_current():
    eng = FusionEngine(["a"], s_finish=100, track_length=200)
    for n in range(30):
        t = n / 30
        eng.process(t, [det(1.0 * t, 0, k=1, t=t)], "c")
    before = eng.marbles[0].x[0]
    late_t = 29 / 30 - 0.1
    eng.process(late_t, [det(late_t * 1.0, 0, k=1, t=late_t)], "c")
    assert abs(eng.marbles[0].x[0] - before) < 0.01  # consistent past data barely moves it


def test_reorder_buffer_releases_in_capture_order():
    buf = ReorderBuffer(latency=0.05)
    buf.push(0.02, "b", [])
    buf.push(0.01, "a", [])
    assert list(buf.pop_ready(0.05)) == []
    assert [c for _, c, _ in buf.pop_ready(0.08)] == ["a", "b"]
    buf.push(0.0, "late", [])
    assert buf.late == 1


def _noisy(s, k, best, t, rng, sigma_m=0.001, s_per_px=0.001):
    return det(s + rng.normal(0, sigma_m), best, k=k, t=t)


def test_finish_least_squares_is_accurate_and_sigma_honest():
    rng = np.random.default_rng(3)
    z = []
    for trial in range(40):
        eng = FusionEngine(["a"], s_finish=1.0, track_length=2)
        v, t_cross = rng.uniform(1.2, 1.8), rng.uniform(0.6, 0.7)
        for n in range(int(0.9 * 240)):
            t = n / 240
            eng.process(t, [_noisy(1.0 + v * (t - t_cross), 1, 0, t, rng)], "finish")
        m = eng.marbles[0]
        assert m.finish_final
        assert abs(m.finish_time - t_cross) < 1e-3
        z.append((m.finish_time - t_cross) / m.finish_sigma)
    z = np.array(z)
    assert np.mean(np.abs(z) > 3) < 0.05  # stated sigma is not optimistic
    assert 0.5 < np.std(z) < 1.6


def test_dead_heat_dither_never_flickers():
    rng = np.random.default_rng(1)
    eng = FusionEngine(["a", "b"], s_finish=100, track_length=200)
    flips, prev = 0, None
    for n in range(300):
        t = n / 30
        sa = 1.0 + 1.2 * t
        sb = sa + 0.008 * np.sin(2 * np.pi * t / 0.2)
        eng.process(t, [_noisy(sa, 2, 0, t, rng, 0.002), _noisy(sb, 2, 1, t, rng, 0.002)], "c")
        order = [e.marble_id for e in eng.leaderboard()]
        if prev is not None and order != prev and n > 3:
            flips += 1
        prev = order
    assert flips == 0


def test_multi_position_overtake_confirmed_at_once():
    eng = FusionEngine(["a", "b", "c", "d"], s_finish=100, track_length=200)
    for n in range(40):
        t = n / 30
        # 'd' starts last and is 0.9 m/s faster than a tight group of three.
        group = [det(1.00 + 1.0 * t, 0, k=4, t=t), det(0.98 + 1.0 * t, 1, k=4, t=t), det(0.96 + 1.0 * t, 2, k=4, t=t)]
        eng.process(t, group + [det(0.90 + 1.9 * t, 3, k=4, t=t)], "c")
        board = [e.marble_id for e in eng.leaderboard()]
        true_d_ahead = 0.90 + 1.9 * t > 1.00 + 1.0 * t + 0.04
        if true_d_ahead:
            assert board[0] == "d", (t, board)


def test_bounce_is_followed_within_two_frames():
    eng = FusionEngine(["a"], s_finish=100, track_length=200)
    s, errs = 1.0, []
    for n in range(60):
        t = n / 30
        v = 1.4 if t < 1.0 else (-0.8 if t < 1.15 else 1.4)
        s += v / 30
        eng.process(t, [det(s, 0, k=1, t=t)], "c")
        if t >= 1.0 + 2 / 30:
            errs.append(abs(eng.marbles[0].x[0] - s))
    assert max(errs) < 0.03
    assert eng.status(eng.marbles[0], eng.now) == "tracking"


def test_reacquisition_after_long_gap_does_not_invent_speed():
    eng = FusionEngine(["a", "b"], s_finish=100, track_length=200)
    # 'a' runs at 1.2 m/s, vanishes for 1.4 s (dead camera), reappears where it really is.
    for n in range(30):
        t = n / 30
        eng.process(t, [det(1.0 + 1.2 * t, 0, k=2, t=t), det(1.05 + 1.2 * t, 1, k=2, t=t)], "c")
    for n in range(30, 72):
        t = n / 30
        eng.process(t, [det(1.05 + 1.2 * t, 1, k=2, t=t)], "c")
    t = 72 / 30
    eng.process(t, [det(1.0 + 1.2 * t, 0, k=2, t=t), det(1.05 + 1.2 * t, 1, k=2, t=t)], "c")
    a = eng.marbles[0]
    assert abs(a.x[1] - 1.2) < 0.2  # speed from the average across the gap, not a burst
    assert eng.status(a, eng.now) == "acquiring"  # must be seen again before it can gain places
    order = [e.marble_id for e in eng.leaderboard()]
    assert order.index("b") < order.index("a")  # no overtake on a single re-sighting


def test_hidden_marble_knocked_backwards_stays_in_its_blind_zone():
    # 'a' bounces back off an obstacle at a tunnel exit and rolls back into the tunnel
    # (2.0-3.0 m). Its filter velocity is negative, but it cannot leave the tunnel
    # backwards unseen, so 'b' (crawling at 1.5 m, visible) must not "pass" it.
    eng = FusionEngine(["a", "b"], s_finish=100, track_length=200, static_blind_zones=[(2.0, 3.0)])
    for n in range(16):
        t = n / 30
        eng.process(t, [det(3.4 - 0.8 * t, 0, k=2, t=t), det(1.5 + 0.1 * t, 1, k=2, t=t)], "c")
    for n in range(16, 120):
        t = n / 30
        eng.process(t, [det(1.5 + 0.1 * t, 1, k=2, t=t)], "c")
    a = eng.marbles[0]
    assert a.x[0] >= 2.0 - 0.06
    assert [e.marble_id for e in eng.leaderboard()] == ["a", "b"]
