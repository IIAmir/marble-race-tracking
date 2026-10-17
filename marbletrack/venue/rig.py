"""Where the venue's cameras go on a new route: 12 AIDA track cameras and the OBSBOT overhead (13 in all).

Placement is what an installer would do with a plan of the route: one camera on
the start, one close on the finish line, and the other eleven chosen one by one
to see as much racing track as possible that no camera sees yet, at a useful
resolution (a ball at least ~24 px across), from a spot above the terrain, with
mountains not blocking the view. A camera may see two passes of the track (a
hairpin, a bridge): that counts. The tunnel and the enclosed lift cannot be
seen by anyone, and the return path counts for little (balls are spread out
there and rarely change places).

The OBSBOT hangs over the middle of the layout, looking straight down.
"""
from __future__ import annotations

import numpy as np

from .camera import AIDA, OBSBOT, CameraSpec, look_at
from .course import BALL_R, Course
from .raster import View, rasterize
from .scene import Scene

AZIMUTHS = (90.0, -90.0, 135.0, -135.0, 180.0, 0.0, 60.0, -60.0)
ELEVATIONS = (40.0, 55.0, 70.0)


def _occluded(scene: Scene, C, X, steps=24):
    """Does the terrain block the straight line from ``C`` to each point ``X``? (heightfield ray march)"""
    f = np.linspace(0.04, 0.96, steps)[:, None, None]
    pts = C[None, None, :] * (1 - f) + X[None, :, :] * f
    h = scene.height(pts[..., 0], pts[..., 1])
    return np.any(h > pts[..., 2] + 0.004, axis=0)


def _coverage(scene, course, C, R, optics, samples, margin=0.05, max_mm_px=1.05):
    """Which track samples (ball centres) a candidate camera sees, in frame, sharp enough and unblocked."""
    W, H = optics.size
    f = (W / 2) / np.tan(np.radians(optics.hfov_deg) / 2)
    Xc = (samples - C) @ R.T
    z = Xc[:, 2]
    ok = z > 0.1
    uv = np.zeros((len(samples), 2))
    uv[ok] = Xc[ok, :2] / z[ok, None] * f + [W / 2, H / 2]
    ok &= (uv[:, 0] > margin * W) & (uv[:, 0] < (1 - margin) * W) & (uv[:, 1] > margin * H) & (uv[:, 1] < (1 - margin) * H)
    ok &= z * 1000 / f <= max_mm_px  # mm per pixel at the ball
    if ok.any():
        idx = np.flatnonzero(ok)
        ok[idx] &= ~_occluded(scene, C, samples[idx])
    return ok


def _unblocked(scene: Scene, C, R, optics, samples, vis, size=(480, 270)):
    """Of the samples ``vis`` marks visible, those not hidden by trees, buildings or the track itself.

    The heightfield test above only knows the terrain; this low-resolution depth render
    of the whole scene is what the installer checks by looking through the camera.
    """
    W, H = size
    f = (W / 2) / np.tan(np.radians(optics.hfov_deg) / 2)
    view = View(R, C, f, W / 2, H / 2, W, H)
    depth, _ = rasterize(view, scene.tris)
    idx = np.flatnonzero(vis)
    uv, z = view.project(samples[idx])
    x = np.clip(np.round(uv[:, 0]).astype(int), 0, W - 1)
    y = np.clip(np.round(uv[:, 1]).astype(int), 0, H - 1)
    # nearest surface around the pixel (a ball centre sits just above the channel floor)
    near = np.max([depth[np.clip(y + dy, 0, H - 1), np.clip(x + dx, 0, W - 1)]
                   for dx in (-1, 0, 1) for dy in (-1, 0, 1)], axis=0)
    out = vis.copy()
    out[idx] = z <= near + 0.01
    return out


def place_cameras(course: Course, scene: Scene, seed: int = 0, n_track: int = 12, overhead: bool = True):
    rng = np.random.default_rng(seed + 404)
    step = 0.02
    s_samp = np.arange(0, course.length, step)
    samples = course.ball_centre(s_samp, np.zeros(len(s_samp)))
    tags = np.array(course.tags)[np.clip(np.searchsorted(course.s, s_samp), 0, len(course.tags) - 1)]
    weight = np.ones(len(s_samp))
    weight[course.in_any(s_samp, course.blind)] = 0.0
    weight[tags == "return"] = 0.15
    weight[(s_samp > course.finish_s - 1.0) & (s_samp < course.divert_s)] = 2.0  # the run to the line matters most
    covered = np.zeros(len(s_samp), bool)
    specs = []

    def candidate(s0, az, el, dist):
        P, T, L, U, Wd = course.frame(np.array([s0]))
        target = P[0] + BALL_R * U[0]
        heading = np.arctan2(T[0, 1], T[0, 0]) + np.radians(az)
        e = np.radians(el)
        C = target + dist * np.array([np.cos(heading) * np.cos(e), np.sin(heading) * np.cos(e), np.sin(e)])
        if C[2] < float(scene.height(C[0], C[1])) + 0.06:
            return None
        return C, look_at(C, target)

    def best_for(s_centres, dists, must=None):
        found = []
        for s0 in s_centres:
            for az in AZIMUTHS:
                for el in ELEVATIONS:
                    for dist in dists:
                        cand = candidate(s0, az + rng.uniform(-8, 8), el + rng.uniform(-4, 4), dist)
                        if cand is None:
                            continue
                        C, R = cand
                        vis = _coverage(scene, course, C, R, AIDA, samples)
                        if must is not None and not vis[must].all():
                            continue
                        gain = float((weight * (vis & ~covered)).sum())
                        found.append((gain, C, R, vis))
        # Look through the best few candidates, as an installer would: a tree or the
        # helix tower in the way disqualifies a spot the terrain test let through.
        best = None
        for gain, C, R, vis in sorted(found, key=lambda c: -c[0])[:5]:
            seen = _unblocked(scene, C, R, AIDA, samples, vis)
            if must is not None and not seen[must].all():
                continue
            g = float((weight * (seen & ~covered)).sum())
            if best is None or g > best[0]:
                best = (g, C, R, seen)
            if g >= 0.8 * gain:
                break
        return best

    def add(name, best, role="section"):
        gain, C, R, vis = best
        covered[:] |= vis
        idx = np.flatnonzero(vis)
        s_hint = (float(s_samp[idx].min()), float(s_samp[idx].max())) if len(idx) else None
        specs.append(CameraSpec(name, AIDA, C, R, wb=tuple(rng.uniform(0.94, 1.06, 3)), phase=float(rng.uniform(0, 1 / 60)),
                                role=role, s_hint=s_hint, principal=tuple(rng.normal(0, 4, 2))))

    # 1. the finish line, close (fine timing), 2. the start, 3.. the rest greedily
    # The finish camera must see the last 40 cm before the line and 10 cm after it
    # (the timing fit uses frames on both sides), with the line well inside the frame.
    fin = int(np.argmin(np.abs(s_samp - course.finish_s)))
    finish = best_for([course.finish_s - 0.2, course.finish_s - 0.1, course.finish_s], (0.4, 0.5, 0.6),
                      must=np.arange(fin - 20, fin + 6))
    if finish is None:
        finish = best_for([course.finish_s - 0.1, course.finish_s], (0.5, 0.7, 0.9), must=np.arange(fin - 8, fin + 4))
    add("cam-finish", finish, role="finish")
    add("cam-start", best_for([0.4, 0.7, 1.0], (0.6, 0.8)))
    centres = s_samp[::15]
    for k in range(n_track - 2):
        open_ = [s for s in centres if not covered[min(len(covered) - 1, int(s / step))]
                 and weight[min(len(weight) - 1, int(s / step))] > 0.5]
        pick = list(rng.choice(open_, size=min(10, len(open_)), replace=False)) if open_ else list(centres[:5])
        best = best_for(pick, (0.55, 0.8, 1.1))
        if best is None:
            break
        add(f"cam{k + 1:02d}", best)
    if overhead:
        x0, y0, x1, y1 = scene.bounds
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        ext = np.array([x1 - x0, y1 - y0]) - 0.6
        long_axis = 0 if ext[0] >= ext[1] else 1
        W, H = OBSBOT.size
        hf = np.tan(np.radians(OBSBOT.hfov_deg) / 2)
        vf = hf * H / W
        top = float(course.P[:, 2].max()) + 0.1
        height = top + max(ext[long_axis] / 2 / hf, ext[1 - long_axis] / 2 / vf) * 1.05
        C = np.array([cx, cy, height])
        yaw = 0.0 if long_axis == 0 else np.pi / 2
        R = np.vstack([[np.cos(yaw), np.sin(yaw), 0.0], [np.sin(yaw), -np.cos(yaw), 0.0], [0.0, 0.0, -1.0]])
        specs.append(CameraSpec("obsbot", OBSBOT, C, R, wb=(1.0, 0.99, 1.02), phase=float(rng.uniform(0, 1 / 60)),
                                role="overhead", s_hint=(0.0, course.length)))
    racing = weight > 0.5
    return specs, {"covered_racing": float((covered & racing).sum() / max(1, racing.sum())),
                   "covered_all": float(covered.mean())}
