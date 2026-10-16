"""Run the tracker again on a recorded session, without cameras.

    python -m marbletrack_app.replay <session folder> [--out replay-folder] [--workers 4]

Every camera's recorded frames go through a fresh detector built from the
session's own map and empty-track images (one process per camera, like live).
The detections are then fused in the order they arrived live, through the same
``Runtime.step`` as the live loop, so a replay reproduces what was on air and
any change to the tracker can be measured on the same real races.

Output (in ``--out``, default ``<session>/replay-<time>``): ``boards.jsonl`` and
``events.jsonl`` in the live format, and ``summary.json``.
"""
from __future__ import annotations

import argparse
import json
import pickle
import time
from multiprocessing import get_context
from pathlib import Path

import cv2

from . import calibration as C
from .config import Project
from .recording import SessionLog, read_frames_csv, read_jsonl


def load_session(folder: Path):
    folder = Path(folder)
    with open(folder / "manifest.json", encoding="utf-8") as f:
        manifest = json.load(f)
    project = Project.from_dict(manifest["project"])
    backgrounds = {}
    for key, name in manifest["cameras"].items():
        img = cv2.imread(str(folder / "backgrounds" / f"{name}.png"))
        if img is not None:
            backgrounds[key] = img
    return manifest, project, backgrounds


def _detect_camera(args):
    """All recorded frames of one camera -> [(arrival, t, detections)] (runs in a worker process)."""
    from marbletrack.detector import ColorDetector

    from .engine import _min_radius_px, _work_scale

    folder, key, name, calib_blob, latency, readout = args
    calib = pickle.loads(calib_blob)
    rows = read_frames_csv(Path(folder) / "cameras" / f"{name}.csv")
    cap = cv2.VideoCapture(str(Path(folder) / "cameras" / f"{name}.avi"))
    h, w = calib.track_mask.shape
    det = ColorDetector(calib, work_scale=_work_scale(w, h, _min_radius_px(calib)), readout=readout)
    out = []
    t_detect = 0.0
    for _, t_capture, t_arrival in rows:
        ok, frame = cap.read()
        if not ok:
            break
        t = t_capture - latency
        t0 = time.perf_counter()
        out.append((t_arrival, t, det.detect(frame, t)))
        t_detect += time.perf_counter() - t0
    cap.release()
    return key, out, {"frames": len(out), "detect_ms": 1000 * t_detect / max(1, len(out))}


def detect_session(folder: Path, project: Project, backgrounds: dict, manifest: dict, workers: int = 4):
    jobs = []
    for cam in project.active_cameras:
        if cam.key not in manifest["cameras"]:
            continue
        calib = C.build_calibration(cam, project, backgrounds.get(cam.key))
        jobs.append((str(folder), cam.key, manifest["cameras"][cam.key], pickle.dumps(calib), cam.latency_ms / 1000.0,
                     cam.readout_ms / 1000.0))
    results, stats = {}, {}
    with get_context("spawn").Pool(max(1, workers)) as pool:
        for key, dets, st in pool.imap_unordered(_detect_camera, jobs):
            results[key], stats[key] = dets, st
    return results, stats


def fuse(project: Project, backgrounds: dict, detections: dict, out: Path | None = None) -> dict:
    """Fuse recorded detections in live arrival order through ``Runtime.step``; returns the boards and events."""
    from .engine import Runtime

    class _Replay(Runtime):
        replay_now = 0.0

        def now(self):
            return self.replay_now

    boards, events = [], []

    def on_event(kind, data):
        if kind == "board":
            boards.append({"now": round(rt.replay_now, 4), **data})
        else:
            events.append({"now": round(rt.replay_now, 4), "kind": kind, **{k: v for k, v in data.items() if k != "board"}})

    rt = _Replay(project, backgrounds, on_event=on_event, record_dir=None)
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        rt.session_log = SessionLog(out)
    stream = sorted(((a, key, t, d) for key, rows in detections.items() for a, t, d in rows), key=lambda x: (x[0], x[1]))
    for arrival, key, t, dets in stream:
        rt.replay_now = arrival
        rt.step(("det", key, t, dets), arrival)
    if stream:  # let the reorder buffer drain and late finishes settle
        end = stream[-1][0]
        for k in range(1, 61):
            rt.replay_now = end + 0.05 * k
            rt.step(None, rt.replay_now)
    if rt.session_log is not None:
        rt.session_log.close()
    return {"boards": boards, "events": events, "final": rt._board(rt.race), "history": list(rt.history)}


def replay(folder: Path, out: Path | None = None, workers: int = 4) -> dict:
    folder = Path(folder)
    manifest, project, backgrounds = load_session(folder)
    out = Path(out) if out else folder / f"replay-{time.strftime('%Y%m%d-%H%M%S')}"
    t0 = time.time()
    detections, stats = detect_session(folder, project, backgrounds, manifest, workers)
    res = fuse(project, backgrounds, detections, out)
    live_boards = read_jsonl(folder / "boards.jsonl")
    summary = {"session": str(folder), "app_version_recorded": manifest.get("app_version"),
               "cameras": stats, "boards": len(res["boards"]), "live_boards": len(live_boards),
               "final_order": [e["marble_id"] for e in res["final"]],
               "races": [{"race_id": h["race_id"], "state": h["state"], "order": [e["marble_id"] for e in h["board"]]}
                         for h in res["history"]],
               "wall_s": round(time.time() - t0, 1)}
    with open(out / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)
    return {**summary, "out": str(out)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("session")
    ap.add_argument("--out", default=None)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args(argv)
    res = replay(Path(args.session), Path(args.out) if args.out else None, args.workers)
    print(json.dumps({k: v for k, v in res.items() if k != "cameras"}, indent=1))


if __name__ == "__main__":
    main()
