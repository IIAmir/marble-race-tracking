"""Per-camera colour-based marble detection (runs on the edge node)."""
from __future__ import annotations

from dataclasses import dataclass, field

import copy

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
    max_lateral_px: float  # reject blobs farther than this from the centerline
    background: np.ndarray | None = None  # BGR median of empty-track frames
    palette: list[ColorModel] = field(default_factory=list)  # one per marble, global order

    @property
    def s_range(self) -> tuple[float, float]:
        return self.centerline.s_range


@dataclass
class Detection:
    cam_id: str
    t: float
    xy: np.ndarray  # blob centroid in pixels
    area: int
    color_d2: np.ndarray  # (K,) Mahalanobis^2 of the blob's median colour to every marble
    candidates: list[Projection]  # possible track positions (usually exactly one)
    sigma_px: float = 1.0  # centroid uncertainty; grows when the blob is partly hidden

    def sigma_s(self, cand: Projection) -> float:
        return self.sigma_px * cand.s_per_unit


def foreground_mask(frame, background, threshold, roi=None, kernel=None) -> np.ndarray:
    """0/1 mask of pixels whose largest per-channel difference to the background exceeds threshold."""
    b, g, r = cv2.split(cv2.absdiff(frame, background))
    diff = cv2.max(cv2.max(b, g), r)
    fg = cv2.threshold(diff, threshold, 1, cv2.THRESH_BINARY)[1]
    if roi is not None:
        fg = cv2.bitwise_and(fg, roi // 255)
    return fg if kernel is None else cv2.morphologyEx(fg, cv2.MORPH_OPEN, kernel)


class ColorDetector:
    """Background subtraction + per-pixel colour classification + blob grouping.

    1. Foreground = pixels inside the track mask that differ from the empty-track
       background (fixed cameras, locked exposure/white balance).
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
    ):
        if calib.background is None or not calib.palette:
            raise ValueError("camera needs a background and a colour palette")
        self.calib = calib
        self.fg_threshold = fg_threshold
        self.pixel_gate = pixel_gate
        self.full_area = 0.85 * np.pi * calib.marble_radius_px**2  # highlight pixels go unclassified
        self.min_area = max(4, min_area_frac * np.pi * calib.marble_radius_px**2)
        self.fg_scale = fg_scale
        self.adapt = adapt
        self.bg_alpha, self.bg_every, self.color_alpha = bg_alpha, bg_every, color_alpha
        # Adaptive copies: lighting drift is absorbed here, the calibration stays pristine.
        self.background = calib.background.copy()
        self._bg_f = calib.background.astype(np.float32)
        self.palette = copy.deepcopy(calib.palette)
        self._roi = calib.track_mask
        if fg_scale != 1.0:
            h, w = calib.track_mask.shape
            self._small = (int(w * fg_scale), int(h * fg_scale))
            self._roi_small = cv2.resize(calib.track_mask, self._small, interpolation=cv2.INTER_NEAREST)
            self._bg_small = cv2.resize(self.background, self._small, interpolation=cv2.INTER_AREA)
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        self._frames = 0

    def _inside_coverage(self, c: Projection) -> bool:
        # A blob within one radius of the end of this camera's section is cut off
        # by the mask and its centroid is biased; the neighbouring camera (overlap)
        # sees it whole.
        lo, hi = self.calib.s_range
        r = self.calib.marble_radius_px * c.s_per_unit
        return lo + r <= c.s <= hi - r

    def foreground(self, frame: np.ndarray) -> np.ndarray:
        if self.fg_scale == 1.0:
            return foreground_mask(frame, self.background, self.fg_threshold, self._roi, self._kernel)
        # Large sensors: find marbles on a downscaled frame, classify at full resolution.
        small = cv2.resize(frame, self._small, interpolation=cv2.INTER_AREA)
        fg = foreground_mask(small, self._bg_small, self.fg_threshold, self._roi_small)
        return cv2.resize(fg, frame.shape[1::-1], interpolation=cv2.INTER_NEAREST)

    def _adapt_background(self, frame: np.ndarray, fg: np.ndarray) -> None:
        still = cv2.dilate(fg, self._kernel, iterations=3) == 0
        cv2.accumulateWeighted(frame.astype(np.float32), self._bg_f, self.bg_alpha, still.astype(np.uint8))
        self.background = self._bg_f.astype(np.uint8)
        if self.fg_scale != 1.0:
            self._bg_small = cv2.resize(self.background, self._small, interpolation=cv2.INTER_AREA)

    def detect(self, frame: np.ndarray, t: float) -> list[Detection]:
        fg = self.foreground(frame)
        self._frames += 1
        if self.adapt and self._frames % self.bg_every == 0:
            self._adapt_background(frame, fg)
        n_comp, comp = cv2.connectedComponents(fg, connectivity=8)
        if n_comp <= 1:
            return []
        xs, ys = cv2.findNonZero(fg).reshape(-1, 2).T
        lab = bgr_to_lab(frame[ys, xs])
        d2 = d2_matrix(self.palette, lab)
        cls = d2.argmin(axis=1)
        ok = d2[np.arange(len(cls)), cls] < self.pixel_gate
        ys, xs, lab, cls = ys[ok], xs[ok], lab[ok], cls[ok]
        if len(cls) == 0:
            return []

        n_cls = len(self.palette)
        key = comp[ys, xs].astype(np.int64) * n_cls + cls
        groups, inverse, counts = np.unique(key, return_inverse=True, return_counts=True)
        dets = []
        for g in np.flatnonzero(counts >= self.min_area):
            sel = inverse == g
            xy = np.array([xs[sel].mean(), ys[sel].mean()])
            cands = [c for c in self.calib.centerline.project(xy, self.calib.max_lateral_px)
                     if self._inside_coverage(c)]
            if not cands:
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
                    area=int(counts[g]),
                    color_d2=color_d2,
                    candidates=cands,
                    # A partly hidden marble's visible centroid shifts by up to ~r/2.
                    sigma_px=1.0 + 0.5 * self.calib.marble_radius_px * max(0.0, 1.0 - counts[g] / self.full_area),
                )
            )
        return dets

    def _adapt_color(self, color_d2: np.ndarray, median_lab: np.ndarray) -> None:
        # Follow slow illumination drift, but only from unambiguous blobs so a
        # model can never be dragged towards a neighbouring colour.
        best, second = np.partition(color_d2, 1)[:2]
        if best < 9.0 and second > 50.0:
            m = self.palette[int(np.argmin(color_d2))]
            m.mean = (1 - self.color_alpha) * m.mean + self.color_alpha * median_lab
