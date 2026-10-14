"""Progress accuracy of the 14 angled cameras, with and without the oblique projection.

    python scripts/angled_accuracy.py --out results/angled_projection.json

Places one marble at 6 positions x 5 lateral offsets in every camera's section
(far lane, centre, near lane; walls hide part of the marble in some views) and
compares measured progress with the truth.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from marbletrack.detector import ColorDetector  # noqa: E402
from marbletrack.sim import DEFAULT_MARBLES, RaceConfig, RaceSimulator, serpentine_track  # noqa: E402
from marbletrack.sim3d import angled_camera_rig  # noqa: E402
from marbletrack.track import Polyline  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results/angled_projection.json")
    a = ap.parse_args()
    cfg = RaceConfig(tunnels=())
    track = serpentine_track(cfg)
    sim = RaceSimulator(track, DEFAULT_MARBLES, cfg, seed=1)
    cams = angled_camera_rig(track, cfg, seed=1)
    res = {"oblique": [], "nearest_pixel": [], "missed": 0, "placements": 0, "per_camera": []}
    for cam in cams:
        calib = cam.calibration(DEFAULT_MARBLES, sim)
        det = ColorDetector(calib)
        cl = calib.centerline
        naive = Polyline(cl.points, cl.s)  # same waypoints, no cross-track directions
        lo, hi = cam.s_range
        errs = []
        for s in np.linspace(lo + 0.08, hi - 0.08, 6):
            for lat in (-0.013, -0.006, 0.0, 0.006, 0.013):
                xy, tan = track.point_at(np.array([s]))
                pos = xy[0] + lat * np.array([-tan[0, 1], tan[0, 0]])
                res["placements"] += 1
                d = det.detect(cam.render([pos], [True], [DEFAULT_MARBLES[3].bgr]), 0.0)
                if not d:
                    res["missed"] += 1
                    continue
                e = abs(d[0].candidates[0].s - s) * 1000
                errs.append(e)
                res["oblique"].append(e)
                nc = naive.project(d[0].xy, 1e9)
                res["nearest_pixel"].append(abs(nc[0].s - s) * 1000)
        res["per_camera"].append({"cam": cam.cam_id, "elevation_deg": round(float(np.degrees(np.arcsin(cam.view_dir[2]))), 1),
                                  "distance_m": round(cam.distance, 2), "max_mm": round(max(errs), 2)})
    for k in ("oblique", "nearest_pixel"):
        v = np.array(res[k])
        res[k] = {"median_mm": float(np.median(v)), "p95_mm": float(np.percentile(v, 95)), "max_mm": float(v.max())}
    Path(a.out).write_text(json.dumps(res, indent=1))
    print(json.dumps({k: res[k] for k in ("oblique", "nearest_pixel", "missed", "placements")}))


if __name__ == "__main__":
    main()
