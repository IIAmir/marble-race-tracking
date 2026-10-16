"""Venue races: procedural circuits filmed by the 13 AIDA cameras and the OBSBOT, tracked by the app's code.

    python scripts/venue_suite.py --seeds 1 2 3 --scenarios venue venue_laps --out results/venue.json
    python scripts/venue_suite.py --stills 1          # contact sheet of what the cameras see

Each race renders every camera frame (about 2-4 minutes per camera on one core),
so the suite takes a while; ``--cache`` keeps physics and detections between runs.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from marbletrack.venue.run import SCENARIOS, run_venue_race  # noqa: E402

KEYS = ["rank_accuracy", "rank_accuracy_latency", "phantom_overtakes", "overtakes_shown", "id_errors",
        "final_order_correct", "final_order_strict", "photo_finishes", "finish_err_ms_mean", "finish_err_ms_max",
        "pos_err_cm_rms", "rack_matches_board", "n_issues", "frames", "laps", "lap_m", "wall_s",
        "truth_order", "final_order", "rack_order"]


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def summarize(rows):
    out = []
    for sc in sorted({r["scenario"] for r in rows}):
        rs = [r for r in rows if r["scenario"] == sc]
        out.append({
            "scenario": sc,
            "description": SCENARIOS[sc].description,
            "races": len(rs),
            "official_order_correct": sum(bool(r["final_order_correct"]) for r in rs),
            "rack_matches_board": sum(bool(r["rack_matches_board"]) for r in rs),
            "rank_accuracy_visible_mean": float(np.mean([r["rank_accuracy"] for r in rs])),
            "rank_accuracy_visible_min": float(np.min([r["rank_accuracy"] for r in rs])),
            "rank_accuracy_latency_min": float(np.min([r["rank_accuracy_latency"] for r in rs])),
            "phantom_overtakes_total": int(sum(r["phantom_overtakes"] for r in rs)),
            "overtakes_shown_total": int(sum(r["overtakes_shown"] for r in rs)),
            "id_errors_total": int(sum(r["id_errors"] for r in rs)),
            "finish_err_ms_max": float(max((r["finish_err_ms_max"] or np.inf) for r in rs)),
            "pos_err_cm_rms_mean": float(np.mean([r["pos_err_cm_rms"] for r in rs])),
        })
    return out


def stills(seed: int, out: Path, scenario: str = "venue"):
    """A contact sheet: what eight of the cameras see at one moment of the race."""
    import cv2

    from marbletrack.venue.camera import VenueCamera
    from marbletrack.venue.rig import place_cameras
    from marbletrack.venue.run import prepare
    from marbletrack.venue.scene import build_scene
    from marbletrack.venue.shading import ShadowMap

    race, _ = prepare(seed, SCENARIOS[scenario])
    course = race.course
    scene = build_scene(course, seed)
    shadows = ShadowMap(scene)
    specs, _ = place_cameras(course, scene, seed)
    tiles = []
    for spec in specs[:7] + specs[-1:]:
        cam = VenueCamera(spec, scene, shadows, course, seed=1)
        # each camera at the moment it sees the most balls
        ts = np.arange(1.0, race.duration, 0.25)
        counts = []
        for t in ts:
            s, u, _, _, h = race.state(t)
            counts.append(int(cam.visible(course.ball_centre(np.mod(s, course.length), u, h)).sum()))
        t = float(ts[int(np.argmax(counts))])
        img = cam.render(race, t)
        img = cv2.resize(img, (640, 360), interpolation=cv2.INTER_AREA)
        cv2.putText(img, f"{spec.cam_id}  t={t:.2f}s", (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        tiles.append(img)
    rows = [np.hstack(tiles[i:i + 2]) for i in range(0, len(tiles), 2)]
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 88])
    print(f"wrote {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[1])
    ap.add_argument("--scenarios", nargs="+", default=list(SCENARIOS))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--cache", default=None, help="folder for physics and detection caches")
    ap.add_argument("--out", default="results/venue.json")
    ap.add_argument("--stills", type=int, default=None, help="only render a contact sheet for this seed")
    args = ap.parse_args()
    if args.stills is not None:
        stills(args.stills, Path("results/stills/venue_cameras.jpg"))
        return
    rows, t0 = [], time.time()
    out = Path(args.out)
    for sc in args.scenarios:
        for seed in args.seeds:
            rep = run_venue_race(seed, sc, workers=args.workers, cache_dir=args.cache)
            row = {"scenario": sc, "seed": seed, **{k: rep.get(k) for k in KEYS},
                   "coverage": rep["coverage"], "issues": rep["issues"][:30],
                   "cameras": [{k: c.get(k) for k in ("cam_id", "ok", "reason", "n_frames", "stretches", "mm_per_px",
                                                       "render_ms", "detect_ms", "palette_issues")} for c in rep["cameras"]]}
            rows.append(_clean(row))
            print(f"{sc} seed {seed}: rank {row['rank_accuracy']:.4f}, phantoms {row['phantom_overtakes']}, "
                  f"id errors {row['id_errors']}, official order {'OK' if row['final_order_correct'] else 'WRONG'}, "
                  f"finish err max {row['finish_err_ms_max']} ms, {row['wall_s']:.0f} s", flush=True)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({"summary": summarize(rows), "runs": rows, "wall_time_s": time.time() - t0}, indent=1))
    for s in summarize(rows):
        print(json.dumps(s))


if __name__ == "__main__":
    main()
