"""Per-camera colour-based marble detection (runs on the edge node)."""
from __future__ import annotations

from dataclasses import dataclass, field

import copy
from dataclasses import replace

import cv2
import numpy as np

from .color import ColorModel, bgr_to_lab, d2_matrix
from .track import Polyline, Projection


@dataclass
class CameraCalibration:
    """Everything a camera node needs, produced once by the calibration tool."""

    cam_id: str
    centerline: Polyline  # image-space centerline, vertices carry global s (metres)
    track_mask: np.ndarray  # uint8 mask of the track surface this camera owns
    marble_radius_px: float
    max_lateral_px: float  # reject blobs farther than this from the centerline (px, or m with normals)
    background: np.ndarray | None = None  # BGR median of empty-track frames
    palette: list[ColorModel] = field(default_factory=list)  # one per marble, global order
    # Optional for perspective cameras: expected full-marble blob area (px) along the section.
    area_s: np.ndarray | None = None
    area_px: np.ndarray | None = None
    # Optional: how far from the centerline (px) a marble may be at each distance; the
    # channel is wider in places, and one camera may see near and far stretches.
    lateral_s: np.ndarray | None = None
    lateral_px: np.ndarray | None = None

    def expected_area(self, s: float) -> float:
        if self.area_s is None:
            return float(np.pi * self.marble_radius_px**2)
        return float(np.interp(s, self.area_s, self.area_px))

    def lateral_limit(self, s: float) -> float:
        if self.lateral_s is None or self.lateral_px is None:
            return self.max_lateral_px
        return float(np.interp(s, self.lateral_s, self.lateral_px))

    @property
    def s_range(self) -> tuple[float, float]:
        return self.centerline.s_range

    @property
    def pieces(self) -> list[tuple[float, float]]:
        """Separate stretches of track this camera sees (one, unless it sees e.g. both legs of a hairpin)."""
        return self.centerline.pieces


@dataclass
class Detection:
    cam_id: str
    t: float
    xy: np.ndarray  # blob centroid in pixels
    area: int
    color_d2: np.ndarray  # (K,) Mahalanobis^2 of the blob's median colour to every marble
    candidates: list[Projection]  # possible track positions (usually exactly one)
    sigma_px: float = 1.0  # centroid uncertainty; grows when the blob is partly hidden
    median_lab: np.ndarray | None = None  # the blob's median colour (diagnostics, colour checks)

    def sigma_s(self, cand: Projection) -> float:
        return self.sigma_px * cand.s_per_unit


def shrink(frame: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Downscale ``frame`` to ``size`` (w, h), averaging pixels like INTER_AREA.

    OpenCV's INTER_AREA is fast only for whole-number factors; at a factor such as
    0.385 (1080p processed at ~740x416) it costs more than the whole detector. So
    halve exactly while the frame is at least twice the target, then finish with
    a bilinear step of less than 2x, which averages nearly as well.
    """
    w, h = size
    while frame.shape[1] >= 2 * w and frame.shape[0] >= 2 * h and frame.shape[1] % 2 == 0 and frame.shape[0] % 2 == 0:
        frame = cv2.resize(frame, (frame.shape[1] // 2, frame.shape[0] // 2), interpolation=cv2.INTER_AREA)
    if (frame.shape[1], frame.shape[0]) == (w, h):
        return frame
    return cv2.resize(frame, (w, h), interpolation=cv2.INTER_LINEAR)


def foreground_mask(frame, background, threshold, roi=None, kernel=None) -> np.ndarray:
    """0/1 mask of pixels whose largest per-channel difference to the background exceeds threshold."""
    b, g, r = cv2.split(cv2.absdiff(frame, background))
    diff = cv2.max(cv2.max(b, g), r)
    fg = cv2.threshold(diff, threshold, 1, cv2.THRESH_BINARY)[1]
    if roi is not None:
        fg = cv2.bitwise_and(fg, roi // 255)
    return fg if kernel is None else cv2.morphologyEx(fg, cv2.MORPH_OPEN, kernel)


def suppress_shadows(frame, background, fg, lo=0.42, hi=0.95, max_spread=0.1, min_bg=60) -> np.ndarray:
    """Drop foreground pixels that are the background darkened (a cast shadow).

    A shadow keeps the colour of the surface it falls on and darkens all three
    channels by the same factor; a marble changes the colour or, for the black
    marble, darkens much further (its dark side is below ``lo`` of the track's
    brightness). Over dark surfaces (``min_bg``) a shadow is too faint to pass the
    foreground threshold anyway, and a dark marble there must not be dropped.
    """
    ys, xs = np.nonzero(fg)
    if len(ys) == 0:
        return fg
    f = frame[ys, xs].astype(np.float32) + 1.0
    b = background[ys, xs].astype(np.float32) + 1.0
    r = f / b
    mean = r.mean(axis=1)
    spread = np.abs(r - mean[:, None]).max(axis=1) / mean
    shadow = (mean >= lo) & (mean <= hi) & (spread <= max_spread) & (b.min(axis=1) >= min_bg)
    if not shadow.any():
        return fg
    out = fg.copy()
    out[ys[shadow], xs[shadow]] = 0
    return out


def scale_calibration(calib: CameraCalibration, k: float) -> CameraCalibration:
    """The same calibration expressed for frames resized by factor ``k``."""
    w, h = calib.track_mask.shape[::-1]
    size = (max(1, round(w * k)), max(1, round(h * k)))
    cl = calib.centerline
    line = Polyline(cl.points * k, cl.s, None if cl.normals is None else cl.normals * k, cl.gaps)
    return replace(
        calib,
        centerline=line,
        track_mask=cv2.resize(calib.track_mask, size, interpolation=cv2.INTER_NEAREST),
        marble_radius_px=calib.marble_radius_px * k,
        max_lateral_px=calib.max_lateral_px if cl.normals is not None else calib.max_lateral_px * k,
        background=cv2.resize(calib.background, size, interpolation=cv2.INTER_AREA),
        area_px=None if calib.area_px is None else calib.area_px * k * k,
        lateral_px=None if calib.lateral_px is None or cl.normals is not None else calib.lateral_px * k,
    )


class ColorDetector:
    """Background subtraction + per-pixel colour classification + blob grouping.

    1. Foreground = pixels inside the track mask that differ from the empty-track
       background (fixed cameras, locked exposure/white balance), minus cast
       shadows (the background darkened, its colour kept).
    2. Each foreground pixel is classified against the camera's marble colour
       models; pixels that match nothing (highlights, shadows, noise) are dropped.
    3. Pixels are grouped by (connected component, colour class). This splits
       touching marbles of different colours without any shape analysis, because
       every marble in a race has a unique colour.
    4. Each group's centroid is projected onto the image centerline -> progress s.
    """

    def __init__(
        self,
        calib: CameraCalibration,
        fg_threshold: int = 30,
        pixel_gate: float = 20.0,
        min_area_frac: float = 0.25,
        fg_scale: float = 1.0,
        adapt: bool = True,
        bg_alpha: float = 0.05,
        bg_every: int = 10,
        color_alpha: float = 0.03,
        work_scale: float = 1.0,
        shadows: bool = True,
    ):
        if calib.background is None or not calib.palette:
            raise ValueError("camera needs a background and a colour palette")
        self.work_scale = work_scale
        self.shadows = shadows
        # fragment merging (see _merge_fragments), relative to the expected marble radius / area
        self.merge_dist, self.merge_union, self.merge_shade = 1.5, 1.4, 20.0
        self.full_size = calib.track_mask.shape[::-1]
        if work_scale != 1.0:
            calib = scale_calibration(calib, work_scale)
        self.calib = calib
        self.fg_threshold = fg_threshold
        self.pixel_gate = pixel_gate
        self.min_area_frac = min_area_frac
        self.min_area = max(4, min_area_frac * np.pi * calib.marble_radius_px**2)  # prefilter (smallest marble)
        self.fg_scale = fg_scale
        self.adapt = adapt
        self.bg_alpha, self.bg_every, self.color_alpha = bg_alpha, bg_every, color_alpha
        # Adaptive copies: lighting drift is absorbed here, the calibration stays pristine.
        self.background = calib.background.copy()
        self._bg_f = calib.background.astype(np.float32)
        self.palette = copy.deepcopy(calib.palette)
        # Foreground is searched in a slightly widened region so that a marble which
        # straddles the edge of this camera's section is seen whole; such blobs are
        # then rejected by the completeness test in detect().
        grow = int(2 * np.sqrt(max(calib.expected_area(s) for s in np.linspace(*calib.s_range, 9)) / np.pi)) + 3
        self._roi = cv2.dilate(calib.track_mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow + 1,) * 2))
        self.min_inside = 0.85
        if fg_scale != 1.0:
            h, w = calib.track_mask.shape
            self._small = (int(w * fg_scale), int(h * fg_scale))
            self._roi_small = cv2.resize(self._roi, self._small, interpolation=cv2.INTER_NEAREST)
            self._bg_small = cv2.resize(self.background, self._small, interpolation=cv2.INTER_AREA)
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        self._frames = 0
        # Pixels used to measure global illumination: a fixed sample of the track area.
        ys, xs = np.nonzero(calib.track_mask)
        pick = np.random.default_rng(0).choice(len(ys), size=min(1500, len(ys)), replace=False)
        self._probe = (ys[pick], xs[pick])
        self.gain = np.ones(3, np.float32)

    def _inside_coverage(self, c: Projection) -> bool:
        # A blob within one radius of the end of this camera's section is cut off
        # by the mask and its centroid is biased; the neighbouring camera (overlap)
        # sees it whole.
        r = np.sqrt(self.calib.expected_area(c.s) / np.pi) * c.s_per_unit
        return any(lo + r <= c.s <= hi - r for lo, hi in self.calib.pieces)

    def _measure_gain(self, frame: np.ndarray) -> np.ndarray:
        """Per-channel illumination ratio frame/background (median over track pixels).

        Marbles cover a small share of the probe pixels, so the median ignores
        them. Without this, a global dimming makes static walls and track edges
        differ from the stored background and appear as foreground clutter.
        """
        f = frame[self._probe].astype(np.float32) + 1.0
        b = self.background[self._probe].astype(np.float32) + 1.0
        g = np.median(f / b, axis=0)
        return np.clip(g, 0.3, 3.0).astype(np.float32)

    def _scaled(self, bg: np.ndarray) -> np.ndarray:
        if np.all(np.abs(self.gain - 1.0) < 0.02):
            return bg
        return cv2.convertScaleAbs(bg.astype(np.float32) * self.gain)

    def foreground(self, frame: np.ndarray) -> np.ndarray:
        if self.fg_scale == 1.0:
            bg = self._scaled(self.background)
            fg = foreground_mask(frame, bg, self.fg_threshold, self._roi)
            if self.shadows:
                fg = suppress_shadows(frame, bg, fg)
            return cv2.morphologyEx(fg, cv2.MORPH_OPEN, self._kernel)
        # Large sensors: find marbles on a downscaled frame, classify at full resolution.
        small = cv2.resize(frame, self._small, interpolation=cv2.INTER_AREA)
        bg = self._scaled(self._bg_small)
        fg = foreground_mask(small, bg, self.fg_threshold, self._roi_small)
        if self.shadows:
            fg = suppress_shadows(small, bg, fg)
        return cv2.resize(fg, frame.shape[1::-1], interpolation=cv2.INTER_NEAREST)

    def _adapt_background(self, frame: np.ndarray, fg: np.ndarray) -> None:
        still = cv2.dilate(fg, self._kernel, iterations=3) == 0
        cv2.accumulateWeighted(frame.astype(np.float32), self._bg_f, self.bg_alpha, still.astype(np.uint8))
        self.background = self._bg_f.astype(np.uint8)
        if self.fg_scale != 1.0:
            self._bg_small = cv2.resize(self.background, self._small, interpolation=cv2.INTER_AREA)

    def detect(self, frame: np.ndarray, t: float) -> list[Detection]:
        if self.work_scale != 1.0:
            # Processing resolution: detect on a downscaled frame (the stress suite ran on
            # 640x480 cameras, i.e. below half of a 1.6 MP sensor); report full-frame pixels.
            frame = shrink(frame, self.calib.track_mask.shape[::-1])
            dets = self._detect(frame, t)
            for d in dets:
                d.xy = d.xy / self.work_scale
            return dets
        return self._detect(frame, t)

    def _detect(self, frame: np.ndarray, t: float) -> list[Detection]:
        self.gain = self._measure_gain(frame)
        fg = self.foreground(frame)
        self._frames += 1
        if self.adapt and self._frames % self.bg_every == 0:
            self._adapt_background(frame, fg)
        n_comp, comp = cv2.connectedComponents(fg, connectivity=8)
        if n_comp <= 1:
            return []
        xs, ys = cv2.findNonZero(fg).reshape(-1, 2).T
        # Colour constancy: undo the global illumination change before matching colours.
        lab = bgr_to_lab(np.clip(frame[ys, xs] / self.gain, 0, 255))
        d2 = d2_matrix(self.palette, lab)
        cls = d2.argmin(axis=1)
        ok = d2[np.arange(len(cls)), cls] < self.pixel_gate
        ys, xs, lab, cls = ys[ok], xs[ok], lab[ok], cls[ok]
        if len(cls) == 0:
            return []

        n_cls = len(self.palette)
        key = comp[ys, xs].astype(np.int64) * n_cls + cls
        keys, inverse, counts = np.unique(key, return_inverse=True, return_counts=True)
        dets = []
        inside = np.bincount(inverse, weights=(self.calib.track_mask[ys, xs] > 0).astype(float), minlength=len(keys))
        groups = []  # [colour class, selection, centroid]
        for g in np.flatnonzero(counts >= self.min_area):
            if inside[g] < self.min_inside * counts[g]:
                continue  # cut by the section edge or belongs to neighbouring track
            sel = self._contiguous(xs, ys, inverse == g)
            if sel.sum() < self.min_area:
                continue
            groups.append([int(keys[g] % n_cls), sel, np.array([xs[sel].mean(), ys[sel].mean()])])
        groups = self._merge_fragments(groups, xs, ys, lab)
        for _, sel, xy in groups:
            n_px = int(sel.sum())
            cands = [c for c in self.calib.centerline.project(xy, self.calib.max_lateral_px)
                     if self._inside_coverage(c) and c.dist <= self.calib.lateral_limit(c.s)]
            if not cands:
                continue
            full = 0.85 * self.calib.expected_area(cands[0].s)  # highlight pixels go unclassified
            if n_px < self.min_area_frac * full / 0.85:
                continue
            median_lab = np.median(lab[sel], axis=0)
            color_d2 = d2_matrix(self.palette, median_lab)[0]
            if self.adapt:
                self._adapt_color(color_d2, median_lab)
            dets.append(
                Detection(
                    cam_id=self.calib.cam_id,
                    t=t,
                    xy=xy,
                    area=n_px,
                    color_d2=color_d2,
                    candidates=cands,
                    # A partly hidden marble's visible centroid shifts by up to ~r/2.
                    sigma_px=1.0 + 0.5 * np.sqrt(full / 0.85 / np.pi) * max(0.0, 1.0 - n_px / full),
                    median_lab=median_lab,
                )
            )
        return dets

    def _merge_fragments(self, groups, xs, ys, lab):
        """One marble split into two colour classes is still one marble.

        Shading, a number disc, a stripe, motion blur or (at low resolution) chroma
        blur can make part of a marble match a neighbouring colour better. Such a
        fragment lies within about a radius of the bigger piece, the two together
        are about one marble, and the fragment's colour is still a plausible shade of
        the bigger piece's marble. A neighbour in a pack (its visible crescent can
        be as close and as small) has a colour of its own and stays separate.
        """
        if len(groups) < 2:
            return groups
        groups = sorted(groups, key=lambda g: -int(g[1].sum()))
        kept = []
        for cls, sel, xy in groups:
            for k in kept:
                cands = self.calib.centerline.project(k[2], self.calib.max_lateral_px)
                area = self.calib.expected_area(cands[0].s) if cands else np.pi * self.calib.marble_radius_px**2
                main, frag = int(k[1].sum()), int(sel.sum())
                if np.linalg.norm(xy - k[2]) >= self.merge_dist * np.sqrt(area / np.pi):
                    continue
                if main + frag >= self.merge_union * area:
                    continue
                shade = float(d2_matrix([self.palette[k[0]]], np.median(lab[sel], axis=0))[0, 0])
                if shade <= self.merge_shade:
                    k[1] = k[1] | sel
                    break
            else:
                kept.append([cls, sel, xy])
        for k in kept:  # centroid of the whole marble
            k[2] = np.array([xs[k[1]].mean(), ys[k[1]].mean()])
        return kept

    @staticmethod
    def _contiguous(xs, ys, sel):
        """Keep the largest spatially connected piece of one colour inside a blob.

        When several marbles merge into one foreground blob, the small white
        specular highlights of all of them share the white marble's colour; they
        are scattered specks, while a real marble is one contiguous patch.
        """
        gx, gy = xs[sel], ys[sel]
        x0, y0 = gx.min(), gy.min()
        m = np.zeros((gy.max() - y0 + 1, gx.max() - x0 + 1), np.uint8)
        m[gy - y0, gx - x0] = 1
        n, lbl, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
        if n <= 2:
            return sel
        big = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        idx = np.flatnonzero(sel)[lbl[gy - y0, gx - x0] == big]
        out = np.zeros_like(sel)
        out[idx] = True
        return out

    def _adapt_color(self, color_d2: np.ndarray, median_lab: np.ndarray) -> None:
        # Follow slow illumination drift, but only from unambiguous blobs so a
        # model can never be dragged towards a neighbouring colour.
        best, second = np.partition(color_d2, 1)[:2]
        if best < 9.0 and second > 50.0:
            m = self.palette[int(np.argmin(color_d2))]
            m.mean = (1 - self.color_alpha) * m.mean + self.color_alpha * median_lab
