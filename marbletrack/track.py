"""Track geometry: a 1-D progress coordinate ``s`` shared by every camera.

Every observation in the system is expressed as *progress along the track
centerline* (metres from the start gate). Rankings, hand-offs between cameras,
finish detection and gap timing all become 1-D problems in ``s``.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Projection:
    """Result of projecting an image or world point onto a centerline polyline."""

    s: float  # progress along the track (metres)
    dist: float  # lateral distance from the centerline (polyline units)
    s_per_unit: float  # local d(s)/d(polyline unit); converts pixel noise to metres


class Polyline:
    """Polyline whose vertices carry a track-progress value ``s``.

    Used for the world centerline (vertices in metres) and for each camera's
    image-space centerline (vertices in pixels). Because ``s`` is stored per
    vertex instead of being derived from vertex spacing, perspective
    foreshortening in a camera image does not distort progress as long as the
    vertices are dense (a few centimetres apart).

    ``normals`` (optional, image polylines) gives, per vertex, the image vector
    of one metre of *cross-track* displacement. Under an oblique view that
    direction is not perpendicular to the track's image, so the nearest point in
    pixels slides along the track for a marble in the inside or outside lane.
    With normals, :meth:`project` decomposes a point as
    ``vertex + t * segment + u * normal`` (an oblique projection that uses the
    camera's own skew), giving progress from ``t`` and the lateral offset ``u``
    in metres.
    """

    def __init__(self, points, s, normals=None, gaps=None):
        self.points = np.asarray(points, dtype=float)
        self.s = np.asarray(s, dtype=float)
        self.normals = None if normals is None else np.asarray(normals, dtype=float)
        # ``gaps[k]`` is True where segment k only joins two separate stretches of track seen by
        # one camera (e.g. both legs of a hairpin): nothing on it is ever projected.
        self.gaps = np.zeros(max(0, len(self.s) - 1), bool) if gaps is None else np.asarray(gaps, bool)
        if len(self.points) < 2 or len(self.points) != len(self.s):
            raise ValueError("need >= 2 vertices with one s value each")
        if np.any(np.diff(self.s) <= 0):
            raise ValueError("s must be strictly increasing along the polyline")
        self._a = self.points[:-1]
        self._ab = self.points[1:] - self.points[:-1]
        self._len2 = np.maximum((self._ab**2).sum(axis=1), 1e-12)
        self._seglen = np.sqrt(self._len2)

    @classmethod
    def from_points(cls, points, s0: float = 0.0) -> "Polyline":
        """Build a polyline whose ``s`` is the cumulative arc length of ``points``."""
        points = np.asarray(points, dtype=float)
        keep = np.r_[True, np.linalg.norm(np.diff(points, axis=0), axis=1) > 1e-9]
        points = points[keep]
        s = s0 + np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
        return cls(points, s)

    @property
    def s_range(self) -> tuple[float, float]:
        return float(self.s[0]), float(self.s[-1])

    def resample(self, step: float) -> "Polyline":
        s = np.arange(self.s[0], self.s[-1], step)
        s = np.r_[s, self.s[-1]] if self.s[-1] - s[-1] > 1e-9 else s
        return Polyline(self.point_at(s)[0], s)

    def point_at(self, s):
        """Interpolated position(s) and unit tangent(s) at progress ``s``."""
        s = np.asarray(s, dtype=float)
        idx = np.clip(np.searchsorted(self.s, s, side="right") - 1, 0, len(self.s) - 2)
        t = (s - self.s[idx]) / (self.s[idx + 1] - self.s[idx])
        xy = self._a[idx] + t[..., None] * self._ab[idx]
        tangent = self._ab[idx] / self._seglen[idx][..., None]
        return xy, tangent

    def _project_oblique(self, p, max_dist):
        n = 0.5 * (self.normals[:-1] + self.normals[1:])
        d = p - self._a
        det = self._ab[:, 0] * n[:, 1] - self._ab[:, 1] * n[:, 0]
        ok = np.abs(det) > 1e-9
        det = np.where(ok, det, 1.0)
        t = (d[:, 0] * n[:, 1] - d[:, 1] * n[:, 0]) / det
        u = (self._ab[:, 0] * d[:, 1] - self._ab[:, 1] * d[:, 0]) / det
        inside = ok & (t >= -0.02) & (t <= 1.02) & (np.abs(u) <= max_dist) & ~self.gaps
        idx = np.flatnonzero(inside)
        out = []
        for run in np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1) if idx.size else []:
            k = run[np.argmin(np.abs(u[run]))]
            tk = float(np.clip(t[k], 0.0, 1.0))
            ds = self.s[k + 1] - self.s[k]
            out.append(Projection(s=float(self.s[k] + tk * ds), dist=float(abs(u[k])),
                                  s_per_unit=float(ds / self._seglen[k])))
        return sorted(out, key=lambda c: c.dist)

    @property
    def pieces(self) -> list[tuple[float, float]]:
        """The separate stretches of track this polyline covers, as (s_lo, s_hi)."""
        cut = np.flatnonzero(self.gaps)
        starts = np.r_[0, cut + 1]
        ends = np.r_[cut, len(self.s) - 1]
        return [(float(self.s[a]), float(self.s[b])) for a, b in zip(starts, ends) if b > a]

    def section(self, s_lo: float, s_hi: float) -> "Polyline":
        """Sub-polyline covering ``[s_lo, s_hi]`` (endpoints interpolated)."""
        inner = (self.s > s_lo) & (self.s < s_hi)
        s = np.r_[s_lo, self.s[inner], s_hi]
        return Polyline(self.point_at(s)[0], s)

    def project(self, p, max_dist: float) -> list[Projection]:
        """Project point ``p`` onto the polyline.

        Without normals ``dist``/``max_dist`` are in polyline units (pixels for
        an image polyline); with normals they are metres of lateral offset.

        Returns one candidate per *branch* of the polyline that passes within
        ``max_dist`` of ``p``, sorted by distance. More than one candidate occurs
        where the track crosses or runs alongside itself in the image (bridges,
        spirals, stacked levels); the fusion layer resolves the ambiguity with
        each marble's predicted progress.
        """
        p = np.asarray(p, dtype=float)
        if self.normals is not None:
            return self._project_oblique(p, max_dist)
        t = np.clip(((p - self._a) * self._ab).sum(axis=1) / self._len2, 0.0, 1.0)
        closest = self._a + t[:, None] * self._ab
        d = np.linalg.norm(p - closest, axis=1)
        d[self.gaps] = np.inf
        idx = np.flatnonzero(d <= max_dist)
        if idx.size == 0:
            return []
        out = []
        # Consecutive segment indices belong to the same pass of the track.
        for run in np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1):
            k = run[np.argmin(d[run])]
            ds = self.s[k + 1] - self.s[k]
            out.append(
                Projection(
                    s=float(self.s[k] + t[k] * ds),
                    dist=float(d[k]),
                    s_per_unit=float(ds / self._seglen[k]),
                )
            )
        return sorted(out, key=lambda c: c.dist)
