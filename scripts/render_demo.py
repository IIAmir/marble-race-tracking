"""Render an annotated demo video (and replay data) from a simulated race.

    python scripts/render_demo.py --mode single --out results/demo_single.mp4
    python scripts/render_demo.py --mode multi  --out results/demo_multi.mp4

Left: what the camera(s) see, with each detection labelled by identity.
Right: the live leaderboard produced by the fusion engine, with a check mark
when the rank agrees with ground truth.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from marbletrack.race import SCENARIOS, run_race  # noqa: E402
from marbletrack.sim import DEFAULT_MARBLES  # noqa: E402

IDS = [m.marble_id for m in DEFAULT_MARBLES]
PANEL_W = 340


def annotate(frame, dets, scale):
    img = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    for d in dets:
        k = int(np.argmin(d.color_d2))
        x, y = (d.xy * scale).astype(int)
        cv2.circle(img, (x, y), max(6, int(9 * scale / 0.5)), (255, 255, 255), 1, cv2.LINE_AA)
        cv2.putText(img, IDS[k], (x + 8, y - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)
    return img


class TrackMap:
    """Top-down schematic: fused estimates (dots) vs ground truth (rings), camera coverage."""

    def __init__(self, track, cam_ranges, w, h, tunnels):
        self.track, self.cam_ranges, self.w, self.h, self.tunnels = track, cam_ranges, w, h, tunnels
        pts = track.points
        lo, hi = pts.min(0), pts.max(0)
        self.k = min((w - 30) / (hi[0] - lo[0]), (h - 30) / (hi[1] - lo[1]))
        self.off = np.array([(w - self.k * (hi[0] - lo[0])) / 2, (h - self.k * (hi[1] - lo[1])) / 2]) - lo * self.k

    def px(self, s):
        xy, _ = self.track.point_at(np.atleast_1d(s))
        return np.round(xy * self.k + self.off).astype(np.int32)

    def _line(self, img, a, b, col, th):
        cv2.polylines(img, [self.px(np.linspace(a, b, max(2, int((b - a) / 0.02))))], False, col, th, cv2.LINE_AA)

    def draw(self, est, truth, status, cams_ok):
        img = np.full((self.h, self.w, 3), 18, np.uint8)
        L = self.track.s_range[1]
        self._line(img, 0, L, (70, 70, 70), 6)
        for (a, b), ok in zip(self.cam_ranges, cams_ok):
            self._line(img, a, b, (70, 150, 70) if ok else (60, 60, 220), 2)
        for a, b in self.tunnels:
            self._line(img, a, b, (40, 70, 110), 8)
        for i, m in enumerate(DEFAULT_MARBLES):
            cv2.circle(img, tuple(self.px(truth[i])[0]), 7, (200, 200, 200), 1, cv2.LINE_AA)
            if status[i] != "unseen":
                c = tuple(int(v) for v in m.bgr)
                cv2.circle(img, tuple(self.px(est[i])[0]), 4, c, -1 if status[i] != "coasting" else 1, cv2.LINE_AA)
        cv2.putText(img, "fused track map: dot = estimate, ring = truth", (8, self.h - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (160, 160, 160), 1, cv2.LINE_AA)
        return img


def panel(h, board, truth, t, cams_down, title):
    p = np.full((h, PANEL_W, 3), 24, np.uint8)
    cv2.putText(p, title, (12, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (230, 230, 230), 1, cv2.LINE_AA)
    cv2.putText(p, f"t = {t:5.2f} s", (12, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (160, 160, 160), 1, cv2.LINE_AA)
    order_truth = sorted(range(len(IDS)), key=lambda i: -truth[i])
    for r, e in enumerate(board):
        y = 84 + r * 30
        i = IDS.index(e.marble_id)
        cv2.circle(p, (24, y - 5), 8, DEFAULT_MARBLES[i].bgr, -1, cv2.LINE_AA)
        txt = f"{e.rank:2d} {e.marble_id:<7}"
        info = f"{e.finish_time:6.3f}s" if e.finish_time is not None else f"+{e.gap_m:5.2f}m"
        col = (230, 230, 230) if e.status in ("tracking", "finished") else (130, 130, 130)
        cv2.putText(p, txt, (40, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, col, 1, cv2.LINE_AA)
        cv2.putText(p, info, (170, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1, cv2.LINE_AA)
        tag = "PHOTO" if e.photo_finish else {"coasting": "hidden", "lost": "lost"}.get(e.status, "")
        if tag:
            cv2.putText(p, tag, (262, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (80, 200, 255), 1, cv2.LINE_AA)
        elif e.status != "finished" and abs(order_truth.index(i) - r) == 0:
            cv2.putText(p, "ok", (270, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (120, 220, 120), 1, cv2.LINE_AA)
    if cams_down:
        cv2.putText(p, "OFFLINE: " + ",".join(cams_down), (12, h - 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                    (60, 60, 255), 1, cv2.LINE_AA)
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["single", "multi"], default="single")
    ap.add_argument("--scenario", choices=list(SCENARIOS), default="nominal")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="results/demo.mp4")
    ap.add_argument("--replay", default=None, help="also write replay JSON here")
    ap.add_argument("--stills", default=None, help="directory for a few annotated PNG stills")
    a = ap.parse_args()

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    proc = None
    latest = {}
    still_times = [2.0, 4.0, 9.7]
    from marbletrack.sim import RaceConfig, RaceSimulator, serpentine_track
    cfg = SCENARIOS[a.scenario].race
    track = serpentine_track(cfg)
    sim = RaceSimulator(track, DEFAULT_MARBLES, cfg, seed=a.seed)
    tmap = [None]

    def emit(img):
        nonlocal proc
        if proc is None:
            h, w = img.shape[:2]
            proc = subprocess.Popen(
                ["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}",
                 "-r", "30", "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "26",
                 "-preset", "medium", "-movflags", "+faststart", a.out], stdin=subprocess.PIPE)
        proc.stdin.write(img.tobytes())

    def on_frame(cam, frame, dets, engine, t):
        if cam.cam_id == "finish":
            return
        latest[cam.cam_id] = (frame, dets)
        trigger = "master" if a.mode == "single" else "cam01"
        if cam.cam_id != trigger:
            return
        if a.mode == "single":
            view = annotate(frame, dets, 0.5)
        else:
            tiles = []
            for k in range(14):
                cid = f"cam{k + 1:02d}"
                f, d = latest.get(cid, (np.zeros((480, 640, 3), np.uint8), []))
                tile = annotate(f, d, 0.5)
                down = not engine.camera_status().get(cid, True)
                cv2.putText(tile, cid + (" OFFLINE" if down else ""), (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                            (60, 60, 255) if down else (220, 220, 220), 1, cv2.LINE_AA)
                tiles.append(tile)
            if tmap[0] is None:
                ranges = [engine.cameras[f"cam{k + 1:02d}"] for k in range(14)]
                tmap[0] = TrackMap(track, ranges, 2 * tiles[0].shape[1], tiles[0].shape[0], cfg.tunnels)
            st = {m.marble_id: engine.status(m, engine.now) for m in engine.marbles}
            mp_img = tmap[0].draw([m.x[0] for m in engine.marbles], sim.progress(engine.now),
                                  [st[i] for i in IDS], [engine.camera_status()[f"cam{k + 1:02d}"] for k in range(14)])
            tiles += [mp_img[:, : tiles[0].shape[1]], mp_img[:, tiles[0].shape[1]:]]
            view = np.vstack([np.hstack(tiles[r * 4:(r + 1) * 4]) for r in range(4)])
        board = engine.leaderboard()
        down = [c for c, ok in engine.camera_status().items() if not ok]
        title = "1 master camera" if a.mode == "single" else "14 cameras"
        img = np.hstack([view, panel(view.shape[0], board, sim.progress(engine.now), engine.now, down, title)])
        emit(img)
        if a.stills and any(abs(t - s) < 1 / 60 for s in still_times):
            Path(a.stills).mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(Path(a.stills) / f"{a.mode}_{a.scenario}_t{t:.1f}.png"), img)

    r = run_race(a.mode, seed=a.seed, scenario=a.scenario, record=bool(a.replay), on_frame=on_frame)
    proc.stdin.close()
    proc.wait()
    if a.replay:
        Path(a.replay).write_text(json.dumps(r.replay, separators=(",", ":")))
    print(a.out, "order", "OK" if r.final_order_correct else "WRONG", "rank", round(r.rank_accuracy, 4))


if __name__ == "__main__":
    main()
