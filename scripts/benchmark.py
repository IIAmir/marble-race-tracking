"""Per-frame compute cost of the edge detector at production camera resolutions.

    python scripts/benchmark.py --out results/benchmark.json

Single thread, Python + OpenCV, 10 marbles in view. Frames come from the
simulator at the resolution of each recommended camera.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from marbletrack.detector import ColorDetector  # noqa: E402
from marbletrack.fusion import FusionEngine  # noqa: E402
from marbletrack.sim import (DEFAULT_MARBLES, RaceConfig, RaceSimulator, finish_camera,  # noqa: E402
                             multi_camera_rig, serpentine_track, single_camera_rig)

CASES = [
    # name, camera, fps the camera runs at in production, detector options.
    # Processing resolutions match what the stress suite validated: segment cameras ran at
    # 640x480 there (below half of 1440x1080), the finish camera at 480x240.
    ("Segment camera 1440x1080 (IMX273), processed at 720x540", "segment", 60, {"work_scale": 0.5}),
    ("Segment camera 1440x1080, full resolution", "segment", 60, {}),
    ("Overhead camera 1920x1200 (IMX392), half-res foreground", "master", 51, {"fg_scale": 0.5}),
    ("Finish camera ROI 720x240, processed at 480x160", "finish", 240, {"work_scale": 2 / 3}),
]


def _place(cam, track, colors):
    lo, hi = cam.s_range
    s = np.linspace(lo + 0.04, hi - 0.04, len(colors))
    xy, _ = track.point_at(s)
    return xy, np.ones(len(s), bool), colors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results/benchmark.json")
    ap.add_argument("--frames", type=int, default=60)
    a = ap.parse_args()
    cv2.setNumThreads(1)
    cfg = RaceConfig(tunnels=())
    track = serpentine_track(cfg)
    sim = RaceSimulator(track, DEFAULT_MARBLES, cfg, seed=0)
    colors = [m.bgr for m in DEFAULT_MARBLES]
    s_f = track.s_range[1] - cfg.finish_before_end
    cams = {
        "segment": multi_camera_rig(track, cfg, size=(1440, 1080))[1],
        "master": single_camera_rig(track, cfg, size=(1920, 1200))[0],
        "finish": finish_camera(track, cfg, s_f, size=(720, 240)),
    }
    out = {"machine": {"cpu": platform.processor() or platform.machine(), "cores": os.cpu_count(),
                       "python": platform.python_version(), "opencv": cv2.__version__}, "cases": []}
    for name, which, fps, opts in CASES:
        cam = cams[which]
        det = ColorDetector(cam.calibration(DEFAULT_MARBLES, sim), **opts)
        assert len(det.detect(cam.render(*_place(cam, track, colors)), 0.0)) >= 9, name  # detection still works
        # Place all ten marbles inside this camera's section, spread along it.
        lo, hi = cam.s_range
        s = np.linspace(lo + 0.04, hi - 0.04, len(colors))
        xy, tan = track.point_at(s)
        normal = np.c_[-tan[:, 1], tan[:, 0]]
        frames = [cam.render(xy + normal * (0.01 * np.sin(k + np.arange(len(s))))[:, None], np.ones(len(s), bool), colors)
                  for k in range(5)]
        for f in frames:  # warm-up
            det.detect(f, 0.0)
        times = []
        for k in range(a.frames):
            t0 = time.perf_counter()
            dets = det.detect(frames[k % len(frames)], 0.0)
            times.append((time.perf_counter() - t0) * 1e3)
        ms = float(np.median(times))
        out["cases"].append({"name": name, "width": cam.size[0], "height": cam.size[1], "fps": fps,
                             "ms_median": ms, "ms_p95": float(np.percentile(times, 95)),
                             "budget_ms": 1000.0 / fps, "cores_needed_at_fps": ms * fps / 1000.0,
                             "detections": len(dets)})
        print(out["cases"][-1], flush=True)
    # Fusion cost: one camera frame with 10 detections against 10 marbles.
    eng = FusionEngine([m.marble_id for m in DEFAULT_MARBLES], s_f, track.s_range[1])
    det = ColorDetector(cams["master"].calibration(DEFAULT_MARBLES, sim))
    xy, _ = track.point_at(np.linspace(1.0, 3.0, 10))
    dets = det.detect(cams["master"].render(xy, np.ones(10, bool), colors), 0.0)
    times = []
    for k in range(400):
        for d in dets:
            d.t = k / 400
        t0 = time.perf_counter()
        eng.process(k / 400, dets, "master")
        eng.leaderboard()
        times.append((time.perf_counter() - t0) * 1e3)
    out["fusion_ms_per_frame_median"] = float(np.median(times[50:]))
    seg, mas = out["cases"][0], out["cases"][2]
    out["sizing"] = {
        "mode_b_cores_14_cams_at_60fps": 14 * seg["cores_needed_at_fps"],
        "mode_a_cores_master_at_51fps": mas["cores_needed_at_fps"],
        "finish_cores_at_240fps": out["cases"][3]["cores_needed_at_fps"],
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=1))
    print(json.dumps(out["sizing"]), "fusion ms", out["fusion_ms_per_frame_median"])


if __name__ == "__main__":
    main()
