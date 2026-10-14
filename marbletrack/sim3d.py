"""Angled perspective cameras: a real pinhole model with depth, for oblique views.

Compared with the top-down rig, every effect of a camera mounted at an angle is
present: perspective scale that changes across the image, elliptical/skewed
marble silhouettes, the 12.5 mm height of a marble's centre above the track
(parallax), track walls that hide the lower part of marbles in the near lane,
tunnels seen from the side, and marbles that hide each other along the line of
sight. Static geometry (table, track, walls, tunnels) is rasterised once into a
colour image and a depth buffer; marbles are drawn far-to-near and only where
they are nearer than the static depth.
"""
from __future__ import annotations

import cv2
import numpy as np

from .color import ColorModel, bgr_to_lab
from .detector import CameraCalibration, foreground_mask
from .sim import TABLE_BGR, TRACK_BGR, TUNNEL_BGR, MarbleSpec, RaceConfig, RaceSimulator
from .track import Polyline

WALL_H = 0.015  # track side walls (m)
TUNNEL_H = 0.05
WALL_BGR = np.array((178, 178, 182), float)
LIGHT = np.array([-0.4, 0.5, 1.0]) / np.linalg.norm([-0.4, 0.5, 1.0])


def _fib_sphere(n=160):
    k = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * k / n)
    th = np.pi * (1 + 5**0.5) * k
    return np.c_[np.cos(th) * np.sin(phi), np.sin(th) * np.sin(phi), np.cos(phi)]


SPHERE = _fib_sphere()


class AngledCamera:
    """Pinhole camera looking at a track section from ``elevation`` degrees above horizontal."""

    def __init__(self, cam_id, track: Polyline, cfg: RaceConfig, s_range, rng, size=(640, 480), hfov_deg=70.0,
                 elevation_deg=45.0, azimuth_offset_deg=90.0, gains=(1, 1, 1), vignette=0.1, noise=4.0,
                 phase=0.0, fps=None, margin=0.05):
        self.cam_id, self.track, self.cfg, self.s_range = cam_id, track, cfg, s_range
        self.size, self.phase, self.fps = size, phase, fps
        w, h = size
        f = (w / 2) / np.tan(np.radians(hfov_deg) / 2)
        self.K = np.array([[f, 0, w / 2], [0, f, h / 2], [0, 0, 1.0]])
        r = cfg.marble_radius
        lo, hi = s_range
        line = track.section(lo, hi)
        mid_xy, mid_t = track.point_at(np.array([(lo + hi) / 2]))
        heading = np.arctan2(mid_t[0, 1], mid_t[0, 0])
        pts = line.points
        self.target = np.r_[(pts.min(0) + pts.max(0)) / 2, r]
        az = heading + np.radians(azimuth_offset_deg)  # where the camera stands, seen from the target
        el = np.radians(elevation_deg)
        self.view_dir = np.array([np.cos(az) * np.cos(el), np.sin(az) * np.cos(el), np.sin(el)])
        # Smallest distance at which the whole section (incl. walls and marble tops) fits the frame.
        normal = np.c_[-np.gradient(pts[:, 1]), np.gradient(pts[:, 0])]
        normal /= np.linalg.norm(normal, axis=1, keepdims=True)
        probe = np.vstack([np.c_[pts + o * normal, np.full(len(pts), z)]
                           for o in (-cfg.track_width / 2 - 0.01, cfg.track_width / 2 + 0.01) for z in (0.0, 2 * r)])
        lo_d, hi_d = 0.1, 20.0
        for _ in range(40):
            d = (lo_d + hi_d) / 2
            self._pose(self.target + d * self.view_dir)
            uv, z = self.project(probe)
            ok = np.all(z > 0) and np.all((uv[:, 0] > margin * w) & (uv[:, 0] < (1 - margin) * w)
                                          & (uv[:, 1] > margin * h) & (uv[:, 1] < (1 - margin) * h))
            lo_d, hi_d = (lo_d, d) if ok else (d, hi_d)
        self._pose(self.target + hi_d * self.view_dir)
        self.distance = hi_d

        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        rr = ((xx - w / 2) ** 2 + (yy - h / 2) ** 2) / ((w / 2) ** 2 + (h / 2) ** 2)
        self.gain = (1 - vignette * rr)[..., None] * np.asarray(gains, np.float32)
        self.noise = [rng.normal(0, noise, (h, w, 3)).astype(np.float32) for _ in range(6)]
        self._build_static()
        self._empty = [self._finish(self.clean, n) for n in self.noise]
        self._n = 0

    # -- geometry -------------------------------------------------------------
    def _pose(self, C):
        f = self.target - C
        f /= np.linalg.norm(f)
        right = np.cross(f, [0.0, 0.0, 1.0])
        right /= np.linalg.norm(right)
        down = np.cross(f, right)
        self.R = np.vstack([right, down, f])
        self.C = np.asarray(C, float)

    @property
    def position(self):
        return self.C

    def project(self, P):
        """World points (N, 3) -> pixels (N, 2) and depth along the optical axis (N,)."""
        Xc = (np.asarray(P, float) - self.C) @ self.R.T
        z = Xc[:, 2]
        uv = (Xc[:, :2] / np.maximum(z, 1e-9)[:, None]) * [self.K[0, 0], self.K[1, 1]] + self.K[:2, 2]
        return uv, z

    def _ground_depth(self):
        w, h = self.size
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
        rays_c = np.stack([(xx - self.K[0, 2]) / self.K[0, 0], (yy - self.K[1, 2]) / self.K[1, 1], np.ones_like(xx)], -1)
        rays_w = rays_c @ self.R
        t = -self.C[2] / np.minimum(rays_w[..., 2], -1e-9)
        return np.where(rays_w[..., 2] < 0, t, 1e9).astype(np.float32)

    def footprint(self) -> list[list[float]]:
        w, h = self.size
        out = []
        for u, v in ((0, 0), (w, 0), (w, h), (0, h)):
            ray = np.array([(u - self.K[0, 2]) / self.K[0, 0], (v - self.K[1, 2]) / self.K[1, 1], 1.0]) @ self.R
            t = -self.C[2] / min(ray[2], -1e-6)
            p = self.C + min(t, 6.0) * ray
            out.append([round(float(p[0]), 4), round(float(p[1]), 4)])
        return out

    def _band(self, s0, s1, off0, off1, z0, z1, step=0.02):
        """Quads (N, 4, 3) of a strip along the track between two offsets/heights."""
        s = np.linspace(s0, s1, max(2, int(np.ceil((s1 - s0) / step)) + 1))
        xy, tan = self.track.point_at(s)
        nrm = np.c_[-tan[:, 1], tan[:, 0]]
        a = np.c_[xy + off0 * nrm, np.full(len(s), z0)]
        b = np.c_[xy + off1 * nrm, np.full(len(s), z1)]
        return np.stack([a[:-1], b[:-1], b[1:], a[1:]], axis=1)

    def _build_static(self):
        w, h = self.size
        img = np.full((h, w, 3), TABLE_BGR, np.uint8)
        depth = self._ground_depth()
        L, tw = self.track.s_range[1], self.cfg.track_width
        items = []  # (quads, colour, writes_depth)
        items.append((self._band(0, L, -tw / 2, tw / 2, 0, 0), np.array(TRACK_BGR, float), False))
        for o in (-tw / 2, tw / 2):
            items.append((self._band(0, L, o, o, 0, WALL_H), WALL_BGR, True))
        for a, b in self.cfg.tunnels:
            items.append((self._band(a, b, -0.9 * tw, 0.9 * tw, TUNNEL_H, TUNNEL_H), np.array(TUNNEL_BGR, float), True))
            for o in (-0.9 * tw, 0.9 * tw):
                items.append((self._band(a, b, o, o, 0, TUNNEL_H), np.array(TUNNEL_BGR, float) * 0.85, True))
        quads = []
        for q, col, wd in items:
            uv, z = self.project(q.reshape(-1, 3))
            uv, z = uv.reshape(-1, 4, 2), z.reshape(-1, 4)
            vis = np.all(z > 0.01, axis=1) & np.any((uv[..., 0] > -50) & (uv[..., 0] < w + 50)
                                                    & (uv[..., 1] > -50) & (uv[..., 1] < h + 50), axis=1)
            for k in np.flatnonzero(vis):
                quads.append((float(z[k].mean()), uv[k], col, wd))
        quads.sort(key=lambda x: -x[0])  # painter's algorithm, far to near
        for zq, uv, col, wd in quads:
            poly = np.round(uv * 16).astype(np.int32)
            cv2.fillConvexPoly(img, poly, tuple(float(c) for c in col), cv2.LINE_AA, 4)
            if wd:
                m = np.zeros((h, w), np.uint8)
                cv2.fillConvexPoly(m, poly, 1, cv2.LINE_8, 4)
                depth[m > 0] = np.minimum(depth[m > 0], zq)
        self.clean, self.depth = img, depth

    def _finish(self, img, noise, sl=(slice(None), slice(None))):
        out = img[sl].astype(np.float32) * self.gain[sl] + noise[sl]
        return np.clip(out, 0, 255).astype(np.uint8)

    def sphere_silhouette(self, center, scale=1.0, shift=np.zeros(3)):
        uv, z = self.project(center + shift + scale * self.cfg.marble_radius * SPHERE)
        return cv2.convexHull(uv.astype(np.float32)).reshape(-1, 2), z

    def scale_at(self, xy) -> float:
        p = np.c_[np.array([xy, xy + (1e-3, 0), xy + (0, 1e-3)]), np.full(3, self.cfg.marble_radius)]
        uv, _ = self.project(p)
        return float((np.linalg.norm(uv[1] - uv[0]) + np.linalg.norm(uv[2] - uv[0])) / 2e-3)

    # -- rendering ------------------------------------------------------------
    def render(self, xy_world, visible, colors, illumination: float = 1.0) -> np.ndarray:
        w, h = self.size
        r = self.cfg.marble_radius
        k = self._n % len(self.noise)
        self._n += 1
        marbles = []
        for xy, vis, col in zip(xy_world, visible, colors):
            if not vis:
                continue
            c = np.r_[xy, r]
            _, zc = self.project(c[None])
            if zc[0] <= 0.02:
                continue
            marbles.append((zc[0], c, np.asarray(col, float)))
        out = self._empty[k].copy()
        if not marbles:
            return out if illumination == 1.0 else cv2.convertScaleAbs(out, alpha=illumination)
        img = self.clean.copy()
        patches = []
        for zc, c, col in sorted(marbles, key=lambda m: -m[0]):  # far to near: nearer marbles cover farther ones
            hull, _ = self.sphere_silhouette(c)
            x0, y0 = np.floor(hull.min(0)).astype(int) - 2
            x1, y1 = np.ceil(hull.max(0)).astype(int) + 3
            x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
            if x1 <= x0 or y1 <= y0:
                continue
            sl = (slice(y0, y1), slice(x0, x1))
            patch = img[sl].copy()
            mask = np.zeros(patch.shape[:2], np.uint8)
            off = np.array([x0, y0])
            layers = [(hull, col * 0.7),
                      (self.sphere_silhouette(c, 0.78, 0.1 * r * LIGHT)[0], col),
                      (self.sphere_silhouette(c, 0.22, 0.65 * r * LIGHT)[0], np.minimum(255, col * 0.3 + 200))]
            for poly, colour in layers:
                p16 = np.round((poly - off) * 16).astype(np.int32)
                cv2.fillConvexPoly(patch, p16, tuple(float(v) for v in colour), cv2.LINE_AA, 4)
            cv2.fillConvexPoly(mask, np.round((hull - off) * 16).astype(np.int32), 1, cv2.LINE_8, 4)
            # Static geometry nearer than the marble's front surface (walls, tunnel) hides it.
            visible_px = (mask > 0) & (self.depth[sl] > zc - r)
            img[sl][visible_px] = patch[visible_px]
            patches.append(sl)
        for sl in patches:
            out[sl] = self._finish(img, self.noise[k], sl)
        if illumination != 1.0:
            out = cv2.convertScaleAbs(out, alpha=illumination)
        return out

    # -- calibration ----------------------------------------------------------
    def calibration(self, marbles: list[MarbleSpec], sim: RaceSimulator, s_bias: float = 0.0) -> CameraCalibration:
        """What the on-site calibration tool would produce for this camera.

        The centerline is calibrated at marble-centre height (the installer rolls
        a marble along the track), which removes the height parallax of an
        oblique view. Expected blob area is tabulated along the section because
        perspective makes near marbles larger than far ones.
        """
        r, tw = self.cfg.marble_radius, self.cfg.track_width
        w, h = self.size
        line = self.track.section(*self.s_range)
        uv, _ = self.project(np.c_[line.points, np.full(len(line.points), r)])
        # Image direction of the track's cross-axis at each waypoint (px per metre):
        # the calibration tool gets it from the two clicked/detected track edges.
        _, tan_l = self.track.point_at(line.s)
        nrm_l = np.c_[-tan_l[:, 1], tan_l[:, 0]]
        uv_n, _ = self.project(np.c_[line.points + 0.01 * nrm_l, np.full(len(line.points), r)])
        img_line = Polyline(uv, line.s + s_bias, normals=(uv_n - uv) / 0.01)
        mask = np.zeros((h, w), np.uint8)
        for z0 in (0.0, 2 * r):
            for q in self._band(*self.s_range, -tw / 2 - 0.004, tw / 2 + 0.004, z0, z0, 0.01):
                p, _ = self.project(q)
                cv2.fillConvexPoly(mask, np.round(p).astype(np.int32), 255)
        mask = cv2.dilate(mask, np.ones((5, 5), np.uint8))
        s_samp = np.linspace(*self.s_range, 25)
        xy, tan = self.track.point_at(s_samp)
        nrm = np.c_[-tan[:, 1], tan[:, 0]]
        area, lat = [], []
        for p, n in zip(xy, nrm):
            hull, _ = self.sphere_silhouette(np.r_[p, r])
            area.append(cv2.contourArea(hull.astype(np.float32)))
            uv3, _ = self.project(np.array([np.r_[p, r], np.r_[p + 0.6 * tw * n, r], np.r_[p - 0.6 * tw * n, r]]))
            lat.append(max(np.linalg.norm(uv3[1] - uv3[0]), np.linalg.norm(uv3[2] - uv3[0])))
        empty = np.zeros(len(marbles), bool)
        bg = np.median(np.stack([self.render([], empty, []) for _ in range(5)]), axis=0).astype(np.uint8)
        calib = CameraCalibration(
            cam_id=self.cam_id, centerline=img_line, track_mask=mask,
            marble_radius_px=float(np.sqrt(min(area) / np.pi)), max_lateral_px=0.6 * tw,
            background=bg, area_s=s_samp + s_bias, area_px=np.array(area))
        s_lo, s_hi = self.s_range
        samples = np.linspace(s_lo + 0.05, s_hi - 0.05, 8)
        for spec in marbles:
            pix = []
            for i, s in enumerate(samples):
                p, t = self.track.point_at(np.array([s]))
                n = np.array([-t[0, 1], t[0, 0]])
                pos = p[0] + (i % 3 - 1) * 0.008 * n
                frame = self.render([pos], [True], [spec.bgr])
                fg = foreground_mask(frame, bg, 30)
                hull, _ = self.sphere_silhouette(np.r_[pos, r])
                m = np.zeros((h, w), np.uint8)
                cv2.fillConvexPoly(m, np.round(hull).astype(np.int32), 1)
                yy, xx = np.nonzero(fg & m)
                if len(yy) >= 5:
                    pix.append(frame[yy, xx])
            calib.palette.append(ColorModel.fit(bgr_to_lab(np.concatenate(pix))))
        return calib


# Viewing directions relative to the local track heading, cycled over the 14 cameras:
# from behind, from the left, diagonal, from the right, from ahead, ...
AZIMUTHS = (180.0, 90.0, 135.0, -90.0, 0.0, -135.0, 45.0, -45.0)


def angled_camera_rig(track, cfg, n=14, seed=0, size=(640, 480), overlap=0.12, gap_after=9, gap=0.2,
                      fps=30.0, elevation_deg=45.0):
    rng = np.random.default_rng(seed + 200)
    L = track.s_range[1]
    bounds = np.linspace(0, L, n + 1)
    cams = []
    for k in range(n):
        lo = max(0.0, bounds[k] - overlap / 2)
        hi = min(L, bounds[k + 1] + overlap / 2)
        if k == gap_after:
            hi = bounds[k + 1] - gap / 2
        if k == gap_after + 1:
            lo = bounds[k] + gap / 2
        cams.append(AngledCamera(f"cam{k + 1:02d}", track, cfg, (lo, hi), rng, size=size,
                                 elevation_deg=elevation_deg + rng.uniform(-5, 5),
                                 azimuth_offset_deg=AZIMUTHS[k % len(AZIMUTHS)] + rng.uniform(-10, 10),
                                 gains=rng.uniform(0.85, 1.15, 3), phase=rng.uniform(0, 1 / fps)))
    return cams


def angled_finish_camera(track, cfg, s_finish, seed=0, fps=240.0, size=(480, 240), half_span=0.15):
    """Finish camera beside the line, 45 degrees up, looking across the track."""
    rng = np.random.default_rng(seed + 300)
    lo, hi = s_finish - half_span, min(track.s_range[1], s_finish + half_span)
    return AngledCamera("finish", track, cfg, (lo, hi), rng, size=size, hfov_deg=50.0, elevation_deg=45.0,
                        azimuth_offset_deg=90.0, gains=rng.uniform(0.9, 1.1, 3), noise=5.0,
                        phase=rng.uniform(0, 1 / fps), fps=fps, margin=0.03)
