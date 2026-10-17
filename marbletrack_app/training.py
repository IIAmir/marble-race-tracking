"""Training data from real races, labelled for free.

Roll one marble alone through the course while every camera saves a crop
of each blob it detects: whatever the cameras find is that marble, under the
venue's real light, motion blur, NDI compression and shadows. A run with no
marble at all labels every detection "empty" (shadows, flowers, hands: what
a classifier must learn to reject). Each sample is the crop from the frame
and the same crop from the empty-track image, the pair a crop classifier
needs to tell a black marble from a shadow.

    <map folder>/training/<label>/<camera>-<n>.png        frame crop | empty-track crop, side by side
    <map folder>/training/<label>/<camera>.jsonl          one line per crop: time, size, colour scores

Crops are scaled to a fixed size around the blob (about three marble
diameters wide) and capped at ``max_rate`` per camera per second.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import cv2
import numpy as np

from .imageio import imwrite

SIZE = 64


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-") or "x"


class CropWriter:
    def __init__(self, folder, cam_key: str, background: np.ndarray, detector, max_rate: float = 10.0):
        self.root = Path(folder)
        self.cam = _safe(cam_key)
        self.background = background
        self.det = detector
        self.min_dt = 1.0 / max_rate
        self.last_t = -1e9
        self.counts: dict[str, int] = {}
        self._logs: dict[str, object] = {}

    def add(self, frame: np.ndarray, t: float, dets, label: str) -> None:
        if t - self.last_t < self.min_dt:
            return
        self.last_t = t
        out = self.root / _safe(label)
        out.mkdir(parents=True, exist_ok=True)
        if label not in self._logs:
            self._logs[label] = open(out / f"{self.cam}.jsonl", "a", encoding="utf-8")
        h, w = frame.shape[:2]
        for d in dets:
            r = np.sqrt(d.area / np.pi) / max(self.det.work_scale, 1e-6)  # blob radius, full-resolution pixels
            half = int(max(8, 3 * r))
            x, y = int(round(d.xy[0])), int(round(d.xy[1]))
            x0, y0, x1, y1 = max(0, x - half), max(0, y - half), min(w, x + half), min(h, y + half)
            if x1 - x0 < 8 or y1 - y0 < 8:
                continue
            pair = [cv2.resize(img[y0:y1, x0:x1], (SIZE, SIZE), interpolation=cv2.INTER_AREA) for img in (frame, self.background)]
            n = self.counts.get(label, 0)
            name = f"{self.cam}-{n:06d}.png"
            imwrite(out / name, np.hstack(pair))
            self._logs[label].write(json.dumps({"file": name, "t": round(t, 4), "xy": [x, y], "radius_px": round(float(r), 2),
                                                "color_d2": [round(float(v), 2) for v in d.color_d2]}) + "\n")
            self.counts[label] = n + 1

    def close(self) -> dict:
        for f in self._logs.values():
            f.close()
        return dict(self.counts)
