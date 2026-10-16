"""The static diorama around a course: terrain, the printed track, the tower and the props.

Everything is triangles with a material id (and a per-object colour for cars and
trees), built from the course so the scenery follows the route: embankments and
rock faces carry the track down the mountain, the tunnel is mountain over the
track, white plaster lies along some edges of the track (as on the real
layout), and trees, blossoms, flowers, a parking lot with cars, cones, a
house and a golf patch fill the rest.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from .course import BALL_R, WALL_H, WALL_T, Course

# materials (index into ALBEDO); sRGB BGR
GRASS, ROCK, SNOW, ASPHALT, FLOOR, WALL, TOWER, TRUNK, FOLIAGE, CAR, CONE, SAND, GOLF, HOUSE, ROOF, ROOM, PIN, POST, LINE, TERRAIN, BUMPER, RACK = range(22)
ALBEDO = {
    GRASS: (70, 150, 85), ROCK: (112, 142, 176), SNOW: (226, 229, 231), ASPHALT: (48, 48, 52),
    FLOOR: (108, 109, 113), WALL: (120, 121, 125), TOWER: (98, 99, 103), TRUNK: (40, 58, 88),
    FOLIAGE: (60, 140, 70), CAR: (128, 128, 128), CONE: (30, 110, 240), SAND: (150, 196, 222),
    GOLF: (85, 185, 115), HOUSE: (70, 92, 160), ROOF: (62, 62, 72), ROOM: (78, 104, 132), PIN: (150, 151, 155),
    POST: (100, 101, 105), LINE: (230, 232, 233), TERRAIN: (112, 142, 176), BUMPER: (40, 40, 44), RACK: (70, 71, 76),
}
BUMPER_CAPS = [(40, 40, 210), (210, 120, 30), (30, 190, 235), (235, 235, 235)]  # red, blue, yellow, white rubber
FOLIAGE_COLOURS = [(45, 120, 55), (60, 150, 75), (40, 105, 60), (75, 165, 95), (175, 150, 240), (190, 170, 245),
                   (60, 195, 240), (40, 170, 230)]  # greens, pink blossom, yellow blossom
CAR_COLOURS = [(35, 35, 200), (30, 190, 235), (170, 80, 30), (235, 235, 235), (30, 30, 32), (30, 110, 240),
               (175, 175, 180), (60, 140, 50), (120, 40, 140)]


@dataclass
class Scene:
    tris: np.ndarray  # (M, 3, 3)
    mat: np.ndarray  # (M,) material id
    colour: np.ndarray  # (M, 3) sRGB BGR albedo (per object; used for cars, trees)
    height: callable  # terrain height z(x, y)
    bounds: tuple  # (x0, y0, x1, y1) of the diorama
    light: np.ndarray  # unit vector toward the key light
    terrain_material: callable = None  # (X (N, 3), n (N, 3)) -> material id per terrain pixel
    terrain_normal: callable = None  # (X (N, 3)) -> smooth unit normals of the terrain


class _Mesh:
    def __init__(self):
        self.t, self.m, self.c = [], [], []

    def add(self, tris, mat, colour=None):
        tris = np.asarray(tris, float).reshape(-1, 3, 3)
        if not len(tris):
            return
        self.t.append(tris)
        self.m.append(np.full(len(tris), mat, np.int16))
        col = ALBEDO[mat] if colour is None else colour
        self.c.append(np.tile(np.asarray(col, np.float32), (len(tris), 1)))

    def quads(self, a, b, c, d, mat, colour=None):
        """Quads given as four (N, 3) corner arrays -> two triangles each."""
        self.add(np.stack([np.stack([a, b, c], 1), np.stack([a, c, d], 1)], 1).reshape(-1, 3, 3), mat, colour)

    def build(self):
        return np.concatenate(self.t), np.concatenate(self.m), np.concatenate(self.c)


def _cylinder(c, r, h, n=8, z0=None):
    ang = np.linspace(0, 2 * np.pi, n + 1)
    x, y = c[0] + r * np.cos(ang), c[1] + r * np.sin(ang)
    zb = c[2] if z0 is None else z0
    a = np.c_[x[:-1], y[:-1], np.full(n, zb)]
    b = np.c_[x[1:], y[1:], np.full(n, zb)]
    return a, b, b + [0, 0, h + c[2] - zb], a + [0, 0, h + c[2] - zb]


def _sphere(c, r, n=10, squash=1.0):
    th = np.linspace(0, np.pi, n // 2 + 1)
    ph = np.linspace(0, 2 * np.pi, n + 1)
    T, P = np.meshgrid(th, ph, indexing="ij")
    V = np.stack([np.sin(T) * np.cos(P) * r, np.sin(T) * np.sin(P) * r, np.cos(T) * r * squash], -1) + c
    a, b, cc, d = V[:-1, :-1], V[:-1, 1:], V[1:, 1:], V[1:, :-1]
    return [x.reshape(-1, 3) for x in (a, b, cc, d)]


def _box(c, size, yaw=0.0):
    sx, sy, sz = np.asarray(size) / 2
    corners = np.array([[x, y, z] for z in (0, 2 * sz) for y in (-sy, sy) for x in (-sx, sx)])
    ca, sa = np.cos(yaw), np.sin(yaw)
    corners = corners @ np.array([[ca, sa, 0], [-sa, ca, 0], [0, 0, 1]]) + c
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    return [np.array([corners[f[i]] for f in faces]) for i in range(4)]


def build_scene(course: Course, seed: int = 0, grid: float = 0.025) -> Scene:
    rng = np.random.default_rng(seed + 31)
    mesh = _Mesh()
    s, P, Lat, U, W = course.s, course.P, course.L, course.U, course.width
    helix_mask = course.in_any(s, course.helix)
    tunnel_mask = course.in_any(s, course.tunnels)
    lo = P[:, :2].min(0) - 0.4
    hi = P[:, :2].max(0) + 0.4
    # ------------------------------------------------------------------ terrain
    xs = np.arange(lo[0], hi[0] + grid, grid)
    ys = np.arange(lo[1], hi[1] + grid, grid)
    X, Y = np.meshgrid(xs, ys)
    pts = np.c_[X.ravel(), Y.ravel()]
    sub = slice(None, None, 2)
    tree_all = cKDTree(P[sub, :2])
    d_all, i_all = tree_all.query(pts)
    i_all = i_all * 2
    z_tr = P[i_all, 2]
    half = W[i_all] / 2 + WALL_T
    # embankment: the mountain carries the track (not under the tower). Taken as the highest
    # support of ALL passes (not just the nearest), so the terrain stays continuous between them.
    samp = np.arange(0, len(s), 4)
    lift_mask = np.array([g in ('lift', 'transfer') for g in course.tags])
    sup_i = samp[~helix_mask[samp] & ~lift_mask[samp] & ~course.in_any(s[samp], course.tunnels)]
    cut_i = samp[~course.in_any(s[samp], course.tunnels)]
    H = np.full(len(pts), -1.0)
    CAP = np.full(len(pts), 9.0)
    for c0 in range(0, len(pts), 4000):
        q = pts[c0:c0 + 4000]
        dd = np.linalg.norm(q[:, None, :] - P[None, sup_i, :2], axis=-1)
        H[c0:c0 + 4000] = (P[sup_i, 2] - 0.006 - np.maximum(0.0, dd - (W[sup_i] / 2 + 0.012)) * 0.95).max(1)
        dc = np.linalg.norm(q[:, None, :] - P[None, cut_i, :2], axis=-1)
        # beside the walls the rock may rise again at a moderate slope: terraces, not slot canyons
        CAP[c0:c0 + 4000] = (P[cut_i, 2] - 0.004 + np.maximum(0.0, dc - (W[cut_i] / 2 + WALL_T + 0.004)) * 1.4).min(1)
    # mountains: rock faces beside high parts of the track, big peaks around the edge
    hills = []
    high = np.flatnonzero((P[:, 2] > 0.25) & ~helix_mask)
    for k in rng.choice(high, size=min(len(high), 9), replace=False) if len(high) else []:
        side = rng.choice([-1, 1])
        c = P[k, :2] + side * Lat[k, :2] / max(np.linalg.norm(Lat[k, :2]), 1e-9) * rng.uniform(0.11, 0.2)
        hills.append((c, P[k, 2] + rng.uniform(0.06, 0.25), rng.uniform(0.08, 0.16)))
    for _ in range(7):
        edge = rng.uniform(lo, hi)
        if rng.random() < 0.5:
            edge[0] = rng.choice([lo[0] + 0.3, hi[0] - 0.3])
        else:
            edge[1] = rng.choice([lo[1] + 0.3, hi[1] - 0.3])
        hills.append((edge, rng.uniform(0.5, 1.1), rng.uniform(0.25, 0.5)))
    for c, h, sg in hills:
        H = np.maximum(H, h * np.exp(-((pts - c) ** 2).sum(1) / (2 * sg**2)))
    # rocky relief
    H += 0.012 * np.sin(pts[:, 0] * 37 + 1.3) * np.sin(pts[:, 1] * 29) * (H > 0.05)
    H = np.maximum(H, 0.0)
    # keep the channel clear, and keep rock off the walls' inner side
    H = np.minimum(H, CAP)
    # tunnel: mountain over the track
    if tunnel_mask.any():
        tp = np.flatnonzero(tunnel_mask)
        tt = cKDTree(P[tp, :2])
        dt_, jt = tt.query(pts)
        cover = dt_ < W[tp[jt]] / 2 + 0.06
        H = np.where(cover, np.maximum(H, P[tp[jt], 2] + 0.08 + 0.02 * np.cos(dt_ * 20)), H)
    # the tower stands on the table: flatten under it
    if course.helix_axis:
        cx, cy, rad, z_lo, z_hi = course.helix_axis
        dist_axis = np.hypot(pts[:, 0] - cx, pts[:, 1] - cy)
        H = np.where(dist_axis < rad + 0.08, np.minimum(H, 0.0), H)
    Hg = H.reshape(X.shape)

    def height(x, y):
        fx = np.clip((np.asarray(x) - xs[0]) / grid, 0, len(xs) - 1.001)
        fy = np.clip((np.asarray(y) - ys[0]) / grid, 0, len(ys) - 1.001)
        ix, iy = fx.astype(int), fy.astype(int)
        ax, ay = fx - ix, fy - iy
        return (Hg[iy, ix] * (1 - ax) * (1 - ay) + Hg[iy, ix + 1] * ax * (1 - ay)
                + Hg[iy + 1, ix] * (1 - ax) * ay + Hg[iy + 1, ix + 1] * ax * ay)

    def terrain_normal(Xp, h=0.012):
        gx = (height(Xp[:, 0] + h, Xp[:, 1]) - height(Xp[:, 0] - h, Xp[:, 1])) / (2 * h)
        gy = (height(Xp[:, 0], Xp[:, 1] + h) - height(Xp[:, 0], Xp[:, 1] - h)) / (2 * h)
        nn = np.c_[-gx, -gy, np.ones_like(gx)]
        return nn / np.linalg.norm(nn, axis=1, keepdims=True)

    V = np.dstack([X, Y, Hg])
    a, b, c, d = V[:-1, :-1].reshape(-1, 3), V[:-1, 1:].reshape(-1, 3), V[1:, 1:].reshape(-1, 3), V[1:, :-1].reshape(-1, 3)
    # parking lot and golf patch: flat areas away from the track
    ctr = (a + b + c + d) / 4
    dq, _ = tree_all.query(ctr[:, :2])
    flat = (ctr[:, 2] < 0.004) & (dq > 0.18)
    lot_c = golf_c = None
    if flat.any():
        cand = ctr[flat]
        lot_c = cand[rng.integers(len(cand))]
        golf_c = cand[rng.integers(len(cand))]
        if np.hypot(*(golf_c - lot_c)[:2]) < 0.5:
            golf_c = None
    mesh.quads(a, b, c, d, TERRAIN)

    def terrain_material(X, n):
        """Per pixel: rock, grass, snow on peaks, plaster beside the track, asphalt, golf, sand."""
        dd, ii = tree_all.query(X[:, :2])
        ii = ii * 2
        rel = X[:, 2] - P[ii, 2]
        slope = 1 - np.abs(n[:, 2])
        nz = (np.sin(X[:, 0] * 23.1) * np.sin(X[:, 1] * 17.7 + 2) + np.sin(X[:, 0] * 7.3 + X[:, 1] * 9.1)) / 2
        nz2 = np.sin(X[:, 0] * 91 + np.sin(X[:, 1] * 57) * 2) * np.sin(X[:, 1] * 83 + np.sin(X[:, 0] * 61) * 2)
        m = np.full(len(X), GRASS, np.int16)
        m[(X[:, 2] > 0.035 + 0.01 * nz2) | (slope > 0.3)] = ROCK
        m[(m == ROCK) & (slope < 0.2) & (nz + 0.25 * nz2 > 0.35)] = GRASS
        m[(rel > 0.14 + 0.05 * nz + 0.02 * nz2) & (X[:, 2] > 0.3) & (slope < 0.35)] = SNOW
        half_w = W[ii] / 2 + WALL_T
        plaster = (dd > half_w + 0.004) & (dd < half_w + 0.05 + 0.02 * nz2) & (nz + 0.3 * nz2 > 0.3) & (X[:, 2] > 0.015)
        m[plaster] = SNOW
        low = X[:, 2] < 0.004
        if lot_c is not None:
            m[low & (np.abs(X[:, 0] - lot_c[0]) < 0.3) & (np.abs(X[:, 1] - lot_c[1]) < 0.2)] = ASPHALT
        if golf_c is not None:
            g = low & (np.hypot(X[:, 0] - golf_c[0], X[:, 1] - golf_c[1]) < 0.25 + 0.02 * nz2)
            m[g] = GOLF
            m[g & (np.hypot(X[:, 0] - golf_c[0] - 0.08, X[:, 1] - golf_c[1]) < 0.06)] = SAND
        return m

    # room floor around the table
    big = 6.0
    fl = np.array([[lo[0] - big, lo[1] - big, -0.75], [hi[0] + big, lo[1] - big, -0.75],
                   [hi[0] + big, hi[1] + big, -0.75], [lo[0] - big, hi[1] + big, -0.75]])
    mesh.quads(fl[None, 0], fl[None, 1], fl[None, 2], fl[None, 3], ROOM)
    # table sides
    for (p0, p1) in (((lo[0], lo[1]), (hi[0], lo[1])), ((hi[0], lo[1]), (hi[0], hi[1])),
                     ((hi[0], hi[1]), (lo[0], hi[1])), ((lo[0], hi[1]), (lo[0], lo[1]))):
        q = np.array([[*p0, 0], [*p1, 0], [*p1, -0.75], [*p0, -0.75]])
        mesh.quads(q[None, 0], q[None, 1], q[None, 2], q[None, 3], ROOM, (60, 70, 80))
    # ------------------------------------------------------------------ the printed track
    hw = W[:, None] / 2
    fl_l, fl_r = P + hw * Lat, P - hw * Lat
    top_li, top_ri = fl_l + WALL_H * U, fl_r + WALL_H * U
    top_lo, top_ro = top_li + WALL_T * Lat, top_ri - WALL_T * Lat
    bot_lo, bot_ro = fl_l + WALL_T * Lat - 0.004 * U, fl_r - WALL_T * Lat - 0.004 * U
    i0, i1 = np.arange(len(s) - 1), np.arange(1, len(s))
    mesh.quads(fl_l[i0], fl_l[i1], fl_r[i1], fl_r[i0], FLOOR)
    for (p, q_) in ((fl_l, top_li), (fl_r, top_ri), (top_li, top_lo), (top_ri, top_ro), (top_lo, bot_lo), (top_ro, bot_ro)):
        mesh.quads(p[i0], p[i1], q_[i1], q_[i0], WALL)
    mesh.quads(bot_lo[i0], bot_lo[i1], bot_ro[i1], bot_ro[i0], WALL)
    # support posts under the track where the terrain is far below
    for k in range(0, len(s), 40):
        gz = float(height(P[k, 0], P[k, 1]))
        if P[k, 2] - gz > 0.04:
            for side in (-1, 1):
                base = P[k] + side * (W[k] / 2 + WALL_T) * Lat[k]
                mesh.quads(*_box([base[0], base[1], gz], (0.006, 0.006, max(0.001, P[k, 2] - gz - 0.004)),
                                 np.arctan2(Lat[k, 1], Lat[k, 0])), POST)
    # ------------------------------------------------------------------ obstacles
    ob = course.obstacles
    for ps, pu, pr, kind in ob.posts:
        Pk, _, Lk, Uk, _ = course.frame(np.array([ps]))
        base = Pk[0] + pu * Lk[0]
        if kind == 0:
            mesh.quads(*_cylinder(base, pr, 0.02, 8), PIN)
        else:  # pinball bumper: dark body, coloured rubber ring, cap
            mesh.quads(*_cylinder(base, pr * 0.85, 0.022, 14), BUMPER)
            col = BUMPER_CAPS[int(rng.integers(len(BUMPER_CAPS)))]
            mesh.quads(*_cylinder(base + [0, 0, 0.006], pr, 0.008, 14), BUMPER, col)
            mesh.quads(*_sphere(base + [0, 0, 0.022], pr * 0.85, 12, squash=0.3), BUMPER, col)
    for s0, s1, u0, ht in ob.dividers:
        ss = np.arange(s0, s1, 0.01)
        Pd, _, Ld, Ud, _ = course.frame(ss)
        for side in (-1, 1):
            a_ = Pd + (u0 + side * ht) * Ld
            mesh.quads(a_[:-1], a_[1:], a_[1:] + 0.016 * Ud[1:], a_[:-1] + 0.016 * Ud[:-1], WALL)
        top = Pd + u0 * Ld + 0.016 * Ud
        mesh.quads(top[:-1] - ht * Ld[:-1], top[1:] - ht * Ld[1:], top[1:] + ht * Ld[1:], top[:-1] + ht * Ld[:-1], WALL)
    for bs, bh in ob.bumps:
        prof = np.linspace(-1, 1, 6)
        Pb, Tb, Lb_, Ub, Wb = course.frame(np.array([bs]))
        hw_ = Wb[0] / 2
        pts_ = [Pb[0] + x * 0.008 * Tb[0] + bh * np.sqrt(max(0, 1 - x * x)) * Ub[0] for x in prof]
        for a_, b_ in zip(pts_[:-1], pts_[1:]):
            mesh.quads((a_ - hw_ * Lb_[0])[None], (b_ - hw_ * Lb_[0])[None], (b_ + hw_ * Lb_[0])[None],
                       (a_ + hw_ * Lb_[0])[None], FLOOR)
    # ------------------------------------------------------------------ lift tower (enclosed) and the finish rack
    li = np.flatnonzero(lift_mask)
    if len(li):
        Pl, Ll, Ul = P[li], Lat[li], U[li]
        for side in (-1, 1):
            a_ = Pl + side * 0.035 * Ll - 0.01 * Ul
            b_ = a_ + 0.08 * Ul
            mesh.quads(a_[:-1], a_[1:], b_[1:], b_[:-1], TOWER)
        c_ = Pl - 0.035 * Ll + 0.07 * Ul
        d_ = Pl + 0.035 * Ll + 0.07 * Ul
        mesh.quads(c_[:-1], c_[1:], d_[1:], d_[:-1], TOWER)
    rk = course.rack
    base, out, along = np.asarray(rk["base"]), np.asarray(rk["out"]), np.asarray(rk["along"])
    side = np.cross([0.0, 0.0, 1.0], out)
    gz = float(height(base[0], base[1]))
    hgt = 10 * (2 * BALL_R + 0.001) + 0.03
    b0 = base.copy()
    b0[2] = min(base[2], gz)
    back = [b0 + out * 0.016 + side * x + [0, 0, z] for x, z in ((-0.03, 0), (0.03, 0), (0.03, hgt + base[2] - b0[2]), (-0.03, hgt + base[2] - b0[2]))]
    mesh.quads(*[np.array(v)[None] for v in back], RACK)
    for x in (-0.016, 0.016):  # slot rails
        mesh.quads(*_box(b0 + side * x, (0.004, 0.012, hgt + base[2] - b0[2]), np.arctan2(side[1], side[0])), RACK)
    # ------------------------------------------------------------------ spiral tower
    if course.helix_axis:
        cx, cy, rad, z_lo, z_hi = course.helix_axis
        hw_h = float(np.median(W[helix_mask])) / 2 if helix_mask.any() else 0.02
        open_dir = rng.uniform(0, 2 * np.pi)
        ang = np.linspace(open_dir + np.radians(55), open_dir + np.radians(305), 60)
        ro = rad + hw_h + WALL_T + 0.004
        ring = np.c_[cx + ro * np.cos(ang), cy + ro * np.sin(ang)]
        a = np.c_[ring[:-1], np.zeros(len(ring) - 1)]
        b = np.c_[ring[1:], np.zeros(len(ring) - 1)]
        mesh.quads(a, b, b + [0, 0, z_hi + 0.06], a + [0, 0, z_hi + 0.06], TOWER)
        # core with plaster on top
        ri = rad - hw_h - WALL_T - 0.002
        mesh.quads(*_cylinder([cx, cy, 0.0], ri, z_hi, 24), TOWER)
        top = _sphere([cx, cy, z_hi], ri, 16, squash=0.45)
        mesh.quads(*top, SNOW)
    # ------------------------------------------------------------------ props
    tree_track = cKDTree(P[:, :2])

    def free_spot(min_d, tries=200, flat_only=False):
        for _ in range(tries):
            p = rng.uniform(lo + 0.1, hi - 0.1)
            dd, ii = tree_track.query(p)
            gz = float(height(*p))
            if dd < min_d or (flat_only and gz > 0.01):
                continue
            if course.helix_axis and np.hypot(p[0] - course.helix_axis[0], p[1] - course.helix_axis[1]) < course.helix_axis[2] + 0.12:
                continue
            if gz > P[ii, 2] - 0.02 and dd < 0.15:
                continue  # a tree on a ledge above the track would hide it from every camera
            return np.array([p[0], p[1], gz])
        return None

    for _ in range(int(rng.integers(35, 55))):
        p = free_spot(0.07)
        if p is None:
            continue
        h = rng.uniform(0.04, 0.12)
        mesh.quads(*_cylinder(p, 0.004, h, 6), TRUNK)
        col = FOLIAGE_COLOURS[rng.integers(len(FOLIAGE_COLOURS))]
        r_f = rng.uniform(0.025, 0.055)
        mesh.quads(*_sphere(p + [0, 0, h + 0.6 * r_f], r_f, 10, squash=rng.uniform(0.8, 1.4)), FOLIAGE, col)
    if lot_c is not None:
        for row in (-1, 1):
            for k in range(int(rng.integers(4, 8))):
                c = np.array([lot_c[0] - 0.24 + k * 0.075, lot_c[1] + row * 0.08, 0.0])
                if rng.random() < 0.15:
                    continue
                col = CAR_COLOURS[rng.integers(len(CAR_COLOURS))]
                mesh.quads(*_box(c, (0.03, 0.065, 0.018), np.pi / 2), CAR, col)
                mesh.quads(*_box(c + [0, 0, 0.018], (0.026, 0.035, 0.012), np.pi / 2), CAR, col)
        for k in range(8):  # white bay lines
            x = lot_c[0] - 0.28 + k * 0.075
            q = np.array([[x, lot_c[1] - 0.15, 0.0015], [x + 0.004, lot_c[1] - 0.15, 0.0015],
                          [x + 0.004, lot_c[1] + 0.15, 0.0015], [x, lot_c[1] + 0.15, 0.0015]])
            mesh.quads(q[None, 0], q[None, 1], q[None, 2], q[None, 3], LINE)
    for _ in range(int(rng.integers(4, 8))):
        p = free_spot(0.08, flat_only=True)
        if p is not None:
            ang = np.linspace(0, 2 * np.pi, 9)
            base = np.c_[p[0] + 0.009 * np.cos(ang), p[1] + 0.009 * np.sin(ang), np.full(9, p[2])]
            tip = np.tile(p + [0, 0, 0.03], (8, 1))
            mesh.add(np.stack([base[:-1], base[1:], tip], 1), CONE)
    p = free_spot(0.25, flat_only=True)
    if p is not None:
        mesh.quads(*_box(p, (0.16, 0.1, 0.06), rng.uniform(0, np.pi)), HOUSE)
        mesh.quads(*_box(p + [0, 0, 0.06], (0.17, 0.11, 0.02), 0.0), ROOF)
    tris, mats, cols = mesh.build()
    light = np.array([-0.35, 0.45, 1.0])
    return Scene(tris=tris, mat=mats, colour=cols, height=height, bounds=(lo[0], lo[1], hi[0], hi[1]),
                 light=light / np.linalg.norm(light), terrain_material=terrain_material, terrain_normal=terrain_normal)
