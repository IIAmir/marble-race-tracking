"""What a billiard ball looks like at a point of its surface: body colour, number discs, stripe.

A solid (1-8) is its colour all over, with the number printed in a white disc on
two opposite sides. A stripe (9) is white with a coloured band around its
middle, the number discs sitting on the band. The cue ball is plain white. The
digits are real glyphs (rendered once into a small texture), so the dark ink of
the number takes its true share of the disc.
"""
from __future__ import annotations

import cv2
import numpy as np

from .physics import BallSpec

DISC = np.sin(np.radians(25.0))  # disc radius on the unit sphere (about 40 % of the ball's diameter)
STRIPE = 0.42  # half-height of the stripe band (unit sphere)
WHITE = np.array([232.0, 236.0, 238.0])
INK = np.array([30.0, 28.0, 28.0])
_GLYPHS: dict[int, np.ndarray] = {}


def glyph(number: int, size: int = 64) -> np.ndarray:
    """Ink coverage (0..1) of a number printed in a disc, as a size x size texture."""
    if number not in _GLYPHS:
        img = np.zeros((size, size), np.uint8)
        text = str(number)
        scale = 1.55 if len(text) == 1 else 1.1
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_DUPLEX, scale, 4)
        cv2.putText(img, text, ((size - tw) // 2, (size + th) // 2), cv2.FONT_HERSHEY_DUPLEX, scale, 255, 4, cv2.LINE_AA)
        _GLYPHS[number] = img.astype(np.float32) / 255.0
    return _GLYPHS[number]


def ball_albedo(spec: BallSpec, n_local: np.ndarray) -> np.ndarray:
    """sRGB albedo (N, 3) at unit surface normals ``n_local`` (N, 3) in the ball's own frame."""
    n = n_local
    body = np.asarray(spec.body_bgr, float)
    out = np.tile(body, (len(n), 1))
    if spec.number == 0:
        out[:] = WHITE
        return out
    if spec.stripe:
        out[np.abs(n[:, 2]) > STRIPE] = WHITE
        axis = 0  # discs on the band, at +-x
    else:
        axis = 2  # discs at the poles, +-z
    a = n[:, axis]
    t1, t2 = (1, 2) if axis == 0 else (0, 1)  # the disc's own axes
    disc = np.abs(a) > np.sqrt(1 - DISC**2)
    if disc.any():
        u = n[disc, t1] * np.sign(a[disc]) / DISC
        v = -n[disc, t2] / DISC
        g = glyph(spec.number)
        S = g.shape[0]
        px = np.clip(((u + 1) / 2 * (S - 1)).round().astype(int), 0, S - 1)
        py = np.clip(((v + 1) / 2 * (S - 1)).round().astype(int), 0, S - 1)
        ink = g[py, px][:, None]
        out[disc] = WHITE * (1 - ink) + INK * ink
    return out
