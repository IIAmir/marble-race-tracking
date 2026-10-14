"""Scripted extreme race situations ("F1-grade" edge cases) and their scoring.

Each case builds exact ground-truth trajectories, runs the full pipeline
(render -> detect -> fuse -> rank) and scores the specific behaviour it targets:

* ``photo_finish``   pairs crossing the line side by side 0.1-8 ms apart
* ``corner_duel``    inside/outside micro-overtake through a 180-degree corner,
                     plus a pair dithering +/-8 mm around a dead heat
* ``backward_slip``  marbles bouncing backwards 5-15 cm (straight, corner, and
                     back over the finish line) while rivals pass them
* ``fast_handoff``   3-3.5 m/s through 14 cameras with 3 cm overlap, 30 fps and
                     +/-1 cm calibration disagreement between neighbouring cameras
"""
from __future__ import annotations

import numpy as np

from .race import RaceReport, Scenario, run_race
from .sim import RaceConfig, serpentine_track

DT = 1e-3


def corners(track, min_turn=np.pi / 2):
    """(s_start, s_end, direction, radius) of every curved section of a polyline track."""
    pts = track.points
    tang = np.diff(pts, axis=0)
    ang = np.unwrap(np.arctan2(tang[:, 1], tang[:, 0]))
    dang = np.diff(ang)
    curved = np.abs(dang) > 1e-3
    out, i = [], 0
    while i < len(curved):
        if not curved[i]:
            i += 1
            continue
        j = i
        while j < len(curved) and curved[j]:
            j += 1
        turn = ang[j] - ang[i]
        s0, s1 = track.s[i + 1], track.s[j]
        if abs(turn) >= min_turn:
            out.append((float(s0), float(s1), "left" if turn > 0 else "right", float((s1 - s0) / abs(turn))))
        i = j
    return out


def _integrate(v_of_t, s0, t_end):
    t = np.arange(0.0, t_end, DT)
    v = np.array([v_of_t(x) for x in t])
    return t, s0 + np.r_[0.0, np.cumsum(v[:-1]) * DT]


# -- scripts: (track, cfg, rng) -> (times, traj (T,K), lateral (T,K)) ----------

def photo_finish_script(gaps_ms=None):
    def script(track, cfg, rng):
        s_f = track.s_range[1] - cfg.finish_before_end
        gaps = np.asarray(gaps_ms if gaps_ms is not None else rng.choice([0.1, 0.25, 0.5, 1, 2, 3, 5, 8], 5, False))
        t = np.arange(0.0, 3.6, DT)
        traj, lat = [], []
        for p, g in enumerate(gaps):
            t_lead = 1.7 + 0.32 * p + rng.uniform(-0.03, 0.03)
            first = rng.integers(2)  # which of the pair crosses first
            for k in range(2):
                t_cross = t_lead + (g * 1e-3 if k != first else 0.0)
                v = rng.uniform(1.3, 1.7)
                amp = rng.uniform(0.0, 0.25) * v / 12.0  # speed wobble, crossing stays exact
                traj.append(s_f + v * (t - t_cross) + amp * np.sin(12.0 * (t - t_cross)))
                lat.append(np.full_like(t, 0.0135 if k == 0 else -0.0135))  # side by side
        return t, np.clip(np.array(traj).T, 0.0, track.s_range[1]), np.array(lat).T
    script.gaps_ms = gaps_ms
    return script


def corner_duel_script(track, cfg, rng):
    c0, c1, *_ = corners(track)[1]  # second 180-degree turn
    sc = (c0 + c1) / 2
    t = np.arange(0.0, 5.0, DT)
    traj, lat = [], []
    # Pair 1 (A outside, B inside): B goes from 3 cm behind to 3 cm ahead through the corner.
    va = 1.2
    t_mid = 2.5
    s_a = sc + va * (t - t_mid)
    g = np.clip((t - t_mid) / 0.5, -1, 1) * 0.03
    traj += [s_a, s_a + g]
    lat += [np.full_like(t, 0.0135), np.full_like(t, -0.0135)]
    # Pair 2 (C, D): dead heat dithering +/-8 mm (below the half-diameter margin) in the same corner.
    s_c = sc - 0.25 + va * (t - t_mid)
    traj += [s_c, s_c + 0.008 * np.sin(2 * np.pi * t / 0.2)]
    lat += [np.full_like(t, 0.0135), np.full_like(t, -0.0135)]
    # Six more marbles strung out behind, normal racing.
    for k in range(6):
        v = rng.uniform(1.1, 1.3)
        traj.append(sc - 0.6 - 0.15 * k + v * (t - t_mid))
        lat.append(0.01 * np.sin(2 * np.pi * t / rng.uniform(0.8, 2.0) + rng.uniform(0, 6)))
    traj = np.clip(np.array(traj).T, 0.0, track.s_range[1])
    return t, traj, np.array(lat).T


def backward_slip_script(track, cfg, rng):
    """Marbles that bounce backwards, some in corners, one back over the finish line."""
    L = track.s_range[1]
    s_f = L - cfg.finish_before_end
    turns = corners(track)
    t_end = 4.5
    traj, lat = [], []

    def profile(v0, slips):
        def v(t):
            out = v0
            for t_s, back in slips:  # back: peak reverse speed (m/s)
                u = t - t_s
                if 0 <= u < 0.03:
                    out = v0 + (-back - v0) * u / 0.03
                elif 0.03 <= u < 0.15:
                    out = -back
                elif 0.15 <= u < 0.35:
                    out = -back + (v0 + back) * (u - 0.15) / 0.2
            return out
        return v

    # Slip victims (A) each followed closely by a rival (B) who passes during the slip.
    starts = [5.0, turns[2][0] - 1.2, s_f - 2.4, 9.3]  # straight, corner, finish line, straight
    for k, s0 in enumerate(starts):
        va = rng.uniform(1.3, 1.5)
        slip_t = 1.0 + 0.3 * k if k != 2 else (2.4 + 0.01) / va  # k=2: slips right after the line
        back = rng.uniform(0.5, 1.0)
        t, sa = _integrate(profile(va, [(slip_t, back)]), s0, t_end)
        _, sb = _integrate(lambda x, va=va: va, s0 - 0.05, t_end)
        traj += [sa, sb]
        lat += [np.full_like(t, 0.0135), np.full_like(t, -0.0135)]
    for k in range(2):  # two clean runners
        v = rng.uniform(1.2, 1.4)
        t, s = _integrate(lambda x, v=v: v, 2.0 + 0.4 * k, t_end)
        traj.append(s)
        lat.append(np.zeros_like(t))
    traj = np.clip(np.array(traj).T, 0.0, L)
    return t, traj, np.array(lat).T


EDGE_CASES = {
    "photo_finish": Scenario(
        "photo_finish", "Side-by-side finishes 0.1-8 ms apart (finish camera 240 fps).",
        RaceConfig(tunnels=()), script=photo_finish_script()),
    "corner_duel": Scenario(
        "corner_duel", "Inside/outside micro-overtake in a 180-degree corner, plus a +/-8 mm dead-heat dither.",
        RaceConfig(tunnels=()), script=corner_duel_script),
    "backward_slip": Scenario(
        "backward_slip", "Marbles bounce back 5-15 cm (straight, corner, finish line) while rivals pass.",
        RaceConfig(tunnels=()), script=backward_slip_script),
    "fast_handoff": Scenario(
        "fast_handoff", "3.0-3.5 m/s through 14 cameras, 3 cm overlap, 30 fps, +/-1 cm calibration bias.",
        RaceConfig(speed_range=(3.0, 3.5), wobble=0.15), modes=("multi",), overlap=0.03, calib_bias=0.01),
}


# -- scoring --------------------------------------------------------------------

def _pair_flips(order_hist, a, b):
    flips, prev = 0, None
    for order in order_hist:
        cur = order.index(a) < order.index(b)
        if prev is not None and cur != prev:
            flips += 1
        prev = cur
    return flips


def score_photo_finish(r: RaceReport) -> dict:
    rp = r.replay
    tf, ef, sig, photo = (np.array(rp[k], dtype=float) for k in ("truth_finish", "est_finish", "finish_sigma", "photo"))
    pairs = []
    for p in range(len(tf) // 2):
        a, b = 2 * p, 2 * p + 1
        gap = abs(tf[a] - tf[b]) * 1e3
        right = (ef[a] < ef[b]) == (tf[a] < tf[b])
        flagged = bool(photo[a] and photo[b])
        pairs.append({"gap_ms": round(gap, 3), "correct": bool(right), "flagged": flagged,
                      "outcome": "correct" if right and not flagged else
                      "flagged" if flagged else "WRONG_UNFLAGGED"})
    z = (ef - tf) / np.maximum(sig, 1e-9)
    return {"pairs": pairs, "finish_err_ms": [round(float(v), 3) for v in np.abs(ef - tf) * 1e3],
            "sigma_ms": [round(float(v), 3) for v in sig * 1e3], "z": [round(float(v), 2) for v in z]}


def score_corner_duel(r: RaceReport) -> dict:
    rp = r.replay
    order = rp["order"]
    truth = np.array(rp["truth"])
    t = np.array(rp["t"])
    g = truth[:, 1] - truth[:, 0]
    margin = 0.0125
    shown_b_ahead = np.array([o.index(1) < o.index(0) for o in order])
    t_true = t[np.argmax(g > margin)]  # B leads by more than the margin
    t_shown = t[np.argmax(shown_b_ahead)] if shown_b_ahead.any() else np.inf
    # Count from the moment both marbles of a pair are on the board (first placement is not a flip).
    def settled(a, b):
        k = next(k for k, st in enumerate(rp["status"]) if st[a] != "unseen" and st[b] != "unseen")
        return order[k:]
    return {"overtake_flips": _pair_flips(settled(0, 1), 0, 1), "overtake_expected_flips": 1,
            "overtake_display_latency_ms": round(float(t_shown - t_true) * 1e3, 1),
            "dither_flips": _pair_flips(settled(2, 3), 2, 3), "dither_expected_flips": 0}


def score_backward_slip(r: RaceReport) -> dict:
    rp = r.replay
    est, truth, status = np.array(rp["est"]), np.array(rp["truth"]), rp["status"]
    tracked = np.array([[s == "tracking" for s in row] for row in status])
    err = np.abs(est - truth)[tracked]
    lost = sum(row.count("lost") for row in status)
    tf, ef = np.array(rp["truth_finish"], float), np.array([np.inf if v is None else v for v in rp["est_finish"]])
    return {"max_pos_err_cm": round(float(err.max()) * 100, 2), "rms_pos_err_cm": round(float(np.sqrt((err**2).mean())) * 100, 2),
            "lost_marble_instants": lost,
            "finish_first_crossing_err_ms": round(float(abs(ef[4] - tf[4])) * 1e3, 2)}


def score_fast_handoff(r: RaceReport) -> dict:
    rp = r.replay
    cams = np.array(rp["obs_cam"])
    handoffs = int(sum(((cams[1:] != cams[:-1]) & (cams[1:] >= 0) & (cams[:-1] >= 0)).sum(axis=0)))
    v = np.array(rp["v"])
    st = np.array([[s == "tracking" for s in row] for row in rp["status"]])
    dv = np.abs(np.diff(v, axis=0))[st[1:] & st[:-1]]
    return {"handoffs": handoffs, "speed_jump_p99_mps": round(float(np.percentile(dv, 99)), 3)}


SCORERS = {"photo_finish": score_photo_finish, "corner_duel": score_corner_duel,
           "backward_slip": score_backward_slip, "fast_handoff": score_fast_handoff}


def run_edge_case(name: str, mode: str = "multi", seed: int = 0) -> dict:
    r = run_race(mode, seed=seed, scenario=EDGE_CASES[name], record=True)
    out = {"case": name, "mode": mode, "seed": seed, "view": r.view, "rank_accuracy": r.rank_accuracy,
           "rank_accuracy_latency": r.rank_accuracy_latency, "phantom_overtakes": r.phantom_overtakes,
           "overtakes_shown": r.overtakes_shown,
           "id_errors": r.id_errors, "pos_err_cm_rms": r.pos_err_cm_rms,
           "final_order_correct": r.final_order_correct}
    out.update(SCORERS[name](r))
    return out


if __name__ == "__main__":  # quick look: python -m marbletrack.edgecases
    track = serpentine_track(RaceConfig())
    print("corners:", corners(track))
    for name, sc in EDGE_CASES.items():
        for mode in sc.modes:
            print(run_edge_case(name, mode))
