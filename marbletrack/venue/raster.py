"""A small vectorised triangle rasteriser with a depth buffer (numpy only).

Used once per camera to draw the static diorama, and once per course for the
light's shadow map. Depth is exact per pixel: every pixel's ray is intersected
with its triangle's plane (perspective-correct by construction). Triangles are
processed in batches grouped by screen size; within a batch, fragments are
resolved by sorting on (pixel, depth).
"""
from __future__ import annotations

import numpy as np


class View:
    """A pinhole (``ortho=False``) or orthographic view onto a ``width x height`` canvas.

    Pinhole: camera-space rows ``R`` (right, down, forward) at centre ``C``, focal
    ``f`` (px) and principal point ``(cx, cy)``. Orthographic: same axes, ``f`` is
    pixels per metre and depth is the distance along the forward axis.
    """

    def __init__(self, R, C, f, cx, cy, width, height, ortho=False, near=0.02):
        self.R, self.C = np.asarray(R, float), np.asarray(C, float)
        self.f, self.cx, self.cy = float(f), float(cx), float(cy)
        self.width, self.height, self.ortho, self.near = int(width), int(height), ortho, near

    def to_cam(self, X):
        return (np.asarray(X, float) - self.C) @ self.R.T

    def project(self, X):
        Xc = self.to_cam(X)
        if self.ortho:
            return Xc[..., :2] * self.f + [self.cx, self.cy], Xc[..., 2]
        z = Xc[..., 2]
        return Xc[..., :2] / np.maximum(z, 1e-9)[..., None] * self.f + [self.cx, self.cy], z

    def pixel_rays(self, px, py):
        """Camera-space ray (pinhole: direction with z = 1) or origin (ortho) of pixel centres."""
        a = (np.asarray(px, float) - self.cx) / self.f
        b = (np.asarray(py, float) - self.cy) / self.f
        return a, b


def rasterize(view: View, tris: np.ndarray, chunk_px: int = 1_500_000):
    """Draw triangles (M, 3, 3 world) -> (depth (H, W) float32, triangle index (H, W) int32, -1 = none)."""
    W, H = view.width, view.height
    zbuf = np.full(H * W, np.inf, np.float32)
    tbuf = np.full(H * W, -1, np.int32)
    Xc = view.to_cam(tris.reshape(-1, 3)).reshape(-1, 3, 3)
    keep = np.all(Xc[:, :, 2] > view.near, axis=1)
    ids = np.flatnonzero(keep)
    Xc = Xc[keep]
    if view.ortho:
        uv = Xc[:, :, :2] * view.f + [view.cx, view.cy]
    else:
        uv = Xc[:, :, :2] / Xc[:, :, 2:3] * view.f + [view.cx, view.cy]
    x0 = np.floor(uv[:, :, 0].min(1)).astype(np.int64)
    x1 = np.ceil(uv[:, :, 0].max(1)).astype(np.int64)
    y0 = np.floor(uv[:, :, 1].min(1)).astype(np.int64)
    y1 = np.ceil(uv[:, :, 1].max(1)).astype(np.int64)
    vis = (x1 >= 0) & (x0 < W) & (y1 >= 0) & (y0 < H)
    x0, x1, y0, y1 = np.clip(x0, 0, W - 1), np.clip(x1, 0, W), np.clip(y0, 0, H - 1), np.clip(y1, 0, H)
    vis &= (x1 > x0) & (y1 > y0)
    n = np.cross(Xc[:, 1] - Xc[:, 0], Xc[:, 2] - Xc[:, 0])
    d = (n * Xc[:, 0]).sum(1)
    # edge function coefficients in pixel space, oriented so inside >= 0
    a = uv
    area = (a[:, 1, 0] - a[:, 0, 0]) * (a[:, 2, 1] - a[:, 0, 1]) - (a[:, 1, 1] - a[:, 0, 1]) * (a[:, 2, 0] - a[:, 0, 0])
    vis &= np.abs(area) > 1e-9
    sgn = np.sign(area)
    size = np.maximum(x1 - x0, y1 - y0)
    for lo, hi in ((0, 4), (4, 8), (8, 16), (16, 32), (32, 64), (64, 128), (128, 256), (256, 512), (512, 1 << 30)):
        sel = np.flatnonzero(vis & (size > lo) & (size <= hi))
        if not len(sel):
            continue
        S = hi if hi < (1 << 30) else int(size[sel].max())
        if S * S > chunk_px:  # very large triangles: one at a time, tiled
            for k in sel:
                _tri_tiles(view, k, ids, a, x0, x1, y0, y1, n, d, sgn, zbuf, tbuf, chunk_px)
            continue
        per = max(1, chunk_px // (S * S))
        oy, ox = np.divmod(np.arange(S * S), S)
        for c0 in range(0, len(sel), per):
            k = sel[c0:c0 + per]
            px = x0[k, None] + ox[None, :]
            py = y0[k, None] + oy[None, :]
            ok = (px < x1[k, None]) & (py < y1[k, None])
            _frag(view, k, px, py, ok, ids, a, n, d, sgn, zbuf, tbuf)
    return zbuf.reshape(H, W), tbuf.reshape(H, W)


def _tri_tiles(view, k, ids, a, x0, x1, y0, y1, n, d, sgn, zbuf, tbuf, chunk_px):
    rows = max(1, chunk_px // max(1, int(x1[k] - x0[k])))
    for ys in range(int(y0[k]), int(y1[k]), rows):
        ye = min(int(y1[k]), ys + rows)
        py, px = np.mgrid[ys:ye, int(x0[k]):int(x1[k])]
        kk = np.array([k])
        _frag(view, kk, px.reshape(1, -1), py.reshape(1, -1), np.ones((1, px.size), bool), ids, a, n, d, sgn, zbuf, tbuf)


def _frag(view, k, px, py, ok, ids, a, n, d, sgn, zbuf, tbuf):
    W = view.width
    cxp, cyp = px + 0.5, py + 0.5
    inside = ok
    for e in range(3):
        p0, p1 = a[k, e], a[k, (e + 1) % 3]
        ef = (p1[:, 0, None] - p0[:, 0, None]) * (cyp - p0[:, 1, None]) - (p1[:, 1, None] - p0[:, 1, None]) * (cxp - p0[:, 0, None])
        inside &= ef * sgn[k, None] >= 0
    if not inside.any():
        return
    ra, rb = view.pixel_rays(cxp, cyp)
    nk = n[k]
    if view.ortho:
        # point = (ra, rb, t) in camera axes: n . (ra, rb, t) = d
        denom = nk[:, 2, None]
        z = (d[k, None] - nk[:, 0, None] * ra - nk[:, 1, None] * rb) / np.where(np.abs(denom) < 1e-12, 1e-12, denom)
    else:
        denom = nk[:, 0, None] * ra + nk[:, 1, None] * rb + nk[:, 2, None]
        z = d[k, None] / np.where(np.abs(denom) < 1e-12, 1e-12, denom)
    inside &= np.isfinite(z) & (z > view.near if not view.ortho else True)
    pix = (py * W + px)[inside]
    zz = z[inside].astype(np.float32)
    tt = np.broadcast_to(ids[k][:, None], inside.shape)[inside]
    order = np.lexsort((zz, pix))
    pix, zz, tt = pix[order], zz[order], tt[order]
    first = np.r_[True, pix[1:] != pix[:-1]]
    pix, zz, tt = pix[first], zz[first], tt[first]
    better = zz < zbuf[pix]
    zbuf[pix[better]] = zz[better]
    tbuf[pix[better]] = tt[better]
