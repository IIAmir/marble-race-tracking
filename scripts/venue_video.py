"""A venue race on video: nine camera views with the tracker's detections, and the live leaderboard.

    python scripts/venue_video.py --seed 6 --out results/venue_race.mp4 [--cache cache] [--workers 4]

Every tile is a simulated venue camera (AIDA 1080p or the OBSBOT overhead) at
the moment shown, with each detection circled in the colour of the marble it
was identified as. The board on the right is the fusion engine's leaderboard
from all thirteen cameras, as it went out, with a check mark where the shown
rank agrees with the true order (the physics) and the true order beside it.
The tiles' detections come from running the app's detector on these frames;
the board from fusing every camera's detections (``--cache`` holds them from
``scripts/venue_suite.py``, or the race is run first).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
import zlib
from multiprocessing import get_context
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TILE = (480, 270)
PANEL_W = 480
FPS = 30


def _hex(c):
    return tuple(int(c[i:i + 2], 16) for i in (5, 3, 1))


def _tiles_for_camera(args):
    """Render this camera through the race, detect, draw: a list of JPEG tiles (one per video frame)."""
    seed, sc, spec, times, ids, colours, cache = args
    from marbletrack.detector import ColorDetector
    from marbletrack.venue.camera import VenueCamera
    from marbletrack.venue.run import calibrate, prepare
    from marbletrack.venue.scene import build_scene
    from marbletrack.venue.shading import ShadowMap
    from marbletrack_app.engine import _min_radius_px, _work_scale

    race, _ = prepare(seed, sc, cache)
    course = race.course
    scene = build_scene(course, seed)
    cam = VenueCamera(spec, scene, ShadowMap(scene), course, seed=zlib.crc32(spec.cam_id.encode()) % 10000)
    rng = np.random.default_rng(seed * 7919 + zlib.crc32(spec.cam_id.encode()))
    _, _, _, calib = calibrate(cam, course, race.balls, race.laps, rng)
    W, H = cam.size
    det = ColorDetector(calib, work_scale=_work_scale(W, H, _min_radius_px(calib)), readout=spec.optics.readout)
    out = []
    k = W / TILE[0]
    label = {"overhead": "OBSBOT overhead 4K", "finish": "finish camera"}.get(spec.role, spec.cam_id)
    for t in times:
        frame = cam.render(race, t)
        dets = det.detect(frame, t)
        img = cv2.resize(frame, TILE, interpolation=cv2.INTER_AREA)
        for d in dets:
            j = int(np.argmin(d.color_d2))
            x, y = int(d.xy[0] / k), int(d.xy[1] / k)
            r = max(5, int(np.sqrt(d.area / np.pi) / max(det.work_scale, 1e-6) / k * 1.6))
            cv2.circle(img, (x, y), r + 1, (15, 15, 15), 2, cv2.LINE_AA)
            cv2.circle(img, (x, y), r, colours[j], 2, cv2.LINE_AA)
            cv2.putText(img, ids[j][1:], (x + r + 2, y - r), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (255, 255, 255), 1, cv2.LINE_AA)
        cv2.rectangle(img, (0, 0), (TILE[0], 18), (20, 20, 20), -1)
        cv2.putText(img, label, (6, 13), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (230, 230, 230), 1, cv2.LINE_AA)
        out.append(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes())
    return spec.cam_id, out


def _boards(seed, sc, cache):
    """The fused leaderboard through the race (all cameras), with the true positions."""
    import pickle

    from marbletrack.venue import run as R

    path = Path(cache) / f"venue_dets_{seed}_{sc.name}.pkl"
    if not path.exists():
        R.run_venue_race(seed, sc, cache_dir=str(cache))
    saved = pickle.load(open(path, "rb"))
    saved["sc"] = sc  # the current rig (the cache may hold detections of a bigger one)
    race, _ = R.prepare(seed, saved["sc"], str(cache))
    timeline = []
    from marbletrack import scoring

    orig = scoring.Scorer.evaluate

    def evaluate(self, engine, t):
        board = orig(self, engine, t)
        timeline.append((t, board, self.truth_progress(t)))
        return board

    scoring.Scorer.evaluate = evaluate
    try:
        rep = R.fuse_and_score(race, [r for r in saved["results"] if r["ok"] and R.in_rig(r["cam_id"], sc)], saved["sc"])
    finally:
        scoring.Scorer.evaluate = orig
    return race, timeline, rep


def _panel(h, board, truth, t, ids, names, colours, race, rep):
    p = np.full((h, PANEL_W, 3), (26, 24, 22), np.uint8)
    cv2.putText(p, "MarbleTrack  live leaderboard", (18, 36), cv2.FONT_HERSHEY_DUPLEX, 0.75, (240, 240, 240), 1, cv2.LINE_AA)
    cv2.putText(p, f"race clock {t:6.2f} s", (18, 66), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (170, 200, 170), 1, cv2.LINE_AA)
    cv2.putText(p, "rank  marble          gap / time      true", (18, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (150, 150, 150), 1, cv2.LINE_AA)
    true_rank = {ids[i]: k for k, i in enumerate(np.argsort(-truth))}
    fin = race.finish_times
    for i, e in enumerate(board):
        y = 132 + i * 40
        j = ids.index(e.marble_id)
        live = e.status in ("tracking", "finished", "acquiring")
        col = (235, 235, 235) if live else (120, 120, 120)
        cv2.circle(p, (40, y - 6), 11, colours[j], -1, cv2.LINE_AA)
        cv2.putText(p, f"{e.rank:>2}", (6, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, col, 1, cv2.LINE_AA)
        cv2.putText(p, names[j], (60, y), cv2.FONT_HERSHEY_SIMPLEX, 0.58, col, 1, cv2.LINE_AA)
        info = f"{e.finish_time:7.3f} s" if e.finish_time is not None else (
            {"coasting": "hidden", "lost": "lost", "unseen": "-"}.get(e.status) or ("leader" if i == 0 else f"+{e.gap_m:5.2f} m"))
        cv2.putText(p, info, (215, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, col, 1, cv2.LINE_AA)
        if e.status != "unseen":
            tr = true_rank[e.marble_id]
            ok = tr == i or (e.finish_time is not None and np.isfinite(fin[j]))
            cv2.putText(p, f"{tr + 1:>2}", (390, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (150, 150, 150), 1, cv2.LINE_AA)
            if ok:
                cv2.putText(p, "ok", (430, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (110, 210, 120), 1, cv2.LINE_AA)
    y0 = 132 + 10 * 40 + 20
    lines = [f"whole race: ranks right {rep['rank_accuracy'] * 100:.2f} % of the time",
             f"phantom overtakes {rep['phantom_overtakes']}, final order {'correct' if rep['final_order_correct'] else 'WRONG'}",
             f"finish times within {rep['finish_err_ms_max']:.1f} ms",
             "grey rows: not in view of any camera right now",
             "(a gap in coverage); they hold their place",
             "true = the true rank from the physics; ok = board agrees",
             "simulated venue: 12 AIDA 1080p60 + OBSBOT 4K,",
             "NDI timing, the app's own calibration and tracker"]
    for k, s in enumerate(lines):
        cv2.putText(p, s, (18, y0 + k * 26), cv2.FONT_HERSHEY_SIMPLEX, 0.47, (190, 190, 190), 1, cv2.LINE_AA)
    return p


def main():
    from marbletrack.venue.rig import place_cameras
    from marbletrack.venue.run import SCENARIOS, prepare
    from marbletrack.venue.scene import build_scene

    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=6)
    ap.add_argument("--scenario", default="venue")
    ap.add_argument("--cache", default="cache")
    ap.add_argument("--out", default="results/venue_race.mp4")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--duration", type=float, default=None)
    a = ap.parse_args()
    sc = SCENARIOS[a.scenario]
    t0 = time.time()
    race, timeline, rep = _boards(a.seed, sc, a.cache)
    ids = [f"b{b.number}" for b in race.balls]
    names = [b.name for b in race.balls]
    colours = [tuple(int(v) for v in b.body_bgr) for b in race.balls]
    end = race.duration + 1.0 if a.duration is None else a.start + a.duration
    times = np.arange(a.start, end, 1.0 / FPS)
    scene = build_scene(race.course, a.seed)
    specs, _ = place_cameras(race.course, scene, a.seed)
    # the cameras with the most racing in view, plus start, finish and overhead
    must = [s for s in specs if s.cam_id in ("cam-start", "cam-finish", "obsbot")]
    rest = sorted((s for s in specs if s not in must), key=lambda s: -(s.s_hint[1] - s.s_hint[0]) if s.s_hint else 0)[:9 - len(must)]
    chosen = sorted(must + rest, key=lambda s: (s.s_hint or (0, 0))[0] if s.role != "overhead" else 99)
    print(f"{len(times)} frames from {len(chosen)} cameras: {[s.cam_id for s in chosen]}", flush=True)
    jobs = [(a.seed, sc, s, times, ids, colours, a.cache) for s in chosen]
    tiles = {}
    with get_context("spawn").Pool(a.workers) as pool:
        for cam_id, t in pool.imap_unordered(_tiles_for_camera, jobs):
            tiles[cam_id] = t
            print(f"  {cam_id} done ({time.time() - t0:.0f} s)", flush=True)
    order = [s.cam_id for s in chosen]
    rows = 3
    H = TILE[1] * rows
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    W = TILE[0] * 3 + PANEL_W
    proc = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
                             "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "22",
                             "-preset", "medium", "-movflags", "+faststart", a.out], stdin=subprocess.PIPE)
    bt = np.array([b[0] for b in timeline])
    for f, t in enumerate(times):
        grid = np.zeros((H, TILE[0] * 3, 3), np.uint8)
        for k, cid in enumerate(order):
            img = cv2.imdecode(np.frombuffer(tiles[cid][f], np.uint8), cv2.IMREAD_COLOR)
            r, c = divmod(k, 3)
            grid[r * TILE[1]:(r + 1) * TILE[1], c * TILE[0]:(c + 1) * TILE[0]] = img
        i = int(np.clip(np.searchsorted(bt, t + 0.08), 0, len(bt) - 1))  # the board lags the pictures by the reorder delay
        _, board, truth = timeline[i]
        frame = np.hstack([grid, _panel(H, board, race.progress(t), t, ids, names, colours, race, rep)])
        proc.stdin.write(frame.tobytes())
        if f == len(times) // 2:
            cv2.imwrite(str(Path(a.out).with_suffix(".jpg")), frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
    proc.stdin.close()
    proc.wait()
    print(f"wrote {a.out} in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
