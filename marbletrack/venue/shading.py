"""Materials and light for the static diorama: procedural textures, a key light with shadows.

Colours are albedos in sRGB, converted to linear light for shading. The room
has one main ceiling light (the key light, casting the mountains' and trees'
shadows) and diffuse fill from the walls and ceiling (ambient, brighter on
surfaces facing up).
"""
from __future__ import annotations

import numpy as np

from . import scene as S
from .raster import View, rasterize

AMBIENT = 0.42
KEY = 0.78
ENV = np.array([0.32, 0.31, 0.29])  # what a glossy ball reflects (linear BGR): white walls, warm floor
FLOWER_COLOURS = np.array([(200, 120, 245), (40, 210, 245), (40, 40, 220), (30, 120, 245), (235, 235, 240),
                           (170, 60, 140), (190, 160, 250)], float)


def srgb_to_lin(c):
    c = np.asarray(c, np.float32) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def lin_to_srgb(x):
    x = np.clip(x, 0.0, 1.0)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055) * 255.0


def _hash(i, j, k):
    h = np.sin(i * 127.1 + j * 311.7 + k * 74.7) * 43758.5453
    return h - np.floor(h)


def value_noise(p):
    """Smooth 3-D value noise in [0, 1] at points ``p`` (N, 3) (lattice spacing 1)."""
    f = np.floor(p)
    t = p - f
    t = t * t * (3 - 2 * t)
    i, j, k = f[:, 0], f[:, 1], f[:, 2]
    out = 0.0
    for dx in (0, 1):
        for dy in (0, 1):
            for dz in (0, 1):
                w = (t[:, 0] if dx else 1 - t[:, 0]) * (t[:, 1] if dy else 1 - t[:, 1]) * (t[:, 2] if dz else 1 - t[:, 2])
                out = out + w * _hash(i + dx, j + dy, k + dz)
    return out


def fbm(p, octaves=3):
    out, amp, tot = 0.0, 1.0, 0.0
    for o in range(octaves):
        out = out + amp * value_noise(p * (2.0**o) + o * 17.3)
        tot += amp
        amp *= 0.5
    return out / tot


def albedo(mat, colour, X, n):
    """Per-pixel sRGB albedo (N, 3) for material ids ``mat`` at world points ``X`` with normals ``n``."""
    a = colour.astype(np.float32).copy()
    def sel(m):
        return np.flatnonzero(mat == m)
    i = sel(S.GRASS)
    if len(i):
        p = X[i]
        a[i] *= (0.82 + 0.32 * fbm(p * 45))[:, None]
        # flowers: some cells of a 1.2 cm grid carry a coloured flower, densest in flower beds
        cell = np.floor(p[:, :2] / 0.012)
        h = _hash(cell[:, 0], cell[:, 1], 3.0)
        bed = fbm(p * 3.0, 2)
        has = h < np.clip((bed - 0.45) * 1.6, 0, 0.6)
        cx = (cell + 0.5 + 0.3 * (np.c_[_hash(cell[:, 0], cell[:, 1], 5.0), _hash(cell[:, 0], cell[:, 1], 7.0)] - 0.5)) * 0.012
        dist = np.linalg.norm(p[:, :2] - cx, axis=1)
        rad = 0.0022 + 0.0016 * _hash(cell[:, 0], cell[:, 1], 9.0)
        on = has & (dist < rad)
        col = FLOWER_COLOURS[(_hash(cell[:, 0], cell[:, 1], 11.0) * len(FLOWER_COLOURS)).astype(int) % len(FLOWER_COLOURS)]
        a[i[on]] = col[on]
    i = sel(S.ROCK)
    if len(i):
        p = X[i]
        strata = 0.88 + 0.12 * np.sin(p[:, 2] * 95 + 4 * fbm(p * 8, 2))
        crev = 0.78 + 0.3 * fbm(p * 70)
        a[i] *= (strata * crev)[:, None]
        moss = (n[i, 2] > 0.75) & (fbm(p * 20 + 3, 2) > 0.58)
        a[i[moss]] = a[i[moss]] * 0.4 + np.array([50, 120, 70]) * 0.6
    i = sel(S.SNOW)
    if len(i):
        a[i] *= (0.9 + 0.12 * fbm(X[i] * 140))[:, None]
    i = sel(S.ASPHALT)
    if len(i):
        a[i] *= (0.8 + 0.4 * fbm(X[i] * 160))[:, None]
    for m in (S.FLOOR, S.WALL, S.POST, S.PIN):
        i = sel(m)
        if len(i):  # printed PLA: faint layer lines and print variation
            a[i] *= (0.97 + 0.03 * np.sin(X[i, 2] * 2 * np.pi / 0.0006) * 0.5 + 0.04 * (fbm(X[i] * 60, 2) - 0.5))[:, None]
    i = sel(S.TOWER)
    if len(i):  # vertical ribs
        ang = np.arctan2(X[i, 1], X[i, 0])
        a[i] *= (0.8 + 0.2 * np.cos(ang * 90))[:, None]
    i = sel(S.FOLIAGE)
    if len(i):
        a[i] *= (0.7 + 0.45 * fbm(X[i] * 110))[:, None]
    i = sel(S.GOLF)
    if len(i):
        a[i] *= (0.92 + 0.12 * np.sin((X[i, 0] + X[i, 1]) * 120))[:, None]  # mowing stripes
    i = sel(S.ROOM)
    if len(i):
        a[i] *= (0.85 + 0.25 * fbm(X[i] * [20, 2, 20]))[:, None]  # wood floor
    return np.clip(a, 0, 255)


class ShadowMap:
    """Depth seen from the key light (orthographic), for shadows of mountains, trees and the tower."""

    def __init__(self, scene: S.Scene, res: int = 3072):
        Ld = -scene.light  # light travels along -light
        fwd = Ld / np.linalg.norm(Ld)
        right = np.cross(fwd, [0.0, 1.0, 0.0])
        right /= np.linalg.norm(right)
        down = np.cross(fwd, right)
        R = np.vstack([right, down, fwd])
        x0, y0, x1, y1 = scene.bounds
        corners = np.array([[x, y, z] for x in (x0, x1) for y in (y0, y1) for z in (0.0, 1.3)])
        c = corners @ R.T
        C = -fwd * 5.0
        span = (c[:, :2].max(0) - c[:, :2].min(0)).max() * 1.02
        self.ppm = res / span
        mid = (c[:, :2].max(0) + c[:, :2].min(0)) / 2
        cxy = res / 2 - mid * self.ppm + (C @ R.T)[:2] * self.ppm
        self.view = View(R, C, self.ppm, cxy[0], cxy[1], res, res, ortho=True, near=-1e9)
        tris = scene.tris[scene.mat != S.ROOM]
        self.depth, _ = rasterize(self.view, tris)

    def visibility(self, X, bias=0.004):
        """Fraction of the key light reaching world points ``X`` (N, 3): 3x3 PCF, soft edges."""
        uv, z = self.view.project(X)
        h, w = self.depth.shape
        out = np.zeros(len(X), np.float32)
        for dx in (-1.5, 0, 1.5):
            for dy in (-1.5, 0, 1.5):
                u = np.clip(np.round(uv[:, 0] + dx).astype(int), 0, w - 1)
                v = np.clip(np.round(uv[:, 1] + dy).astype(int), 0, h - 1)
                out += (z - bias <= self.depth[v, u]).astype(np.float32)
        return out / 9.0


def shade_static(view: View, scene: S.Scene, shadows: ShadowMap, chunk=1_000_000):
    """Rasterise and shade the static scene for one camera.

    Returns ``lit`` and ``ambient`` (linear BGR float32), the depth (camera z) and
    the world position of every pixel. ``lit - ambient`` is the key light's share,
    which a ball's shadow removes.
    """
    depth, tri = rasterize(view, scene.tris)
    H, W = depth.shape
    lit = np.zeros((H * W, 3), np.float32)
    amb = np.zeros((H * W, 3), np.float32)
    pos = np.zeros((H * W, 3), np.float32)
    tflat = tri.ravel()
    zflat = depth.ravel()
    normals = np.cross(scene.tris[:, 1] - scene.tris[:, 0], scene.tris[:, 2] - scene.tris[:, 0])
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
    yy, xx = np.divmod(np.arange(H * W), W)
    for c0 in range(0, H * W, chunk):
        idx = np.arange(c0, min(H * W, c0 + chunk))
        idx = idx[tflat[idx] >= 0]
        if not len(idx):
            continue
        t = tflat[idx]
        ra, rb = view.pixel_rays(xx[idx] + 0.5, yy[idx] + 0.5)
        Xc = np.c_[ra * zflat[idx], rb * zflat[idx], zflat[idx]]
        X = Xc @ view.R + view.C
        n = normals[t]
        to_cam = view.C - X
        flip = (n * to_cam).sum(1) < 0
        n[flip] *= -1
        mat = scene.mat[t].copy()
        col = scene.colour[t].copy()
        ter = mat == S.TERRAIN
        if ter.any() and scene.terrain_material is not None:
            n[ter] = scene.terrain_normal(X[ter])  # smooth shading: no facets
            sub = scene.terrain_material(X[ter], n[ter])
            mat[ter] = sub
            col[ter] = np.array([S.ALBEDO[int(m)] for m in range(len(S.ALBEDO))], np.float32)[sub]
        alb = srgb_to_lin(albedo(mat, col, X, n))
        vis = shadows.visibility(X)
        ndl = np.clip(n @ scene.light, 0, None)
        hemi = AMBIENT * (0.6 + 0.4 * np.clip(n[:, 2], -1, 1))
        amb[idx] = alb * hemi[:, None]
        lit[idx] = amb[idx] + alb * (KEY * ndl * vis)[:, None]
        pos[idx] = X
    # where nothing was drawn (above the horizon): the room's walls
    none = tflat < 0
    lit[none] = amb[none] = ENV
    pos[none] = view.C + 50.0
    return lit.reshape(H, W, 3), amb.reshape(H, W, 3), depth, pos.reshape(H, W, 3)
