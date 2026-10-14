"""Central fusion: one track-coordinate Kalman filter per marble, fed by every camera."""
from __future__ import annotations

import heapq
import itertools
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import linear_sum_assignment

from .detector import Detection

_INF = 1e9


@dataclass
class MarbleState:
    marble_id: str
    index: int
    x: np.ndarray = field(default_factory=lambda: np.zeros(2))  # [s (m), v (m/s)]
    P: np.ndarray = field(default_factory=lambda: np.eye(2))
    t: float | None = None  # time the state refers to; None until first seen
    last_obs_t: float | None = None
    last_obs_s: float | None = None
    last_obs_v: float = 0.0
    last_obs_r_px: float = 0.0  # variance (m^2) of the last observation from pixel noise alone
    last_obs_cam: str | None = None
    finish_time: float | None = None
    finish_sigma: float = 0.0  # 1-sigma uncertainty of finish_time (s)
    n_obs: int = 0

    @property
    def initialized(self) -> bool:
        return self.t is not None


@dataclass
class LeaderboardEntry:
    rank: int
    marble_id: str
    progress_m: float
    speed_mps: float
    status: str  # finished | tracking | coasting | lost | unseen
    finish_time: float | None
    gap_m: float  # distance behind the leader (0 for finished marbles)
    photo_finish: bool = False  # finish within timing resolution of a neighbour: needs review


@dataclass
class RaceEvent:
    t: float
    kind: str  # finish | camera_offline | camera_online
    subject: str
    detail: str = ""


def _merge(intervals):
    out = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


class FusionEngine:
    """Associates detections from any camera to marbles and filters progress.

    Cameras are stateless producers of ``Detection``s in track coordinates, so a
    hand-off between cameras is not a special case: a marble simply starts
    receiving measurements from a different camera. Overlap zones yield two
    measurements of the same marble, which the filter fuses.

    Occlusion handling, from short to long:
      * marble-on-marble overlap: the detector splits blobs by colour, and a
        fully hidden marble coasts for ``free_coast_time``;
      * tunnels and other permanent occluders (``static_blind_zones``) and gaps
        between camera views: the marble coasts through the zone, may not move
        past its exit, and may not overtake anyone on the leaderboard;
      * a failed camera (no frames for ``camera_timeout``): its section is
        turned into a blind zone until frames resume.
    Re-acquisition after any of these is by colour; the motion gate widens with
    the filter covariance while a marble is unseen.
    """

    def __init__(
        self,
        marble_ids: list[str],
        s_finish: float,
        track_length: float,
        cameras: dict[str, tuple[float, float]] | None = None,
        static_blind_zones: list[tuple[float, float]] = (),
        accel_sigma: float = 4.0,
        meas_sigma_floor: float = 0.01,
        motion_gate: float = 25.0,
        color_gate: float = 50.0,
        color_margin: float = 20.0,
        free_coast_time: float = 0.15,
        coast_tolerance: float = 0.05,
        tracking_timeout: float = 0.2,
        lost_after: float = 1.5,
        camera_timeout: float = 0.3,
        rank_margin: float = 0.012,
        photo_finish_s: float = 0.002,
    ):
        self.marbles = [MarbleState(m, i) for i, m in enumerate(marble_ids)]
        self.s_finish = s_finish
        self.track_length = track_length
        self.cameras = dict(cameras or {})
        self.static_blind_zones = list(static_blind_zones)
        self.q = accel_sigma**2
        self.r_floor = meas_sigma_floor**2
        self.motion_gate = motion_gate
        self.color_gate = color_gate
        self.color_margin = color_margin
        self.free_coast_time = free_coast_time
        self.coast_tolerance = coast_tolerance
        self.tracking_timeout = tracking_timeout
        self.lost_after = lost_after
        self.camera_timeout = camera_timeout
        self.rank_margin = rank_margin
        self.photo_finish_s = photo_finish_s
        self._order = list(range(len(marble_ids)))
        self._last_frame: dict[str, float] = {}
        self._online: dict[str, bool] = {c: True for c in self.cameras}
        self.events: list[RaceEvent] = []
        self.now = 0.0
        self.blind_zones = self._compute_blind_zones()

    # -- camera health -----------------------------------------------------
    def _compute_blind_zones(self):
        if not self.cameras:
            return _merge(self.static_blind_zones)
        covered = _merge(r for c, r in self.cameras.items() if self._online.get(c, True))
        zones, end = list(self.static_blind_zones), 0.0
        for a, b in covered:
            if a > end:
                zones.append((end, a))
            end = max(end, b)
        if end < self.track_length:
            zones.append((end, self.track_length))
        return _merge(zones)

    def _check_health(self) -> None:
        changed = False
        for cam in self.cameras:
            last = self._last_frame.get(cam)
            online = last is not None and self.now - last <= self.camera_timeout
            if last is None and self.now <= self.camera_timeout:
                online = True  # start-up grace
            if online != self._online.get(cam, True):
                self._online[cam] = online
                self.events.append(RaceEvent(self.now, "camera_online" if online else "camera_offline", cam))
                changed = True
        if changed:
            self.blind_zones = self._compute_blind_zones()

    def camera_status(self) -> dict[str, bool]:
        return dict(self._online)

    # -- filtering ---------------------------------------------------------
    def _predict(self, m: MarbleState, t: float) -> None:
        dt = t - m.t
        if dt <= 0:  # late / simultaneous measurement: update at current state
            return
        F = np.array([[1.0, dt], [0.0, 1.0]])
        Q = self.q * np.array([[dt**3 / 3, dt**2 / 2], [dt**2 / 2, dt]])
        m.x = F @ m.x
        m.P = F @ m.P @ F.T + Q
        m.t = t
        cap = self._coast_cap(m)
        if m.x[0] > cap:
            m.x[0] = cap

    def _coast_cap(self, m: MarbleState) -> float:
        """Furthest an unobserved marble may be predicted.

        Briefly (``free_coast_time``) a marble may vanish anywhere, e.g. behind
        another marble. Beyond that it can only be somewhere no camera sees.
        """
        s_last = m.last_obs_s
        cap = s_last + self.coast_tolerance + max(0.0, m.last_obs_v) * self.free_coast_time
        for a, b in self.blind_zones:
            if a <= cap and b > s_last:
                cap = max(cap, b + self.coast_tolerance)
        return cap

    def _innovation(self, m: MarbleState, t: float, s: float, r: float):
        """Measurement model, valid for late (out-of-sequence) measurements too.

        A measurement taken ``lag`` seconds before the current state time is
        compared with the state retrodicted to that instant, ``s - lag * v``,
        with the process noise over the lag added to its variance.
        """
        lag = max(0.0, m.t - t)
        H = np.array([1.0, -lag])
        S = H @ m.P @ H + r + self.q * lag**3 / 3
        return H, s - H @ m.x, S

    def _update(self, m: MarbleState, t: float, s: float, r: float, r_px: float = 0.0,
                cam_id: str | None = None) -> None:
        if not m.initialized:
            m.x = np.array([s, 0.0])
            m.P = np.diag([r, 1.0])
            m.t = t
        else:
            H, y, S = self._innovation(m, t, s, r)
            K = m.P @ H / S
            m.x = m.x + K * y
            m.P = (np.eye(2) - np.outer(K, H)) @ m.P
        if m.last_obs_t is not None and t <= m.last_obs_t:
            m.n_obs += 1
            return  # late measurement: improves the state, but is not the newest observation
        if m.finish_time is None and m.last_obs_s is not None and m.last_obs_s < self.s_finish <= s:
            # Sub-frame finish time from the two raw observations bracketing the line.
            f = (self.s_finish - m.last_obs_s) / (s - m.last_obs_s)
            m.finish_time = m.last_obs_t + f * (t - m.last_obs_t)
            speed = (s - m.last_obs_s) / max(t - m.last_obs_t, 1e-6)
            m.finish_sigma = float(np.sqrt((1 - f) ** 2 * m.last_obs_r_px + f**2 * r_px) / speed)
            self.events.append(RaceEvent(t, "finish", m.marble_id, f"{m.finish_time:.4f}"))
        m.last_obs_t, m.last_obs_s, m.last_obs_v, m.last_obs_r_px = t, s, float(m.x[1]), r_px
        m.last_obs_cam = cam_id
        m.n_obs += 1

    def _r(self, det: Detection, cand) -> float:
        # Pixel noise alone understates the error: overlapping cameras disagree
        # by ~1 cm (calibration, parallax of the marble centre above the track,
        # partial occlusion), and treating that as motion corrupts velocity.
        return det.sigma_s(cand) ** 2 + self.r_floor

    # -- association -------------------------------------------------------
    def process(self, t: float, detections: list[Detection], cam_id: str | None = None) -> None:
        """Ingest one camera frame's detections, all captured at time ``t``.

        Call it for every frame, including frames with no detections: the call
        doubles as the camera's heartbeat.
        """
        self.now = max(self.now, t)
        if cam_id is not None:
            self._last_frame[cam_id] = max(t, self._last_frame.get(cam_id, t))
        self._check_health()
        for m in self.marbles:
            if m.initialized:
                self._predict(m, t)
        if not detections:
            return
        cost = np.full((len(detections), len(self.marbles)), _INF)
        choice: dict[tuple[int, int], tuple[float, float, float]] = {}
        # Finished marbles roll on through the run-out and must keep absorbing
        # their own detections; once out of sight they leave association.
        active = [m.finish_time is None or self.now - m.last_obs_t <= self.tracking_timeout
                  for m in self.marbles]
        for i, det in enumerate(detections):
            best_color = det.color_d2.min()
            for j, m in enumerate(self.marbles):
                if not active[j]:
                    continue
                c_color = det.color_d2[j]
                # Ratio test: never hand a detection to a marble whose colour fits
                # much worse than the best match, however well the motion fits.
                if c_color > self.color_gate or c_color > best_color + self.color_margin:
                    continue
                if not m.initialized:
                    cand = det.candidates[0]
                    cost[i, j] = c_color
                    choice[i, j] = (cand.s, self._r(det, cand), det.sigma_s(cand) ** 2)
                    continue
                best = None
                for cand in det.candidates:  # >1 only where the track overlaps itself
                    r = self._r(det, cand)
                    _, y, S = self._innovation(m, t, cand.s, r)
                    md = y**2 / S
                    if best is None or md < best[0]:
                        best = (md, cand.s, r, det.sigma_s(cand) ** 2)
                if best[0] <= self.motion_gate:
                    cost[i, j] = c_color + best[0]
                    choice[i, j] = best[1:]
        rows, cols = linear_sum_assignment(cost)
        for i, j in zip(rows, cols):
            if cost[i, j] < _INF:
                self._update(self.marbles[j], t, *choice[i, j], cam_id=cam_id)

    def tick(self, now: float) -> None:
        """Advance the clock without measurements (detects silent cameras)."""
        self.now = max(self.now, now)
        self._check_health()

    # -- ranking -----------------------------------------------------------
    def status(self, m: MarbleState, now: float) -> str:
        if m.finish_time is not None:
            return "finished"
        if not m.initialized:
            return "unseen"
        age = now - m.last_obs_t
        if age <= self.tracking_timeout:
            return "tracking"
        return "coasting" if age <= self.lost_after else "lost"

    def leaderboard(self, now: float | None = None) -> list[LeaderboardEntry]:
        now = self.now if now is None else now
        finished = sorted((m for m in self.marbles if m.finish_time is not None), key=lambda m: m.finish_time)
        racing = [i for i in self._order if self.marbles[i].finish_time is None]

        def key(i: int) -> float:
            m = self.marbles[i]
            return m.x[0] if m.initialized else -np.inf

        def observed(i: int) -> bool:
            return self.status(self.marbles[i], now) == "tracking"

        # Adjacent swaps only when the trailing marble leads by > rank_margin
        # (no flicker when two marbles run side by side) and is currently seen:
        # a coasting marble's position is a prediction, which is not evidence of
        # an overtake. Each swap removes one strict inversion, so this terminates.
        swapped = True
        while swapped:
            swapped = False
            for k in range(len(racing) - 1):
                a, b = racing[k], racing[k + 1]
                if key(b) > key(a) + self.rank_margin and (observed(b) or not self.marbles[a].initialized):
                    racing[k], racing[k + 1] = b, a
                    swapped = True
        self._order = [m.index for m in finished] + racing

        leader_s = max((key(i) for i in racing), default=0.0)
        close = set()
        for a, b in zip(finished, finished[1:]):
            # Too close to call: within the fixed timing resolution, or within
            # 2 sigma of the two finish estimates (e.g. one marble partly hidden).
            limit = max(self.photo_finish_s, 2 * np.hypot(a.finish_sigma, b.finish_sigma))
            if b.finish_time - a.finish_time < limit:
                close |= {a.index, b.index}
        out = []
        for rank, i in enumerate(self._order, start=1):
            m = self.marbles[i]
            st = self.status(m, now)
            out.append(
                LeaderboardEntry(
                    rank=rank,
                    marble_id=m.marble_id,
                    progress_m=float(m.x[0]) if m.initialized else 0.0,
                    speed_mps=float(m.x[1]) if m.initialized else 0.0,
                    status=st,
                    finish_time=m.finish_time,
                    gap_m=0.0 if st == "finished" or not m.initialized else float(leader_s - m.x[0]),
                    photo_finish=i in close,
                )
            )
        return out


class ReorderBuffer:
    """Holds per-camera frame batches until ``latency`` has passed, then releases
    them in capture-time order, so the fusion filter sees a time-ordered stream
    even though camera nodes deliver over a network with jitter."""

    def __init__(self, latency: float = 0.05):
        self.latency = latency
        self._heap: list = []
        self._seq = itertools.count()
        self.released_until = -np.inf
        self.late = 0

    def push(self, t: float, cam_id: str, detections: list[Detection]) -> None:
        if t < self.released_until:
            self.late += 1  # still processed; the filter treats it as simultaneous
        heapq.heappush(self._heap, (t, next(self._seq), cam_id, detections))

    def pop_ready(self, now: float):
        while self._heap and self._heap[0][0] <= now - self.latency:
            t, _, cam_id, dets = heapq.heappop(self._heap)
            self.released_until = max(self.released_until, t)
            yield t, cam_id, dets

    def flush(self):
        yield from self.pop_ready(np.inf)
