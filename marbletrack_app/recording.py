"""Recording live sessions for replay and validation.

A session folder holds everything needed to run the tracker again on exactly
what the cameras delivered, without the cameras:

    <session>/
      manifest.json           project (map) as it was when going live, app version, start time
      backgrounds/<cam>.png   the empty-track images the detectors used
      cameras/<cam>.avi       every frame, Motion-JPEG
      cameras/<cam>.csv       per frame: index, capture time, arrival time (s, session clock)
      boards.jsonl            every leaderboard published (what vMix and the stream showed)
      events.jsonl            race starts, finishes, camera on/offline, operator actions

Frames are written by a thread inside each camera process; when the disk
cannot keep up, frames are dropped from the recording (never from tracking)
and counted.
"""
from __future__ import annotations

import csv
import json
import os
import queue
import re
import threading
import time
from pathlib import Path

import cv2

from . import __version__
from .config import file_key
from .imageio import imwrite, native_path


def _safe(key: str) -> str:
    return file_key(key)


def session_root(project, map_folder: Path | None) -> Path:
    if project.record.folder:
        return Path(project.record.folder)
    return (map_folder or Path.cwd()) / "recordings"


def start_session(root: Path, project, backgrounds: dict) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = root / stamp
    n = 2
    while path.exists():
        path = root / f"{stamp}-{n}"
        n += 1
    (path / "backgrounds").mkdir(parents=True)
    (path / "cameras").mkdir()
    for key, img in backgrounds.items():
        imwrite(path / "backgrounds" / f"{_safe(key)}.png", img)
    manifest = {"app_version": __version__, "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "project": project.to_dict(),
                "cameras": {c.key: _safe(c.key) for c in project.active_cameras}}
    with open(path / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)
    return path


class FrameRecorder:
    """Writes one camera's frames and their timestamps in a background thread."""

    def __init__(self, folder: Path, key: str, fps: float, quality: int = 92, max_queue: int = 120):
        base = Path(folder) / "cameras" / _safe(key)
        self.video_path = str(base) + ".avi"
        self.csv_path = str(base) + ".csv"
        self.fps, self.quality = float(fps or 30.0), int(quality)
        self.q: queue.Queue = queue.Queue(maxsize=max_queue)
        self.written = self.dropped = 0
        self._writer = None
        self._csv = open(self.csv_path, "w", newline="", encoding="utf-8")
        self._rows = csv.writer(self._csv)
        self._rows.writerow(["index", "t_capture", "t_arrival"])
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"rec-{key}")
        self._thread.start()

    def add(self, frame, t_capture: float, t_arrival: float) -> None:
        try:
            self.q.put_nowait((frame, t_capture, t_arrival))
        except queue.Full:
            self.dropped += 1

    def _run(self):
        while True:
            item = self.q.get()
            if item is None:
                break
            frame, tc, ta = item
            if self._writer is None:
                h, w = frame.shape[:2]
                self._writer = cv2.VideoWriter(native_path(self.video_path), cv2.VideoWriter_fourcc(*"MJPG"), self.fps, (w, h))
                self._writer.set(cv2.VIDEOWRITER_PROP_QUALITY, self.quality)
            self._writer.write(frame)
            self._rows.writerow([self.written, f"{tc:.6f}", f"{ta:.6f}"])
            self.written += 1

    def close(self) -> dict:
        self.q.put(None)
        self._thread.join(timeout=30)
        if self._writer is not None:
            self._writer.release()
        self._csv.close()
        return {"written": self.written, "dropped": self.dropped}


class SessionLog:
    """Leaderboards and events of a live session, one JSON object per line."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self._boards = open(self.folder / "boards.jsonl", "a", encoding="utf-8")
        self._events = open(self.folder / "events.jsonl", "a", encoding="utf-8")
        self._lock = threading.Lock()

    def board(self, now: float, data: dict):
        with self._lock:
            self._boards.write(json.dumps({"now": round(now, 4), **data}, default=float) + "\n")

    def event(self, now: float, kind: str, data: dict):
        with self._lock:
            self._events.write(json.dumps({"now": round(now, 4), "kind": kind, **data}, default=float) + "\n")
            self._events.flush()

    def close(self):
        with self._lock:
            for f in (self._boards, self._events):
                try:
                    f.close()
                except Exception:
                    pass


def read_frames_csv(path: str | os.PathLike) -> list[tuple[int, float, float]]:
    with open(path, encoding="utf-8") as f:
        rows = list(csv.reader(f))[1:]
    return [(int(r[0]), float(r[1]), float(r[2])) for r in rows if r]


def read_jsonl(path: str | os.PathLike) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
