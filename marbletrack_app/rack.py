"""The finish rack: an independent check of the finishing order.

The marbles end up in the rack in the order they finished. A camera that sees
the rack (role "rack") has its slots clicked in step 3, first finisher's slot
first, and learns the marble colours in step 5 like any camera. While live it
reads which marble sits in each slot; when the race is complete the rack order
is compared with the board. Agreement can confirm the result automatically;
any difference is raised to race control before the result is confirmed.

The run-out between the finish line and the rack must be single file, or the
rack order is not the finishing order.
"""
from __future__ import annotations

import cv2
import numpy as np

from marbletrack.color import bgr_to_lab, d2_matrix
from marbletrack.detector import suppress_shadows

EMPTY, UNSURE = None, "?"


def read_rack(frame: np.ndarray, background: np.ndarray, slots: np.ndarray, radii: np.ndarray, palette, ids: list[str],
              fg_threshold: int = 30, fill: float = 0.45, pixel_gate: float = 20.0) -> list:
    """Marble id in each slot; ``None`` for an empty slot, ``"?"`` when the colour is not clear."""
    out = []
    h, w = frame.shape[:2]
    for (x, y), r in zip(np.asarray(slots, float), np.asarray(radii, float)):
        rr = max(3, int(round(0.8 * r)))
        x0, y0, x1, y1 = max(0, int(x) - rr), max(0, int(y) - rr), min(w, int(x) + rr + 1), min(h, int(y) + rr + 1)
        if x1 <= x0 or y1 <= y0:
            out.append(UNSURE)
            continue
        patch, bg = frame[y0:y1, x0:x1], background[y0:y1, x0:x1]
        yy, xx = np.mgrid[y0:y1, x0:x1]
        disc = ((xx - x) ** 2 + (yy - y) ** 2 <= rr * rr).astype(np.uint8)
        fg = (cv2.absdiff(patch, bg).max(axis=2) > fg_threshold).astype(np.uint8) & disc
        fg = suppress_shadows(patch, bg, fg)
        if fg.sum() < fill * disc.sum():
            out.append(EMPTY)
            continue
        # Vote pixel by pixel: a marble resting with its white number disc towards the camera
        # shows a large white patch, which would drag a median towards the cue ball; the body
        # colour still has most of the pixels.
        d2 = d2_matrix(palette, bgr_to_lab(patch[fg.astype(bool)]))
        cls = d2.argmin(axis=1)
        votes = np.bincount(cls[d2[np.arange(len(cls)), cls] < pixel_gate], minlength=len(ids))
        order = np.argsort(votes)[::-1]
        best, second = votes[order[0]], votes[order[1]] if len(votes) > 1 else 0
        clear = best >= 0.3 * len(cls) and best >= 1.4 * max(second, 1)
        out.append(ids[int(order[0])] if clear else UNSURE)
    return out


def rack_order(slots: list) -> list:
    """The filled slots from the first one, up to the first empty slot."""
    order = []
    for v in slots:
        if v is EMPTY:
            break
        order.append(v)
    return order


def compare(slots: list, board_order: list[str]) -> dict:
    """Rack against the board's finishing order: match, and where they differ."""
    order = rack_order(slots)
    unsure = [k + 1 for k, v in enumerate(order) if v == UNSURE]
    diffs = [(k + 1, r, b) for k, (r, b) in enumerate(zip(order, board_order)) if r != UNSURE and r != b]
    match = bool(order) and not diffs and not unsure and len(order) >= len(board_order)
    if diffs:
        status, detail = "differs", "; ".join(f"slot {k}: rack {r}, board {b}" for k, r, b in diffs[:4])
    elif unsure:
        status, detail = "unreadable", f"slot(s) {', '.join(map(str, unsure))} not readable"
    elif len(order) < len(board_order):
        status, detail = "filling", f"{len(order)} of {len(board_order)} marbles in the rack"
    else:
        status, detail = "agrees", "rack and board agree"
    return {"match": match, "status": status, "rack": order, "board": list(board_order), "detail": detail}
