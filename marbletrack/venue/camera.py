"""A venue camera: what an AIDA HD-NDI-IP67 or the OBSBOT Tail 2 delivers over NDI.

The static diorama is rasterised and shaded once per camera (in an ideal pinhole
image with a margin for the lens). Each frame then only redraws the regions
around balls, which are ray-traced per pixel:

* **Balls**: exact sphere silhouettes in perspective, the ball's own texture
  (colour, number discs, stripe) turning as it rolls, diffuse + glossy
  highlight + a faint reflection of the room, the mountains' shadow on them.
* **Shadows of the balls** on the track, and darkening where a ball touches it.
* **Rolling shutter**: every image row is exposed at its own time (the sensor
  reads top to bottom over ``readout`` seconds), so a moving ball is sheared and
  placed by its row's time, not the frame's.
* **Motion blur**: the exposure time is integrated with several samples.
* **Lens**: barrel distortion (CS-mount 4 mm on a 1/3" sensor), vignetting.
* **Sensor and encoder**: shot and read noise, the camera's own colour cast,
  sRGB gamma, then 4:2:0 chroma subsampling and block compression (NDI|HX is
  H.264; JPEG at a similar quality reproduces its blocks and colour bleeding).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from .balls import ball_albedo
from .course import BALL_R, Course
from .physics import Race, quat_to_mat
from .raster import View
from .scene import Scene
from .shading import AMBIENT, ENV, KEY, ShadowMap, lin_to_srgb, shade_static, srgb_to_lin


@dataclass
class Optics:
    size: tuple[int, int]
    hfov_deg: float
    k1: float
    k2: float
    fps: float = 60.0
    exposure: float = 1 / 1000
    readout: float = 0.0145  # top-to-bottom sensor readout (rolling shutter)
    read_noise: float = 0.006  # linear units
    shot_noise: float = 0.0008
    jpeg_quality: int = 80
    vignette: float = 0.18
    gain: float = 1.0
    model: str = ""


AIDA = Optics(size=(1920, 1080), hfov_deg=67.5, k1=-0.11, k2=0.025, model="AIDA HD-NDI-IP67 (4 mm)")
OBSBOT = Optics(size=(3840, 2160), hfov_deg=81.0, k1=-0.03, k2=0.0, readout=0.012, read_noise=0.005,
                shot_noise=0.0006, jpeg_quality=85, vignette=0.12, model="OBSBOT Tail 2 (wide)")


def look_at(C, target, roll=0.0):
    f = np.asarray(target, float) - C
    f /= np.linalg.norm(f)
    up = np.array([0.0, 0.0, 1.0]) if abs(f[2]) < 0.99 else np.array([0.0, 1.0, 0.0])
    right = np.cross(f, up)
    right /= np.linalg.norm(right)
    down = np.cross(f, right)
    if roll:
        c, s = np.cos(roll), np.sin(roll)
        right, down = c * right + s * down, -s * right + c * down
    return np.vstack([right, down, f])


@dataclass
class CameraSpec:
    cam_id: str
    optics: Optics
    C: np.ndarray
    R: np.ndarray
    wb: tuple[float, float, float] = (1.0, 1.0, 1.0)
    phase: float = 0.0  # start of frame 0 (s): cameras are not synchronised
    role: str = "section"  # section | finish | overhead
    s_hint: tuple[float, float] | None = None  # the stretch it was placed for
    principal: tuple[float, float] = (0.0, 0.0)  # principal point offset (px)
    meta: dict = field(default_factory=dict)


class VenueCamera:
    def __init__(self, spec: CameraSpec, scene: Scene, shadows: ShadowMap, course: Course, seed: int = 0,
                 n_noise: int = 3):
        self.spec, self.course, self.scene, self.shadows = spec, course, scene, shadows
        o = spec.optics
        self.cam_id, self.optics, self.fps, self.phase = spec.cam_id, o, o.fps, spec.phase
        W, H = o.size
        self.size = (W, H)
        self.f = (W / 2) / np.tan(np.radians(o.hfov_deg) / 2)
        self.cx, self.cy = W / 2 + spec.principal[0], H / 2 + spec.principal[1]
        self.R, self.C = spec.R, np.asarray(spec.C, float)
        # how far the ideal (undistorted) image reaches beyond the frame
        corners = np.array([[0, 0], [W, 0], [0, H], [W, H]], float)
        und = self._undistort_n((corners - [self.cx, self.cy]) / self.f) * self.f + [self.cx, self.cy]
        self.m = int(np.ceil(max(0.0, -und.min(), (und - [W, H]).max()))) + 8
        self.view = View(self.R, self.C, self.f, self.cx + self.m, self.cy + self.m, W + 2 * self.m, H + 2 * self.m)
        self.lit, self.amb, self.depth, self.pos = shade_static(self.view, scene, shadows)
        # distorted pixel (index coords) -> ideal canvas (index coords)
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        nd = np.stack([(xx + 0.5 - self.cx) / self.f, (yy + 0.5 - self.cy) / self.f], -1).reshape(-1, 2)
        nu = self._undistort_n(nd)
        self.map_x = (nu[:, 0] * self.f + self.cx + self.m - 0.5).reshape(H, W).astype(np.float32)
        self.map_y = (nu[:, 1] * self.f + self.cy + self.m - 0.5).reshape(H, W).astype(np.float32)
        r2 = ((xx - W / 2) ** 2 + (yy - H / 2) ** 2) / ((W / 2) ** 2 + (H / 2) ** 2)
        self.vig = ((1 - o.vignette * r2)[..., None] * np.asarray(spec.wb, np.float32) * o.gain).astype(np.float32)
        rng = np.random.default_rng(seed + 991)
        self.noise = [rng.standard_normal((H, W, 3)).astype(np.float16) for _ in range(n_noise)]
        lit_d = cv2.remap(self.lit, self.map_x, self.map_y, cv2.INTER_LINEAR)
        self._empty = [self._sensor(lit_d, k, (slice(None), slice(None)), 1.0) for k in range(n_noise)]
        self._n = 0

    # ------------------------------------------------------------------ geometry
    def _distort_n(self, p):
        r2 = (p**2).sum(-1, keepdims=True)
        return p * (1 + self.optics.k1 * r2 + self.optics.k2 * r2**2)

    def _undistort_n(self, p):
        u = p.copy()
        for _ in range(12):
            r2 = (u**2).sum(-1, keepdims=True)
            u = p / (1 + self.optics.k1 * r2 + self.optics.k2 * r2**2)
        return u

    def project(self, X):
        """World points -> distorted pixel coordinates (index convention) and camera depth."""
        Xc = (np.asarray(X, float) - self.C) @ self.R.T
        z = Xc[..., 2]
        n = Xc[..., :2] / np.maximum(z, 1e-9)[..., None]
        d = self._distort_n(n)
        return d * self.f + [self.cx - 0.5, self.cy - 0.5], z

    def ideal(self, X):
        """World points -> ideal canvas coordinates (index convention) and depth."""
        uv, z = self.view.project(X)
        return uv - 0.5, z

    def visible(self, X, margin=4.0):
        """Is a world point in the frame and not hidden by the static scene?"""
        uv, z = self.project(X)
        iu, z2 = self.ideal(X)
        W, H = self.size
        inside = (z > 0.02) & (uv[:, 0] >= margin) & (uv[:, 0] < W - margin) & (uv[:, 1] >= margin) & (uv[:, 1] < H - margin)
        out = np.zeros(len(X), bool)
        k = np.flatnonzero(inside)
        xi = np.clip(np.round(iu[k, 0]).astype(int), 0, self.depth.shape[1] - 1)
        yi = np.clip(np.round(iu[k, 1]).astype(int), 0, self.depth.shape[0] - 1)
        out[k] = z2[k] < self.depth[yi, xi] + 0.002
        return out

    def scale_at(self, X) -> float:
        _, z = self.project(np.asarray(X)[None])
        return float(self.f / z[0])

    # ------------------------------------------------------------------ sensor
    def _sensor(self, lin, k, sl, illumination, rows=None):
        x = lin * self.vig[sl] * illumination
        z = self.noise[k][sl].astype(np.float32)
        x = x + z * np.sqrt(self.optics.read_noise**2 + self.optics.shot_noise * np.clip(x, 0, None))
        img = np.clip(lin_to_srgb(x) + 0.5, 0, 255).astype(np.uint8)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, self.optics.jpeg_quality])
        return cv2.imdecode(buf, cv2.IMREAD_COLOR)

    # ------------------------------------------------------------------ frames
    def frame_times(self, duration: float) -> np.ndarray:
        return self.phase + np.arange(int(duration * self.fps)) / self.fps

    def render(self, race: Race | None, t: float, illumination: float = 1.0, parked=None, spinners: bool = True) -> np.ndarray:
        """The frame whose first row starts exposing at ``t``.

        ``parked`` instead places balls at rest: a list of (ball spec, world centre, 3x3 orientation).
        """
        k = self._n % len(self.noise)
        self._n += 1
        W, H = self.size
        objs = self._balls(race, t, parked) + (self._spinners(t) if spinners else [])
        out = self._base(k, illumination)
        if not objs:
            return out
        for cluster in self._clusters(objs):
            # distorted rectangle covering the cluster, on the 16 px block grid of the encoder
            ix0, iy0, ix1, iy1 = cluster["rect"]
            pts = np.array([[x, y] for x in np.linspace(ix0, ix1, 5) for y in np.linspace(iy0, iy1, 5)], float)
            n = (pts + 0.5 - [self.cx + self.m, self.cy + self.m]) / self.f
            dp = self._distort_n(n) * self.f + [self.cx - 0.5, self.cy - 0.5]
            x0 = int(max(0, np.floor(dp[:, 0].min() / 16) * 16 - 16))
            y0 = int(max(0, np.floor(dp[:, 1].min() / 16) * 16 - 16))
            x1 = int(min(W, np.ceil(dp[:, 0].max() / 16) * 16 + 16))
            y1 = int(min(H, np.ceil(dp[:, 1].max() / 16) * 16 + 16))
            if x1 <= x0 or y1 <= y0:
                continue
            mx, my = self.map_x[y0:y1, x0:x1], self.map_y[y0:y1, x0:x1]
            jx0, jy0 = max(0, int(np.floor(mx.min())) - 1), max(0, int(np.floor(my.min())) - 1)
            jx1 = min(self.depth.shape[1], int(np.ceil(mx.max())) + 2)
            jy1 = min(self.depth.shape[0], int(np.ceil(my.max())) + 2)
            patch = self._draw(cluster["objs"], t, jx0, jy0, jx1, jy1)
            dist = cv2.remap(patch, mx - jx0, my - jy0, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            out[y0:y1, x0:x1] = self._sensor(dist, k, (slice(y0, y1), slice(x0, x1)), illumination)
        return out

    def _base(self, k, illumination):
        """The empty frame at this brightness (cached: brightness changes slowly)."""
        if abs(illumination - 1.0) < 1e-6:
            return self._empty[k].copy()
        key = (k, round(illumination, 3))
        cache = self.__dict__.setdefault("_dim", {})
        if key not in cache:
            if len(cache) > 12:
                cache.clear()
            lit_d = cv2.remap(self.lit, self.map_x, self.map_y, cv2.INTER_LINEAR)
            cache[key] = self._sensor(lit_d, k, (slice(None), slice(None)), illumination)
        return cache[key].copy()

    def _rect(self, centres, radius, extra=()):
        """Ideal-canvas rectangle around spheres at ``centres`` (and extra points), or None if off-canvas."""
        uv, z = self.ideal(np.asarray(centres, float))
        if np.any(z < 0.03):
            return None
        rpx = self.f * radius / z.min() * 1.3 + 3
        allp = uv if not len(extra) else np.r_[uv, self.ideal(np.asarray(extra, float))[0]]
        x0, y0 = allp.min(0) - rpx
        x1, y1 = allp.max(0) + rpx
        cw, ch = self.depth.shape[1], self.depth.shape[0]
        if x1 < 0 or y1 < 0 or x0 > cw or y0 > ch:
            return None
        return (max(0, int(x0)), max(0, int(y0)), min(cw, int(x1) + 1), min(ch, int(y1) + 1))

    def _balls(self, race, t, parked):
        """Balls that may appear in this frame: positions at the start and end of the readout."""
        o = self.optics
        span = o.readout + o.exposure
        out = []
        if parked is not None:
            for spec, c, Rb in parked:
                out.append({"kind": "ball", "spec": spec, "cA": np.asarray(c, float), "cB": np.asarray(c, float),
                            "tA": t, "tB": t + span, "Rm": Rb, "omega": np.zeros(3), "tm": t + span / 2})
        elif race is not None:
            sA, uA, vA, qA, hA = race.state(t)
            sB, uB, vB, qB, hB = race.state(t + span)
            _, _, _, qM, _ = race.state(t + span / 2)
            cA = race.course.ball_centre(sA, uA, hA)
            cB = race.course.ball_centre(sB, uB, hB)
            _, _, _, Uf, _ = race.course.frame((sA + sB) / 2)
            vel = (cB - cA) / span
            slot = {b: k for k, b in enumerate(race.rack_order)}
            for b, spec in enumerate(race.balls):
                if t + span >= race.racked_at[b]:  # in the rack: at rest in its slot
                    c = race.rack_position(slot[b])
                    out.append({"kind": "ball", "spec": spec, "cA": c, "cB": c, "tA": t, "tB": t + span,
                                "Rm": quat_to_mat(qM[b]), "omega": np.zeros(3), "tm": t + span / 2})
                    continue
                out.append({"kind": "ball", "spec": spec, "cA": cA[b], "cB": cB[b], "tA": t, "tB": t + span,
                            "Rm": quat_to_mat(qM[b]), "omega": np.cross(Uf[b], vel[b]) / BALL_R, "tm": t + span / 2})
        keep = []
        Ld = self.scene.light
        for b in out:
            ends = np.array([b["cA"], b["cB"]])
            rect = self._rect(ends, BALL_R, ends - Ld * (2.2 * BALL_R / max(Ld[2], 0.2)))
            if rect is not None:
                b["rect"] = rect
                keep.append(b)
        return keep

    def _spinners(self, t):
        """Rotating spinners in view: each arm is a chain of small spheres (a capsule), the hub a sphere."""
        o = self.optics
        out = []
        for sp_s, sp_u, ln, n, w, ph in self.course.obstacles.spinners:
            P, T, L, U, _ = self.course.frame(np.array([sp_s]))
            hub = P[0] + sp_u * L[0] + 0.011 * U[0]
            rect = self._rect([hub], ln + 0.006)
            if rect is None:
                continue
            out.append({"kind": "spinner", "hub": hub, "T": T[0], "L": L[0], "len": ln, "arms": int(n),
                        "omega": w, "phase": ph, "rect": rect})
        return out

    @staticmethod
    def _clusters(objs):
        groups = [{"rect": b["rect"], "objs": [b]} for b in objs]
        merged = True
        while merged:
            merged = False
            for i in range(len(groups)):
                for j in range(i + 1, len(groups)):
                    a, b = groups[i]["rect"], groups[j]["rect"]
                    if a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]:
                        groups[i] = {"rect": (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])),
                                     "objs": groups[i]["objs"] + groups[j]["objs"]}
                        groups.pop(j)
                        merged = True
                        break
                if merged:
                    break
        return groups

    def _draw(self, objs, t, x0, y0, x1, y1):
        """Linear BGR of the ideal canvas region [x0:x1, y0:y1] with these objects, integrated over the exposure."""
        o = self.optics
        lit = self.lit[y0:y1, x0:x1]
        amb = self.amb[y0:y1, x0:x1]
        zs = self.depth[y0:y1, x0:x1]
        Xs = self.pos[y0:y1, x0:x1]
        h, w = zs.shape
        yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
        dc = np.stack([(xx + 0.5 - self.view.cx) / self.f, (yy + 0.5 - self.view.cy) / self.f, np.ones_like(xx)], -1)
        dc /= np.linalg.norm(dc, axis=-1, keepdims=True)
        dw = dc @ self.R.astype(np.float32)  # world directions
        row_t = t + np.clip((yy[:, :1] - self.m) / self.size[1], 0, 1) * o.readout  # rolling shutter: (h, 1)
        L = self.scene.light.astype(np.float32)
        key_share = lit - amb
        balls = [b for b in objs if b["kind"] == "ball"]
        spins = [b for b in objs if b["kind"] == "spinner"]
        # sub-rectangles of each object inside this region
        for b in objs:
            bx0, by0, bx1, by1 = b["rect"]
            b["sl"] = (slice(max(0, by0 - y0), max(0, min(h, by1 - y0))), slice(max(0, bx0 - x0), max(0, min(w, bx1 - x0))))
        # samples over the exposure: enough to keep each step under ~1 px of motion
        K = 1
        for b in balls:
            z_near = max(0.05, float(self.ideal(np.array([b["cA"]]))[1][0]))
            blur = np.linalg.norm(b["cB"] - b["cA"]) / (o.readout + o.exposure) * o.exposure * self.f / z_near
            K = max(K, int(np.ceil(blur)))
        for sp in spins:
            z_near = max(0.05, float(self.ideal(np.array([sp["hub"]]))[1][0]))
            K = max(K, int(np.ceil(abs(sp["omega"]) * sp["len"] * o.exposure * self.f / z_near)))
        K = int(np.clip(K, 1, 6))
        acc = np.zeros((h, w, 3), np.float32)
        for j in range(K):
            tp = row_t + (j + 0.5) / K * o.exposure  # (h, 1)
            best = zs.copy()
            col = np.zeros((h, w, 3), np.float32)
            hit_any = np.zeros((h, w), bool)
            keyf = np.ones((h, w), np.float32)
            aof = np.ones((h, w), np.float32)
            for b in balls:
                sy, sx = b["sl"]
                if sy.stop <= sy.start or sx.stop <= sx.start:
                    continue
                f = ((tp[sy] - b["tA"]) / (b["tB"] - b["tA"]))[..., None]  # (hh, 1, 1)
                c = b["cA"] + (b["cB"] - b["cA"]) * f  # centre at each row's time
                d = dw[sy, sx]
                oc = self.C - c
                bb = (d * oc).sum(-1)
                cc = (oc * oc).sum(-1) - BALL_R**2
                disc = bb * bb - cc
                th = -bb - np.sqrt(np.maximum(disc, 0))
                zc = th * dc[sy, sx, 2]
                hit = (disc > 0) & (th > 0) & (zc < best[sy, sx] - 1e-4)
                if hit.any():
                    best[sy, sx] = np.where(hit, zc, best[sy, sx])
                    cs = np.broadcast_to(c, d.shape)[hit]
                    X = self.C + th[hit][:, None] * d[hit]
                    n = (X - cs) / BALL_R
                    ang = (np.broadcast_to(tp[sy], hit.shape)[hit] - b["tm"]) * np.linalg.norm(b["omega"])
                    if np.linalg.norm(b["omega"]) > 1e-9:
                        kx = b["omega"] / np.linalg.norm(b["omega"])
                        ca, sa = np.cos(-ang)[:, None], np.sin(-ang)[:, None]
                        nr = n * ca + np.cross(kx, n) * sa + kx * (n @ kx)[:, None] * (1 - ca)
                    else:
                        nr = n
                    nl = nr @ b["Rm"]  # world -> ball frame
                    alb = srgb_to_lin(ball_albedo(b["spec"], nl))
                    vis = self.shadows.visibility(X, bias=0.003)
                    hv = L - d[hit]
                    hv /= np.linalg.norm(hv, axis=-1, keepdims=True)
                    ndl = np.clip(n @ L, 0, None)
                    ndh = np.clip((n * hv).sum(-1), 0, None)
                    ndv = np.clip(-(n * d[hit]).sum(-1), 0, 1)
                    fres = 0.04 + 0.96 * (1 - ndv) ** 5
                    hemi = AMBIENT * (0.6 + 0.4 * n[:, 2])
                    c_ = alb * (hemi + KEY * ndl * vis)[:, None]
                    c_ += (KEY * 0.9 * vis * ndh**120 + KEY * 0.12 * vis * ndh**12)[:, None]
                    c_ = c_ * (1 - fres[:, None] * 0.6) + ENV * (fres * 0.6)[:, None]
                    sub_col = col[sy, sx]
                    sub_col[hit] = c_
                    col[sy, sx] = sub_col
                    hit_any[sy, sx] |= hit
                # the ball's shadow and contact darkening on the static scene
                wv = c - Xs[sy, sx]
                proj = (wv * L).sum(-1)
                perp = np.sqrt(np.maximum((wv * wv).sum(-1) - proj**2, 0))
                keyf[sy, sx] *= 1 - (proj > 0) * np.clip((BALL_R * 1.2 - perp) / (BALL_R * 0.45), 0, 1)
                gap = np.linalg.norm(wv, axis=-1) - BALL_R
                aof[sy, sx] *= 1 - 0.55 * np.exp(-(np.maximum(gap, 0) / (0.45 * BALL_R)) ** 2)
            for sp in spins:
                sy, sx = sp["sl"]
                if sy.stop <= sy.start or sx.stop <= sx.start:
                    continue
                d = dw[sy, sx]
                tt = tp[sy][..., None]  # (hh, 1, 1)
                spheres = [(np.broadcast_to(sp["hub"], (1, 1, 3)), 0.006)]
                for a in range(sp["arms"]):
                    ang = sp["phase"] + sp["omega"] * tt + 2 * np.pi * a / sp["arms"]
                    direc = np.cos(ang) * sp["T"] + np.sin(ang) * sp["L"]
                    for dd in np.linspace(0.006, sp["len"], max(3, int(sp["len"] / 0.004))):
                        spheres.append((sp["hub"] + dd * direc, 0.0032))
                for c, rad in spheres:
                    oc = self.C - c
                    bb = (d * oc).sum(-1)
                    cc = (oc * oc).sum(-1) - rad**2
                    disc = bb * bb - cc
                    th = -bb - np.sqrt(np.maximum(disc, 0))
                    zc = th * dc[sy, sx, 2]
                    hit = (disc > 0) & (th > 0) & (zc < best[sy, sx] - 1e-4)
                    if hit.any():
                        best[sy, sx] = np.where(hit, zc, best[sy, sx])
                        X = self.C + th[hit][:, None] * d[hit]
                        n = (X - np.broadcast_to(c, d.shape)[hit]) / rad
                        alb = srgb_to_lin(np.array([92.0, 93.0, 98.0]))
                        shade = AMBIENT * (0.6 + 0.4 * n[:, 2]) + KEY * np.clip(n @ L, 0, None) * self.shadows.visibility(X)
                        sub_col = col[sy, sx]
                        sub_col[hit] = alb[None, :] * shade[:, None]
                        col[sy, sx] = sub_col
                        hit_any[sy, sx] |= hit
            static = amb * aof[..., None] + key_share * (keyf * aof)[..., None]
            acc += np.where(hit_any[..., None], col, static)
        return acc / K
