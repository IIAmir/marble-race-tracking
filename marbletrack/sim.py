"""Synthetic marble race + camera rigs, used to validate the pipeline end to end.

The simulator renders what fixed cameras would see: a grey track on a dark
table, shaded marbles with specular highlights, a tunnel that hides the track,
per-camera colour casts, vignetting, sensor noise and unsynchronised shutters.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .color import ColorModel, bgr_to_lab
from .detector import CameraCalibration, foreground_mask
from .track import Polyline

TABLE_BGR = (40, 62, 44)
TRACK_BGR = (135, 135, 135)
TUNNEL_BGR = (40, 70, 110)


@dataclass(frozen=True)
class MarbleSpec:
    marble_id: str
    bgr: tuple[int, int, int]


DEFAULT_MARBLES = [
    MarbleSpec("Ruby", (45, 40, 215)),
    MarbleSpec("Amber", (20, 130, 245)),
    MarbleSpec("Sun", (40, 215, 235)),
    MarbleSpec("Leaf", (60, 170, 40)),
    MarbleSpec("Teal", (190, 180, 30)),
    MarbleSpec("Sky", (235, 150, 60)),
    MarbleSpec("Navy", (150, 40, 30)),
    MarbleSpec("Violet", (170, 50, 130)),
    MarbleSpec("Rose", (170, 80, 240)),
    MarbleSpec("Snow", (245, 245, 245)),
]


@dataclass
class RaceConfig:
    width: float = 3.0  # table size (m)
    height: float = 2.0
    rows: int = 5  # serpentine rows
    margin: float = 0.2
    track_width: float = 0.05
    marble_radius: float = 0.0125
    tunnels: tuple[tuple[float, float], ...] = ((3.6, 4.0),)
    finish_before_end: float = 0.2
    hold: float = 0.3  # start gate opens after this many seconds
    speed_range: tuple[float, float] = (1.3, 1.6)
    wobble: float = 0.25  # relative speed variation along the track (drives overtakes)
    lateral_amp: float = 0.012  # how far marbles wander across the track (m)
    light_ramp: tuple[float, float, float] | None = None  # (t0, t1, final brightness factor)

    def illumination(self, t: float) -> float:
        if self.light_ramp is None:
            return 1.0
        t0, t1, f = self.light_ramp
        return 1.0 + (f - 1.0) * float(np.clip((t - t0) / (t1 - t0), 0.0, 1.0))


def serpentine_track(cfg: RaceConfig) -> Polyline:
    pitch = (cfg.height - 2 * cfg.margin) / (cfg.rows - 1)
    r = pitch / 2
    x0, x1 = cfg.margin + r, cfg.width - cfg.margin - r
    pts = []
    for row in range(cfg.rows):
        y = cfg.margin + row * pitch
        xs = (x0, x1) if row % 2 == 0 else (x1, x0)
        pts += [(xs[0], y), (xs[1], y)]
        if row < cfg.rows - 1:
            sign = 1 if row % 2 == 0 else -1
            ang = np.linspace(-np.pi / 2, np.pi / 2, 40)[1:-1]
            cx = xs[1]
            pts += [(cx + sign * r * np.cos(a), y + r + r * np.sin(a)) for a in ang]
    return Polyline.from_points(pts).resample(0.01)


class RaceSimulator:
    """Ground-truth marble trajectories in track coordinates."""

    def __init__(self, track: Polyline, marbles: list[MarbleSpec], cfg: RaceConfig, seed: int = 0):
        self.track, self.marbles, self.cfg = track, marbles, cfg
        rng = np.random.default_rng(seed)
        k = len(marbles)
        grid = rng.permutation(k)
        s = 0.05 + 0.03 * (k - 1 - grid).astype(float)  # staggered behind the gate
        base = rng.uniform(*cfg.speed_range, k)
        wobble_len = rng.uniform(1.2, 3.0, k)
        wobble_phase = rng.uniform(0, 2 * np.pi, k)
        self._lat_period = rng.uniform(0.8, 2.0, k)
        self._lat_phase = rng.uniform(0, 2 * np.pi, k)

        dt = 1e-3
        L = track.s_range[1]
        times, traj = [0.0], [s.copy()]
        t = 0.0
        while np.any(s < L) and t < 120:
            t += dt
            ramp = np.clip((t - cfg.hold) / 0.4, 0.0, 1.0)
            v = base * (1 + cfg.wobble * np.sin(2 * np.pi * s / wobble_len + wobble_phase)) * ramp
            s = np.minimum(s + v * dt, L)
            times.append(t)
            traj.append(s.copy())
        self.times = np.array(times)
        self.traj = np.array(traj)  # (T, K)
        self.duration = t

    def progress(self, t: float) -> np.ndarray:
        return np.array([np.interp(t, self.times, self.traj[:, k]) for k in range(len(self.marbles))])

    def lateral(self, t: float) -> np.ndarray:
        return self.cfg.lateral_amp * np.sin(2 * np.pi * t / self._lat_period + self._lat_phase)

    def finish_times(self, s_finish: float) -> np.ndarray:
        return np.array([np.interp(s_finish, self.traj[:, k], self.times) for k in range(len(self.marbles))])

    def world_positions(self, t: float):
        s = self.progress(t)
        xy, tan = self.track.point_at(s)
        normal = np.stack([-tan[:, 1], tan[:, 0]], axis=1)
        visible = (s > 0.02) & (s < self.track.s_range[1] - 0.03)  # removed at the run-out
        return xy + self.lateral(t)[:, None] * normal, visible


def _apply_h(H, pts):
    pts = np.asarray(pts, dtype=np.float64).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(pts, H).reshape(-1, 2)


class SimCamera:
    def __init__(self, cam_id, H, size, s_range, track, cfg, rng, gains=(1, 1, 1),
                 vignette=0.0, noise=4.0, phase=0.0, fps=None):
        self.cam_id, self.H, self.size, self.s_range = cam_id, H, size, s_range
        self.track, self.cfg, self.phase, self.fps = track, cfg, phase, fps
        w, h = size
        img = np.full((h, w, 3), TABLE_BGR, np.uint8)
        self._draw_track(img, track, cfg.track_width, TRACK_BGR)
        self.clean = img
        self.occ_img = np.zeros_like(img)
        self.occ_mask = np.zeros((h, w), np.uint8)
        for a, b in cfg.tunnels:
            sec = track.section(a, b)
            self._draw_track(self.occ_img, sec, cfg.track_width * 1.8, TUNNEL_BGR)
            self._draw_track(self.occ_mask, sec, cfg.track_width * 1.8, 255)
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        rr = ((xx - w / 2) ** 2 + (yy - h / 2) ** 2) / ((w / 2) ** 2 + (h / 2) ** 2)
        self.gain = (1 - vignette * rr)[..., None] * np.asarray(gains, np.float32)
        self.noise = [rng.normal(0, noise, (h, w, 3)).astype(np.float32) for _ in range(6)]
        occluded = self.clean.copy()
        cv2.copyTo(self.occ_img, self.occ_mask, occluded)
        # Fully post-processed empty frames; marbles only touch small patches.
        self._empty = [self._finish(occluded, n) for n in self.noise]
        self._n = 0

    def footprint(self) -> list[list[float]]:
        """World-plane corners of this camera's image (what it can see on the table)."""
        w, h = self.size
        inv = np.linalg.inv(self.H)
        pts = _apply_h(inv, [(0, 0), (w, 0), (w, h), (0, h)])
        return [[round(float(x), 4), round(float(y), 4)] for x, y in pts]

    def scale_at(self, xy) -> float:
        p = _apply_h(self.H, [xy, xy + (1e-3, 0), xy + (0, 1e-3)])
        return float((np.linalg.norm(p[1] - p[0]) + np.linalg.norm(p[2] - p[0])) / 2e-3)

    def _draw_track(self, img, line: Polyline, width, color):
        pts = _apply_h(self.H, line.points)
        mid = line.points[len(line.points) // 2]
        th = max(1, int(round(width * self.scale_at(mid))))
        cv2.polylines(img, [np.round(pts * 16).astype(np.int32)], False, color, th, cv2.LINE_AA, 4)

    def _finish(self, img, noise, sl=(slice(None), slice(None))):
        out = img[sl].astype(np.float32) * self.gain[sl] + noise[sl]
        return np.clip(out, 0, 255).astype(np.uint8)

    def render(self, xy_world, visible, colors, illumination: float = 1.0) -> np.ndarray:
        img = self.clean.copy()
        w, h = self.size
        patches = []
        for xy, vis, col in zip(xy_world, visible, colors):
            if not vis:
                continue
            p = _apply_h(self.H, [xy])[0]
            r = self.cfg.marble_radius * self.scale_at(xy)
            if not (-r < p[0] < w + r and -r < p[1] < h + r):
                continue
            col = np.asarray(col, float)

            def circ(c, rad, color):
                cv2.circle(img, tuple(np.round(c * 16).astype(int)), int(round(rad * 16)),
                           tuple(float(v) for v in color), -1, cv2.LINE_AA, 4)

            circ(p, r, col * 0.7)
            circ(p - 0.1 * r, 0.78 * r, col)
            circ(p - 0.35 * r, 0.22 * r, np.minimum(255, col * 0.3 + 200))
            x0, y0 = (max(0, int(v - r - 2)) for v in p)
            x1, y1 = min(w, int(p[0] + r + 3)), min(h, int(p[1] + r + 3))
            patches.append((slice(y0, y1), slice(x0, x1)))
        k = self._n % len(self.noise)
        self._n += 1
        out = self._empty[k].copy()
        for sl in patches:
            cv2.copyTo(self.occ_img[sl], self.occ_mask[sl], img[sl])
            out[sl] = self._finish(img, self.noise[k], sl)
        if illumination != 1.0:
            out = cv2.convertScaleAbs(out, alpha=illumination)
        return out

    def calibration(self, marbles: list[MarbleSpec], sim: RaceSimulator) -> CameraCalibration:
        """What the on-site calibration tool would produce for this camera."""
        line = self.track.section(*self.s_range)
        img_line = Polyline(_apply_h(self.H, line.points), line.s)
        mid = line.points[len(line.points) // 2]
        scale = self.scale_at(mid)
        mask = np.zeros(self.size[::-1], np.uint8)
        th = int(round(self.cfg.track_width * 1.4 * scale))
        cv2.polylines(mask, [np.round(img_line.points).astype(np.int32)], False, 255, th)
        empty = np.zeros(len(marbles), bool)
        bg = np.median(np.stack([self.render([], empty, []) for _ in range(5)]), axis=0).astype(np.uint8)
        calib = CameraCalibration(
            cam_id=self.cam_id,
            centerline=img_line,
            track_mask=mask,
            marble_radius_px=self.cfg.marble_radius * scale,
            max_lateral_px=0.6 * self.cfg.track_width * scale,
            background=bg,
        )
        # Colour calibration: each marble is rolled through the view alone and the
        # pixels of its blob are collected at several positions.
        fg_thresh = 30
        s_lo, s_hi = self.s_range
        samples = np.linspace(s_lo + 0.05, s_hi - 0.05, 8)
        for k, spec in enumerate(marbles):
            pix = []
            for i, s in enumerate(samples):
                xy, tan = self.track.point_at(np.array([s]))
                n = np.array([-tan[0, 1], tan[0, 0]])
                pos = xy[0] + (i % 3 - 1) * 0.008 * n
                frame = self.render([pos], [True], [spec.bgr])
                diff = foreground_mask(frame, bg, fg_thresh)
                p = _apply_h(self.H, [pos])[0]
                yy, xx = np.nonzero(diff)
                near = (xx - p[0]) ** 2 + (yy - p[1]) ** 2 <= calib.marble_radius_px**2
                if near.sum() >= 5:
                    pix.append(frame[yy[near], xx[near]])
            calib.palette.append(ColorModel.fit(bgr_to_lab(np.concatenate(pix))))
        return calib


def _view_homography(world_box, size, rng, jitter):
    (x0, y0), (x1, y1) = world_box
    w, h = size
    src = np.float32([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])
    m = 0.04 * np.array([w, h])
    dst = np.float32([[m[0], m[1]], [w - m[0], m[1]], [w - m[0], h - m[1]], [m[0], h - m[1]]])
    dst += rng.uniform(-jitter, jitter, dst.shape).astype(np.float32) * np.float32([w, h])
    return cv2.getPerspectiveTransform(src, dst)


def single_camera_rig(track, cfg, seed=0, size=(1920, 1200)):
    """One top-down master camera covering the whole table (2.3 MP, e.g. Sony IMX392)."""
    rng = np.random.default_rng(seed + 100)
    pad = 0.1
    H = _view_homography(((-pad, -pad), (cfg.width + pad, cfg.height + pad)), size, rng, 0.01)
    return [SimCamera("master", H, size, track.s_range, track, cfg, rng,
                      gains=(1.0, 0.97, 1.03), vignette=0.25)]


def multi_camera_rig(track, cfg, n=14, seed=0, size=(640, 480), overlap=0.12, gap_after=9, gap=0.2,
                     fps=30.0):
    """``n`` fixed cameras, each owning one track section, with overlaps for hand-off.

    A deliberate coverage gap is left between camera ``gap_after`` and the next
    one to exercise coasting through an unobserved stretch.
    """
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
        pts = track.section(lo, hi).points
        c = (pts.min(0) + pts.max(0)) / 2
        ext = pts.max(0) - pts.min(0) + 0.12
        aspect = size[0] / size[1]
        ext = np.array([max(ext[0], ext[1] * aspect), max(ext[1], ext[0] / aspect)])
        H = _view_homography((c - ext / 2, c + ext / 2), size, rng, 0.03)
        cams.append(SimCamera(f"cam{k + 1:02d}", H, size, (lo, hi), track, cfg, rng,
                              gains=rng.uniform(0.85, 1.15, 3), vignette=0.1,
                              phase=rng.uniform(0, 1 / fps)))
    return cams


def finish_camera(track, cfg, s_finish, seed=0, fps=240.0, size=(320, 240), half_span=0.15):
    """High-frame-rate camera (ROI mode) dedicated to the finish line."""
    rng = np.random.default_rng(seed + 300)
    lo, hi = s_finish - half_span, min(track.s_range[1], s_finish + half_span)
    pts = track.section(lo, hi).points
    c = (pts.min(0) + pts.max(0)) / 2
    ext = pts.max(0) - pts.min(0) + 0.06
    aspect = size[0] / size[1]
    ext = np.array([max(ext[0], ext[1] * aspect), max(ext[1], ext[0] / aspect)])
    H = _view_homography((c - ext / 2, c + ext / 2), size, rng, 0.02)
    return SimCamera("finish", H, size, (lo, hi), track, cfg, rng, gains=rng.uniform(0.9, 1.1, 3),
                     noise=5.0, phase=rng.uniform(0, 1 / fps), fps=fps)


def blind_zones(cams, cfg, track):
    """Track stretches no camera can see: coverage gaps plus known tunnels."""
    L = track.s_range[1]
    zones = list(cfg.tunnels)
    covered = sorted(c.s_range for c in cams)
    end = 0.0
    for lo, hi in covered:
        if lo > end:
            zones.append((end, lo))
        end = max(end, hi)
    if end < L:
        zones.append((end, L))
    return sorted(zones)
