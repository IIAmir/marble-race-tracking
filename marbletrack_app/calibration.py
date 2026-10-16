"""Turn what the admin clicked into the engine's per-camera calibration.

* Waypoints: image points of distance marks along the track (race direction),
  clicked at marble-centre height. They become the camera's image centerline,
  each vertex carrying its track distance, which is what makes perspective
  harmless: progress is interpolated between nearby marks, never derived from
  pixel distances across the frame.
* Track band: the image region the detector searches. Its local width follows
  the perspective scale implied by the marks (pixels per metre along the track).
* Background: the median of a few frames of the empty track.
* Colours: one CIELAB Gaussian per marble per camera, fitted to the pixels of
  the blob under the admin's click.
"""
from __future__ import annotations

import cv2
import numpy as np

from marbletrack.color import ColorModel, bgr_to_lab
from marbletrack.detector import CameraCalibration
from marbletrack.track import Polyline

from .config import CameraSetup, ColorSample, Project

SEPARATION_MIN = 50.0  # same threshold the stress suite uses: d² below this can be confused


class CalibrationError(ValueError):
    """A problem the admin can fix; the message is shown in the UI as is."""


# ---------------------------------------------------------------------------- geometry
def _catmull_rom(p: np.ndarray, sub: int) -> tuple[np.ndarray, np.ndarray]:
    """Centripetal Catmull-Rom through the points: (dense points, parameter in waypoint units)."""
    if len(p) < 3:
        u = np.linspace(0, len(p) - 1, (len(p) - 1) * sub + 1)
        return p[0] + (p[-1] - p[0]) * (u / max(len(p) - 1, 1))[:, None], u
    ext = np.vstack([2 * p[0] - p[1], p, 2 * p[-1] - p[-2]])
    pts, us = [], []
    for i in range(len(p) - 1):
        P0, P1, P2, P3 = ext[i:i + 4]
        t0 = 0.0
        t1 = t0 + max(np.linalg.norm(P1 - P0), 1e-6) ** 0.5
        t2 = t1 + max(np.linalg.norm(P2 - P1), 1e-6) ** 0.5
        t3 = t2 + max(np.linalg.norm(P3 - P2), 1e-6) ** 0.5
        for k in range(sub + (i == len(p) - 2)):
            f = k / sub
            t = t1 + f * (t2 - t1)
            A1 = (t1 - t) / (t1 - t0) * P0 + (t - t0) / (t1 - t0) * P1
            A2 = (t2 - t) / (t2 - t1) * P1 + (t - t1) / (t2 - t1) * P2
            A3 = (t3 - t) / (t3 - t2) * P2 + (t - t2) / (t3 - t2) * P3
            B1 = (t2 - t) / (t2 - t0) * A1 + (t - t0) / (t2 - t0) * A2
            B2 = (t3 - t) / (t3 - t1) * A2 + (t - t1) / (t3 - t1) * A3
            pts.append((t2 - t) / (t2 - t1) * B1 + (t - t1) / (t2 - t1) * B2)
            us.append(i + f)
    return np.array(pts), np.array(us)


def centerline(cam: CameraSetup, sub: int = 8) -> Polyline:
    """Image centerline through the waypoints, one smooth curve per stretch.

    A smooth curve (centripetal Catmull-Rom) follows bends between marks, where
    straight chords would cut corners; each dense vertex gets its distance by
    interpolating the marks' distances. Separate stretches seen by one camera
    are joined by a gap segment that never matches anything.
    """
    if len(cam.markers) < 2:
        raise CalibrationError(f"{cam.name}: place at least two waypoints along the track")
    s = np.asarray(cam.marker_s, float)
    if np.any(np.diff(s) <= 0):
        raise CalibrationError(f"{cam.name}: waypoint distances must increase in race direction")
    marks = np.asarray(cam.markers, float)
    pts_all, s_all, gaps = [], [], []
    for a, b in cam.stretches:
        if b == a:
            raise CalibrationError(f"{cam.name}: every stretch needs at least two waypoints (the one at {s[a]:g} m is alone)")
        pts, u = _catmull_rom(marks[a:b + 1], sub)
        ss = _projective_s(pts, u, s[a:b + 1])
        if pts_all:
            gaps[-1:] = [True]
        pts_all.append(pts)
        s_all.append(ss)
        gaps += [False] * (len(pts) - 1) + [False]
    return Polyline(np.vstack(pts_all), np.concatenate(s_all), gaps=np.array(gaps[:-1], bool))


def _mobius(l3, s3):
    """s(l) = (a l + b) / (c l + 1) through three points: the exact 1-D map of a straight
    stretch seen in perspective. None if ill-conditioned."""
    A = np.array([[l, 1.0, -l * s] for l, s in zip(l3, s3)])
    try:
        a, b, c = np.linalg.solve(A, np.asarray(s3, float))
    except np.linalg.LinAlgError:
        return None
    return a, b, c


def _projective_s(pts: np.ndarray, u: np.ndarray, s_mark: np.ndarray) -> np.ndarray:
    """Track distance of every dense vertex.

    Linear interpolation between marks assumes equal pixels per metre, which is
    wrong when the camera looks along the track (far marks are compressed).
    Each gap is interpolated with projective maps fitted to its neighbouring
    marks, blended across the gap; a gap falls back to linear if a fit is not
    monotonic.
    """
    l = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))]
    k_idx = np.round(u).astype(int)
    on_mark = np.isclose(u, k_idx)
    l_mark = np.array([l[on_mark & (k_idx == k)][0] for k in range(len(s_mark))])
    out = np.interp(u, np.arange(len(s_mark)), s_mark)
    n = len(s_mark)
    if n < 3:
        return out
    for k in range(n - 1):
        sel = (u >= k) & (u <= k + 1)
        ll = l[sel]
        fits = []
        for w0 in (k - 1, k):
            if w0 < 0 or w0 + 2 >= n:
                continue
            m = _mobius(l_mark[w0:w0 + 3], s_mark[w0:w0 + 3])
            if m is None:
                continue
            a, b, c = m
            den = c * ll + 1
            if np.any(den <= 1e-6):
                continue
            v = (a * ll + b) / den
            if np.all(np.diff(v) > 0) and abs(v[0] - s_mark[k]) < 1e-6 + 0.02 * (s_mark[k + 1] - s_mark[k]):
                fits.append(v)
        if fits:
            f = (u[sel] - k)
            v = fits[0] if len(fits) == 1 else (1 - f) * fits[0] + f * fits[1]
            v[0], v[-1] = s_mark[k], s_mark[k + 1]
            out[sel] = v
    # strictly increasing for the Polyline (duplicates at the joints)
    keep = np.r_[True, np.diff(out) > 1e-9]
    if not keep.all():
        out = np.maximum.accumulate(out + np.arange(len(out)) * 1e-9)
    return out


def px_per_metre(cam: CameraSetup) -> np.ndarray:
    """Local image scale at each waypoint (pixels per metre along the track), within its stretch."""
    p = np.asarray(cam.markers, float)
    s = np.asarray(cam.marker_s, float)
    out = np.zeros(len(p))
    for a, b in cam.stretches:
        if b == a:
            out[a] = out[a - 1] if a > 0 else 1.0
            continue
        seg = np.linalg.norm(np.diff(p[a:b + 1], axis=0), axis=1) / np.maximum(np.diff(s[a:b + 1]), 1e-6)
        out[a:b + 1] = np.r_[seg, seg] if len(seg) == 1 else np.r_[seg[0], (seg[:-1] + seg[1:]) / 2, seg[-1]]
    return out


def band_halfwidth_px(cam: CameraSetup, project: Project) -> np.ndarray:
    """Half-width of the searched band at each waypoint: channel width plus a marble of margin."""
    w_m = project.track_width_m + project.marble_diameter_mm / 1000.0
    return 0.5 * w_m * px_per_metre(cam) * cam.width_scale


def track_mask(cam: CameraSetup, project: Project, size: tuple[int, int]) -> np.ndarray:
    w, h = size
    mask = np.zeros((h, w), np.uint8)
    marks = np.asarray(cam.markers, float)
    band = band_halfwidth_px(cam, project)
    for s0, s1 in cam.stretches:
        if s1 == s0:
            continue
        p, u = _catmull_rom(marks[s0:s1 + 1], 6)
        hw = np.interp(u, np.arange(s1 - s0 + 1), band[s0:s1 + 1])
        for i in range(len(p) - 1):
            a, b = p[i], p[i + 1]
            d = b - a
            n = np.array([-d[1], d[0]]) / max(np.linalg.norm(d), 1e-9)
            poly = np.array([a + n * hw[i], b + n * hw[i + 1], b - n * hw[i + 1], a - n * hw[i]])
            cv2.fillConvexPoly(mask, np.round(poly).astype(np.int32), 255, cv2.LINE_8)
        for (x, y), r in zip(p, hw):
            cv2.circle(mask, (int(round(x)), int(round(y))), int(round(r)), 255, -1)
    return mask


def marble_radius_px(cam: CameraSetup, project: Project) -> np.ndarray:
    """Expected marble radius (px) at each waypoint, perspective included."""
    r_m = project.marble_diameter_mm / 2000.0
    return r_m * px_per_metre(cam) * cam.radius_scale


# ---------------------------------------------------------------------------- background
def median_background(frames: list[np.ndarray]) -> np.ndarray:
    if not frames:
        raise CalibrationError("no frames captured")
    return np.median(np.stack(frames), axis=0).astype(np.uint8)


def background_check(frames: list[np.ndarray], mask: np.ndarray | None = None) -> dict:
    """Is the scene static? Moving content (a hand, a marble) shows as frame-to-frame change."""
    stack = np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]).astype(np.float32)
    spread = stack.max(0) - stack.min(0)
    if mask is not None:
        spread = spread[mask > 0]
    moving = float((spread > 40).mean()) if spread.size else 0.0
    return {"moving_fraction": moving, "ok": moving < 0.002}


# ---------------------------------------------------------------------------- click to sample a marble
def segment_click(frame, background, xy, r_expected: float, mask=None) -> dict:
    """The marble under a click: foreground pixels connected to the click point.

    Uses the empty-track background when available; otherwise a colour flood fill
    from the click. Returns the pixels (BGR), the blob's centre and radius.
    """
    h, w = frame.shape[:2]
    x, y = int(round(xy[0])), int(round(xy[1]))
    if not (0 <= x < w and 0 <= y < h):
        raise CalibrationError("Click inside the image.")
    R = int(max(6, 2.2 * r_expected))
    x0, y0, x1, y1 = max(0, x - R), max(0, y - R), min(w, x + R + 1), min(h, y + R + 1)
    patch = frame[y0:y1, x0:x1]
    if background is not None:
        diff = cv2.absdiff(patch, background[y0:y1, x0:x1]).max(axis=2)
        fg = (diff > 30).astype(np.uint8)
    else:
        fg = np.zeros(patch.shape[:2], np.uint8)
        ff = np.zeros((patch.shape[0] + 2, patch.shape[1] + 2), np.uint8)
        cv2.floodFill(patch.copy(), ff, (x - x0, y - y0), 0, (18,) * 3, (18,) * 3,
                      cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE | (1 << 8))
        fg = ff[1:-1, 1:-1]
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    n, labels, stats, cents = cv2.connectedComponentsWithStats(fg)
    if n <= 1:
        raise CalibrationError("Nothing found under the click. Is a marble there, and was the empty track captured without marbles?")
    cx, cy = x - x0, y - y0
    lab = labels[cy, cx]
    if lab == 0:  # clicked just beside the marble: take the nearest component
        d = [np.hypot(*(cents[i] - (cx, cy))) if stats[i, cv2.CC_STAT_AREA] >= 6 else np.inf for i in range(1, n)]
        lab = 1 + int(np.argmin(d))
        if d[lab - 1] > 1.5 * r_expected + 4:
            raise CalibrationError("No marble at that point. Click the middle of a marble.")
    blob = labels == lab
    area = int(blob.sum())
    exp_area = np.pi * r_expected**2
    # The expected size comes from the along-track scale of the waypoints, which foreshortening
    # can understate by ~2x in area for a camera looking along the track: generous bounds.
    if area < 0.2 * exp_area:
        raise CalibrationError("That is too small to be a marble (partly hidden, or a reflection?). Try another spot.")
    if area > 6.0 * exp_area:
        raise CalibrationError("That is much bigger than one marble: are two marbles touching? Separate them and click again.")
    # Use the core of the blob for colour: edges mix with the background.
    core = cv2.erode(blob.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    core = core.astype(bool) if core.sum() >= 15 else blob
    ys, xs = np.nonzero(core)
    pixels = patch[ys, xs]
    radius = float(np.sqrt(area / np.pi))
    centre = (float(xs.mean() + x0), float(ys.mean() + y0))
    if mask is not None and mask[int(centre[1]), int(centre[0])] == 0:
        raise CalibrationError("That marble is outside this camera's track band (step 3).")
    return {"pixels": pixels, "radius": radius, "centre": centre, "area": area}


def fit_sample(pixels: np.ndarray, clicks: int, medians: list | None = None) -> ColorSample:
    """Colour model from all sampled pixels, plus the median blob colour.

    ``medians`` are the per-click blob medians; their median is what the
    detector will compare against the models, so the palette check uses it.
    """
    lab = bgr_to_lab(pixels)
    # Robust start: a glass marble shows a near-white specular glint and a dark rim.
    # Seed the fit from the median and MAD so the glint cannot widen the model
    # (a widened model would overlap a white marble's colour).
    med0 = np.median(lab, axis=0)
    mad = 1.4826 * np.median(np.abs(lab - med0), axis=0) + 1.0
    core = lab[(((lab - med0) / mad) ** 2).sum(axis=1) < 11.34]
    model = ColorModel.fit(core if len(core) >= 10 else lab)
    med = np.median(np.asarray(medians), axis=0) if medians else np.median(lab, axis=0)
    return ColorSample(model.mean.tolist(), model.cov_inv.tolist(), [float(v) for v in med], int(len(pixels)), clicks)


def to_model(sample: ColorSample) -> ColorModel:
    return ColorModel(np.asarray(sample.mean, float), np.asarray(sample.cov_inv, float))


def palette_issues(cam: CameraSetup, project: Project) -> list[dict]:
    """Marble pairs this camera could confuse, judged the way the detector judges.

    The detector scores a blob's median colour against every marble's model and
    admits any marble with d² ≤ 50 as a candidate. A pair is a problem when
    marble A's typical blob would also pass as marble B.
    """
    ids = [m.id for m in project.marbles if m.id in cam.palette]
    out = {}
    for a in ids:
        sa = cam.palette[a]
        med = np.asarray(sa.median if sa.median is not None else sa.mean, float)
        for b in ids:
            if a == b:
                continue
            d2 = float(to_model(cam.palette[b]).d2(med)[0])
            if d2 < SEPARATION_MIN:
                key = tuple(sorted((a, b)))
                if key not in out or d2 < out[key]["d2"]:
                    out[key] = {"a": a, "b": b, "d2": round(d2, 1)}
    return sorted(out.values(), key=lambda x: x["d2"])


def copy_palette(src: CameraSetup, dst: CameraSetup, only_missing: bool = True) -> int:
    n = 0
    for mid, sample in src.palette.items():
        if only_missing and mid in dst.palette and not dst.palette[mid].copied_from:
            continue
        dst.palette[mid] = ColorSample(sample.mean, sample.cov_inv, sample.median, sample.pixels, 0, copied_from=src.key)
        n += 1
    return n


# ---------------------------------------------------------------------------- everything together
def camera_problems(cam: CameraSetup, project: Project, has_background: bool) -> list[str]:
    """What still blocks this camera, in the order the wizard fixes it."""
    out = []
    if len(cam.markers) < 2:
        out.append("waypoints")
    if not has_background:
        out.append("background")
    missing = [m.name for m in project.marbles if m.id not in cam.palette]
    if missing:
        out.append("colours")
    return out


def build_calibration(cam: CameraSetup, project: Project, background: np.ndarray) -> CameraCalibration:
    if background is None:
        raise CalibrationError(f"{cam.name}: capture the empty-track background first")
    h, w = background.shape[:2]
    line = centerline(cam)
    missing = [m.name for m in project.marbles if m.id not in cam.palette]
    if missing:
        raise CalibrationError(f"{cam.name}: no colour sample for {', '.join(missing)}")
    radius = marble_radius_px(cam, project)
    hw = band_halfwidth_px(cam, project)
    return CameraCalibration(
        cam_id=cam.key,
        centerline=line,
        track_mask=track_mask(cam, project, (w, h)),
        marble_radius_px=float(np.median(radius)),
        max_lateral_px=float(hw.max()),
        background=background,
        palette=[to_model(cam.palette[m.id]) for m in project.marbles],
        area_s=np.asarray(cam.marker_s, float),
        area_px=np.pi * radius**2,
    )
