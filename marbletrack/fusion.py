"""Central fusion: one track-coordinate Kalman filter per marble, fed by every camera."""
from __future__ import annotations

import heapq
import itertools
from collections import deque
from dataclasses import dataclass, field, replace

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
    last_color: tuple[float, float] = (0.0, 0.0)  # (own colour d2, best rival d2) of the last match
    finish_time: float | None = None
    finish_sigma: float = 0.0  # 1-sigma uncertainty of finish_time (s)
    finish_final: bool = False  # provisional until the refinement window has passed
    finish_pending_until: float = 0.0
    history: deque = field(default_factory=lambda: deque(maxlen=48))  # raw (t, s, var_px, var_bias)
    maneuver_until: float = -1.0  # boosted process noise after a surprising measurement
    placed: bool = False  # inserted into the leaderboard order
    n_obs: int = 0
    fresh: int = 0  # observations since first sighting or since re-acquisition after a long gap

    @property
    def initialized(self) -> bool:
        return self.t is not None


@dataclass
class LeaderboardEntry:
    rank: int
    marble_id: str
    progress_m: float
    speed_mps: float
    status: str  # finished | tracking | acquiring | coasting | lost | unseen
    finish_time: float | None
    gap_m: float  # distance behind the leader (0 for finished marbles)
    photo_finish: bool = False  # finish within timing resolution of a neighbour: needs review
    lap: int = 1  # lap the marble is on (1-based; the last lap once finished)


@dataclass
class RaceEvent:
    t: float
    kind: str  # finish | camera_offline | camera_online
    subject: str
    detail: str = ""


def _ranges(r):
    """A camera's coverage: one (lo, hi) or a list of them (a camera may see several stretches)."""
    if r is None:
        return []
    if len(r) and np.ndim(r[0]) == 0:
        return [tuple(r)]
    return [tuple(x) for x in r]


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

    Laps: with ``lap_length`` set, the track is a circuit and detections carry
    progress within one lap. Each is also tried one, two, ... laps further on and
    the motion gate picks the lap that fits the marble; positions, ranking, blind
    zones and the finish line (``s_finish``, the line on the last lap) all work in
    total distance, so a marble that laps another is simply far ahead of it.
    """

    # Switches for ablation studies (scripts compare the tracker with and without them).
    _nll = True  # association cost includes the log of the innovation variance
    _bound = True  # position variance bounded inside places no camera sees

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
        color_margin_tracked: float = 9.0,
        free_coast_time: float = 0.15,
        coast_tolerance: float = 0.05,
        tracking_timeout: float = 0.1,
        reacquire_gap: float = 0.3,
        lost_after: float = 1.5,
        camera_timeout: float = 0.3,
        rank_margin: float = 0.012,
        photo_finish_s: float = 0.002,
        photo_finish_k: float = 3.0,
        rank_k: float = 2.0,
        rank_confirm: float = 0.1,
        finish_window: float = 0.03,
        maneuver_nis: float = 9.0,
        maneuver_boost: float = 25.0,
        maneuver_time: float = 0.15,
        laps: int = 1,
        lap_length: float | None = None,
        max_back: float = 0.25,
    ):
        self.marbles = [MarbleState(m, i) for i, m in enumerate(marble_ids)]
        self.s_finish = s_finish
        self.track_length = track_length
        self.laps = max(1, int(laps))
        self.lap_length = float(lap_length) if lap_length else None
        self.cameras = dict(cameras or {})
        self.static_blind_zones = list(static_blind_zones)
        self.q = accel_sigma**2
        self.r_floor = meas_sigma_floor**2
        self.motion_gate = motion_gate
        self.color_gate = color_gate
        self.color_margin = color_margin
        self.color_margin_tracked = color_margin_tracked
        self.free_coast_time = free_coast_time
        self.coast_tolerance = coast_tolerance
        self.tracking_timeout = tracking_timeout
        self.lost_after = lost_after
        self.camera_timeout = camera_timeout
        self.rank_margin = rank_margin
        self.reacquire_gap = reacquire_gap
        self.photo_finish_s = photo_finish_s
        self.photo_finish_k = photo_finish_k
        self.rank_k = rank_k
        self.rank_confirm = rank_confirm
        self._lead_since: dict[tuple[int, int], float] = {}
        self.finish_window = finish_window
        self.maneuver_nis, self.maneuver_boost, self.maneuver_time = maneuver_nis, maneuver_boost, maneuver_time
        self._order = list(range(len(marble_ids)))
        self._last_frame: dict[str, float] = {}
        self._online: dict[str, bool] = {c: True for c in self.cameras}
        self.events: list[RaceEvent] = []
        self.now = 0.0
        self.max_back = max_back
        self._unknown_cost = float(np.log(1.0 / self.r_floor))  # as a marble known to within ~1 m
        self.blind_zones = self._compute_blind_zones()

    # -- camera health -----------------------------------------------------
    def _compute_blind_zones(self):
        L = self.lap_length or self.track_length
        if not self.cameras:
            zones = list(self.static_blind_zones)
        else:
            covered = _merge(r for c, rs in self.cameras.items() if self._online.get(c, True) for r in _ranges(rs))
            zones, end = list(self.static_blind_zones), 0.0
            for a, b in covered:
                if a > end:
                    zones.append((end, a))
                end = max(end, b)
            if end < L:
                zones.append((end, L))
        if self.lap_length:  # the same stretches on every lap
            zones = [(a + k * L, b + k * L) for k in range(self.laps) for a, b in zones]
        return _merge(zones)

    def _lap_candidates(self, det: Detection) -> Detection:
        """On a circuit, every position within the lap may be on any lap of the race."""
        L = self.lap_length
        cands = [replace(c, s=float(np.mod(c.s, L)) + k * L) for c in det.candidates for k in range(self.laps)]
        return replace(det, candidates=cands)

    def _cold_start_candidate(self, det: Detection):
        """A marble seen for the first time: the lap that puts it nearest the field (lap 1 at the start)."""
        if not self.lap_length:
            return det.candidates[0]
        known = [m.x[0] for m in self.marbles if m.initialized]
        ref = float(np.median(known)) if known else 0.0
        own = [c for c in det.candidates if c.s < self.lap_length] or det.candidates
        best = min(own, key=lambda c: c.dist)
        return min((c for c in det.candidates if abs(np.mod(c.s, self.lap_length) - np.mod(best.s, self.lap_length)) < 1e-9),
                   key=lambda c: abs(c.s - ref))

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
        # Bounces and back-slips are short, violent accelerations: while one is
        # suspected the filter trusts the model less and follows the measurements.
        q = self.q * (self.maneuver_boost if t <= m.maneuver_until else 1.0)
        Q = q * np.array([[dt**3 / 3, dt**2 / 2], [dt**2 / 2, dt]])
        m.x = F @ m.x
        m.P = F @ m.P @ F.T + Q
        m.t = t
        cap = self._coast_cap(m)
        if m.x[0] > cap:
            m.x[0] = cap
        floor = self._coast_floor(m)
        if m.x[0] < floor:  # cannot have rolled back out of view: stop there
            m.x[0] = floor
            m.x[1] = max(m.x[1], 0.0)
        # Inside a place no camera sees, the marble is somewhere between floor and cap (it
        # cannot have left without being seen): its position variance is bounded by that
        # interval. In view, a marble hidden behind others is not bounded this way, and
        # once lost (``lost_after``) the bound is lifted so the gate can widen again.
        bound = (0.5 * (cap - floor)) ** 2 + 0.01
        tol = self.coast_tolerance
        recent = m.last_obs_t is not None and t - m.last_obs_t <= self.lost_after
        if self._bound and recent and m.P[0, 0] > bound and any(a - tol <= m.x[0] <= b + tol for a, b in self.blind_zones):
            k = np.sqrt(bound / m.P[0, 0])
            m.P[0, :] *= k
            m.P[:, 0] *= k

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

    def _coast_floor(self, m: MarbleState) -> float:
        """Furthest back an unobserved marble may be predicted (mirror of ``_coast_cap``).

        A marble knocked backwards while hidden keeps a negative velocity in the
        filter; extrapolated without limit it would drift out of the blind zone it
        is in, where a camera would have seen it, and let others "pass" it.
        """
        s_last = m.last_obs_s
        floor = s_last - self.coast_tolerance + min(0.0, m.last_obs_v) * self.free_coast_time
        for a, b in sorted(self.blind_zones, reverse=True):
            if b >= floor and a < s_last:
                floor = min(floor, a - self.coast_tolerance)
        return floor

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
                r_bias: float = 0.0, cam_id: str | None = None) -> None:
        if not m.initialized:
            # Cold start: borrow the field's median speed so a marble first seen just
            # before a blind zone is predicted sensibly; its velocity variance stays wide.
            speeds = [o.x[1] for o in self.marbles if o.initialized and o.n_obs >= 3 and o.finish_time is None]
            m.x = np.array([s, float(np.median(speeds)) if speeds else 0.0])
            m.P = np.diag([r, 1.0])
            m.t = t
            m.fresh = 0
        elif m.last_obs_t is not None and t - m.last_obs_t > self.reacquire_gap:
            # Re-acquired after a long gap (dead camera, tunnel, dropped frames): filtering
            # through the jump would read it as a burst of speed. Restart from the average
            # speed across the gap, with wide velocity uncertainty, and require fresh
            # sightings before the marble may gain places again (status "acquiring").
            v_avg = (s - m.last_obs_s) / (t - m.last_obs_t)
            m.x = np.array([s, float(np.clip(v_avg, 0.0, 4.0))])
            m.P = np.diag([r, 0.25])
            m.t = t
            m.maneuver_until = -1.0
            m.fresh = 0
        else:
            H, y, S = self._innovation(m, t, s, r)
            if y * y / S > self.maneuver_nis:
                m.maneuver_until = t + self.maneuver_time
                # Re-open the velocity uncertainty so this very update can follow.
                m.P = m.P + np.diag([0.0, self.q * self.maneuver_boost * self.maneuver_time / 4])
                H, y, S = self._innovation(m, t, s, r)
            K = m.P @ H / S
            m.x = m.x + K * y
            m.P = (np.eye(2) - np.outer(K, H)) @ m.P
        m.history.append((t, s, max(r_px - r_bias, 1e-10), r_bias))
        if m.last_obs_t is not None and t <= m.last_obs_t:
            m.n_obs += 1
            return  # late measurement: improves the state, but is not the newest observation
        if m.finish_time is None and m.last_obs_s is not None and m.last_obs_s < self.s_finish <= s:
            # Sub-frame finish time from the two raw observations bracketing the line.
            f = (self.s_finish - m.last_obs_s) / (s - m.last_obs_s)
            m.finish_time = m.last_obs_t + f * (t - m.last_obs_t)
            speed = (s - m.last_obs_s) / max(t - m.last_obs_t, 1e-6)
            # Provisional (two-frame) time: claim no better than half the frame gap;
            # close finishers stay flagged until the least-squares fit confirms them.
            m.finish_sigma = max(float(np.sqrt((1 - f) ** 2 * m.last_obs_r_px + f**2 * r_px) / speed),
                                 0.5 * (t - m.last_obs_t))
            m.finish_pending_until = m.finish_time + self.finish_window
            self.events.append(RaceEvent(t, "finish", m.marble_id, f"{m.finish_time:.4f}"))
        m.last_obs_t, m.last_obs_s, m.last_obs_v, m.last_obs_r_px = t, s, float(m.x[1]), r_px
        m.last_obs_cam = cam_id
        m.n_obs += 1
        m.fresh += 1

    def _refine_finish(self, m: MarbleState) -> None:
        """Replace the two-frame interpolation by a weighted least-squares fit.

        All raw observations within ``finish_window`` of the provisional crossing
        are fitted with s(t) = b0 + b1*dt + b2*dt^2 (quadratic when >= 6 points),
        weighted by their pixel variances. The crossing is the root of
        s(t) = s_finish near dt = 0, and its variance is propagated from the fit
        covariance (scaled up by the reduced chi^2 when residuals exceed the
        stated noise), so finish_sigma is an honest 1-sigma.
        """
        m.finish_final = True
        t0 = m.finish_time
        pts = sorted(p for p in m.history if abs(p[0] - t0) <= self.finish_window)
        if len(pts) < 4 or pts[0][0] > t0 or pts[-1][0] < t0:
            return
        t, s, var, bias = (np.array(c) for c in zip(*pts))
        dt = t - t0
        deg = 2 if len(pts) >= 6 else 1
        X = np.vander(dt, deg + 1, increasing=True)
        W = 1.0 / var
        A = X.T @ (X * W[:, None])
        try:
            cov = np.linalg.inv(A)
        except np.linalg.LinAlgError:
            return
        beta = cov @ (X.T @ (W * s))
        dof = len(pts) - deg - 1
        if dof > 0:
            chi2 = float(W @ (s - X @ beta) ** 2) / dof
            cov = cov * max(1.0, chi2)
        tau = 0.0
        for _ in range(8):  # Newton on b0 + b1 tau + b2 tau^2 = s_finish
            g = np.polyval(beta[::-1], tau) - self.s_finish
            dg = beta[1] + (2 * beta[2] * tau if deg == 2 else 0.0)
            if dg <= 0:
                return
            tau -= g / dg
        if abs(tau) > self.finish_window:
            return
        dg = beta[1] + (2 * beta[2] * tau if deg == 2 else 0.0)
        J = -np.array([tau**k for k in range(deg + 1)]) / dg
        m.finish_time = t0 + float(tau)
        # Independent pixel noise averages down in the fit; an occlusion bias is the
        # same in consecutive frames and does not, so it is added back in full.
        m.finish_sigma = float(np.sqrt(J @ cov @ J + float(np.mean(bias)) / dg**2))
        self.events.append(RaceEvent(self.now, "finish_confirmed", m.marble_id,
                                     f"{m.finish_time:.5f} +/- {m.finish_sigma * 1e3:.2f} ms"))

    def _settle_finishes(self) -> None:
        for m in self.marbles:
            if m.finish_time is not None and not m.finish_final and self.now >= m.finish_pending_until:
                self._refine_finish(m)

    @staticmethod
    def _bias(det: Detection, cand) -> float:
        """Variance share caused by partial occlusion (correlated between frames)."""
        return (max(0.0, det.sigma_px - 1.0) * cand.s_per_unit) ** 2

    def _r(self, det: Detection, cand) -> float:
        # Pixel noise alone understates the error: overlapping cameras disagree
        # by ~1 cm (calibration, parallax of the marble centre above the track,
        # partial occlusion), and treating that as motion corrupts velocity.
        return det.sigma_s(cand) ** 2 + self.r_floor

    def _sanitize(self, d: Detection) -> Detection | None:
        """Corrupt input (NaN/inf, wrong palette size) must never reach the filter."""
        if len(d.color_d2) != len(self.marbles) or not np.all(np.isfinite(d.color_d2)):
            return None
        if not (np.isfinite(d.sigma_px) and d.sigma_px > 0):
            return None
        cands = [c for c in d.candidates if np.isfinite(c.s) and np.isfinite(c.s_per_unit) and c.s_per_unit > 0]
        if not cands:
            return None
        if len(cands) != len(d.candidates):
            d = replace(d, candidates=cands)
        return d

    # -- association -------------------------------------------------------
    def process(self, t: float, detections: list[Detection], cam_id: str | None = None) -> None:
        """Ingest one camera frame's detections, captured at about time ``t``.

        Call it for every frame, including frames with no detections: the call
        doubles as the camera's heartbeat. A detection may carry its own capture
        time (``Detection.t``, e.g. its image row's time under a rolling shutter);
        it is used when it lies within 50 ms of ``t``.
        """
        if not np.isfinite(t):
            return
        detections = [d for d in map(self._sanitize, detections) if d is not None]
        if self.lap_length:
            detections = [self._lap_candidates(d) for d in detections]
        self.now = max(self.now, t)
        if cam_id is not None:
            self._last_frame[cam_id] = max(t, self._last_frame.get(cam_id, t))
        self._check_health()
        td = [d.t if np.isfinite(d.t) and abs(d.t - t) <= 0.05 else t for d in detections]
        t_pred = max([t] + td)  # predict to the latest row; earlier rows are handled as slightly late
        for m in self.marbles:
            if m.initialized:
                self._predict(m, t_pred)
        if not detections:
            return
        cost = np.full((len(detections), len(self.marbles)), _INF)
        choice: dict[tuple[int, int], tuple[float, float, float]] = {}
        # Finished marbles roll on through the run-out and must keep absorbing
        # their own detections; once out of sight they leave association.
        active = [m.finish_time is None or self.now - m.last_obs_t <= self.tracking_timeout
                  for m in self.marbles]
        # Marbles do not jump backwards: a measurement far behind where a marble could have
        # rolled back to (bounces are 5-15 cm) is another stretch of track seen at the same
        # place in the image, however wide the motion gate has grown while it was unseen.
        floor = [self._coast_floor(m) - self.max_back - max(0.0, m.last_obs_v) * max(0.0, m.last_obs_t - t)
                 if m.initialized and m.last_obs_s is not None else -np.inf for m in self.marbles]
        known = [m.initialized and m.last_obs_t is not None and self.now - m.last_obs_t <= self.lost_after
                 for m in self.marbles]
        for i, det in enumerate(detections):
            best_color = det.color_d2.min()
            # A blob that clearly has the colour of a marble known to be elsewhere (seen within
            # ``lost_after``) is that marble seen where it is not (another pass of the track at
            # the same image place), not some other marble: only a close colour call may go to
            # anyone else. Wider margins are for re-identifying a marble lost for a while.
            jb = int(np.argmin(det.color_d2))
            margin = self.color_margin_tracked if known[jb] else self.color_margin
            for j, m in enumerate(self.marbles):
                if not active[j]:
                    continue
                c_color = det.color_d2[j]
                # Ratio test: never hand a detection to a marble whose colour fits
                # much worse than the best match, however well the motion fits.
                if c_color > self.color_gate or c_color > best_color + margin:
                    continue
                if not m.initialized:
                    cand = self._cold_start_candidate(det)
                    cost[i, j] = c_color + self._unknown_cost
                    choice[i, j] = (cand.s, self._r(det, cand), det.sigma_s(cand) ** 2, self._bias(det, cand))
                    continue
                best = None
                for cand in det.candidates:  # >1 only where the track overlaps itself
                    if cand.s < floor[j]:
                        continue
                    r = self._r(det, cand)
                    _, y, S = self._innovation(m, td[i], cand.s, r)
                    md = y**2 / S
                    if best is None or md < best[0]:
                        best = (md, cand.s, r, det.sigma_s(cand) ** 2, self._bias(det, cand), S)
                if best is not None and best[0] <= self.motion_gate:
                    # Negative log-likelihood, not just the Mahalanobis distance: a marble
                    # unseen for a while has a wide gate that fits almost anything cheaply;
                    # the log-variance term makes a confidently tracked marble win the blob.
                    cost[i, j] = c_color + best[0] + (np.log(best[5] / self.r_floor) if self._nll else 0.0)
                    choice[i, j] = best[1:5]
        rows, cols = linear_sum_assignment(cost)
        for i, j in zip(rows, cols):
            if cost[i, j] < _INF:
                self._update(self.marbles[j], td[i], *choice[i, j], cam_id=cam_id)
                d2 = detections[i].color_d2
                rival = float(np.min(np.delete(d2, j))) if len(d2) > 1 else float("inf")
                self.marbles[j].last_color = (float(d2[j]), rival)
        self._settle_finishes()

    def tick(self, now: float) -> None:
        """Advance the clock without measurements (detects silent cameras)."""
        self.now = max(self.now, now)
        self._check_health()
        self._settle_finishes()

    # -- ranking -----------------------------------------------------------
    def status(self, m: MarbleState, now: float) -> str:
        if m.finish_time is not None:
            return "finished"
        if not m.initialized:
            return "unseen"
        age = now - m.last_obs_t
        if (m.n_obs < 3 or m.fresh < 3) and age <= self.tracking_timeout:
            return "acquiring"  # too few (recent) observations for a reliable speed
        if age <= self.tracking_timeout:
            return "tracking"
        return "coasting" if age <= self.lost_after else "lost"

    def swap_identities(self, a: str, b: str) -> None:
        """Operator correction: the tracker has two marbles the wrong way round.

        Everything the filter knows (position, speed, history, finish) moves to the
        other name, and so do their places on the board.
        """
        ia = next(m.index for m in self.marbles if m.marble_id == a)
        ib = next(m.index for m in self.marbles if m.marble_id == b)
        ma, mb = self.marbles[ia], self.marbles[ib]
        for f in MarbleState.__dataclass_fields__:
            if f not in ("marble_id", "index"):
                va, vb = getattr(ma, f), getattr(mb, f)
                setattr(ma, f, vb)
                setattr(mb, f, va)
        pa, pb = self._order.index(ia), self._order.index(ib)
        self._order[pa], self._order[pb] = ib, ia
        self._lead_since = {}
        self.events.append(RaceEvent(self.now, "identity_swap", f"{a}<->{b}"))

    def lap_of(self, m: MarbleState) -> int:
        if not self.lap_length or not m.initialized:
            return 1
        if m.finish_time is not None:
            return self.laps
        return int(np.clip(np.floor(m.x[0] / self.lap_length) + 1, 1, self.laps))

    def leaderboard(self, now: float | None = None) -> list[LeaderboardEntry]:
        now = self.now if now is None else now
        finished = sorted((m for m in self.marbles if m.finish_time is not None), key=lambda m: m.finish_time)
        racing = [i for i in self._order if self.marbles[i].finish_time is None]
        # First sighting: insert at the true position (no hysteresis), once.
        for i in list(racing):
            m = self.marbles[i]
            if m.initialized and not m.placed:
                racing.remove(i)
                pos = next((k for k, j in enumerate(racing)
                            if not self.marbles[j].initialized or self.marbles[j].x[0] < m.x[0]), len(racing))
                racing.insert(pos, i)
                m.placed = True

        def at_now(m) -> float:
            # Each filter state is at its last update's time (a camera frame, or one image
            # row of it under a rolling shutter); compare everyone at the same instant.
            return float(m.x[0] + m.x[1] * np.clip(now - m.t, -0.05, 0.05))

        def key(i: int) -> float:
            m = self.marbles[i]
            return at_now(m) if m.initialized else -np.inf

        status = {i: self.status(self.marbles[i], now) for i in racing}
        observed = {i: status[i] == "tracking" for i in racing}

        def var(i: int) -> float:
            # Observed marble: pixel noise of the latest observation (inflated when
            # partly hidden); the 1 cm inter-camera floor is excluded, it is not a
            # marble-vs-marble error. Hidden or just-acquired marble: its full
            # prediction variance, which grows while it is unseen, so nobody passes
            # a marble whose whereabouts are uncertain. Lost marbles (unseen for
            # lost_after) no longer block the board.
            m = self.marbles[i]
            if observed[i]:
                return m.last_obs_r_px
            if status[i] in ("coasting", "acquiring"):
                return float(m.P[0, 0])
            return 0.0

        # An overtake is shown only when it is (a) bigger than rank_margin, so two
        # marbles running side by side never flicker, (b) statistically confident:
        # the lead exceeds rank_k standard deviations of the two observations,
        # (c) sustained for rank_confirm seconds (noise spikes are uncorrelated
        # frame to frame; a real overtake persists), and (d) made by a marble that
        # is currently seen: a hidden marble's position is a prediction, which is
        # not evidence. Each swap removes one strict inversion of the key order,
        # so the loop terminates.
        def need(a: int, b: int) -> float:
            return self.rank_margin + self.rank_k * np.sqrt(var(a) + var(b))

        def leads(b: int, a: int) -> bool:
            return key(b) > key(a) + need(a, b) and (observed[b] or not self.marbles[a].initialized)

        # Lead timers run for every pair, adjacent or not, so a marble passing
        # three rivals at once is confirmed past all three in the same instant.
        leading = {(b, a) for b in racing for a in racing if a != b and leads(b, a)}
        self._lead_since = {p: self._lead_since.get(p, now) for p in leading}

        def confirmed(b: int, a: int) -> bool:
            if (b, a) not in leading:
                return False
            # Persistence is only needed close to the threshold, where noise could
            # fake a lead; a lead one more margin beyond it is shown at once.
            clear = key(b) > key(a) + need(a, b) + self.rank_margin
            return (clear or now - self._lead_since[(b, a)] >= self.rank_confirm
                    or not self.marbles[a].placed)

        def may_pass(k: int) -> bool:
            a, b = racing[k], racing[k + 1]
            if confirmed(b, a):
                return True
            if observed[b] and status[a] in ("coasting", "acquiring"):
                # A hidden marble never blocks a pass between visible marbles: b may
                # pass it together with the nearest visible marble the board already
                # holds ahead of it, once b is confirmed ahead of that marble.
                c = next((racing[j] for j in range(k - 1, -1, -1) if observed[racing[j]]), None)
                return c is not None and confirmed(b, c)
            return False

        swapped = True
        while swapped:
            swapped = False
            for k in range(len(racing) - 1):
                if may_pass(k):
                    racing[k], racing[k + 1] = racing[k + 1], racing[k]
                    swapped = True
        self._order = [m.index for m in finished] + racing

        leader_s = max((key(i) for i in racing), default=0.0)
        close = set()
        for a, b in zip(finished, finished[1:]):
            # Too close to call: within the fixed timing resolution, or within
            # k sigma of the two finish estimates (e.g. one marble partly hidden).
            limit = max(self.photo_finish_s, self.photo_finish_k * np.hypot(a.finish_sigma, b.finish_sigma))
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
                    progress_m=(at_now(m) if m.finish_time is None else float(m.x[0])) if m.initialized else 0.0,
                    speed_mps=float(m.x[1]) if m.initialized else 0.0,
                    status=st,
                    finish_time=m.finish_time,
                    gap_m=0.0 if st == "finished" or not m.initialized else float(leader_s - at_now(m)),
                    photo_finish=i in close,
                    lap=self.lap_of(m),
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
