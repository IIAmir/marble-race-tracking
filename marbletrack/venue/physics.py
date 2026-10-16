"""Billiard balls racing round the circuit: gravity, friction, walls, obstacles, laps.

Each ball lives in channel coordinates: total progress ``s`` (laps included) and
lateral offset ``u`` across the floor. It rolls without slipping, so gravity
along the track accelerates it at 5/7 g sin(slope) (a solid sphere), less
rolling resistance on printed PLA and air drag. In a turn it is pushed toward
the outer wall (v^2 x curvature) and scrubs along it, and banking pulls it back
in. Balls collide with each other as discs in the channel plane. Printed track
pieces meet every 20 cm or so and every joint gives a small random lateral kick.

Obstacles: pins (hard), pinball bumpers (they kick back), lane splitters, spinners
(rotating arms that strike the ball), speed bumps (a speed loss and a hop: the
ball leaves the floor for a moment, which a camera sees as parallax).

A race: the gate opens, balls run ``laps`` laps. On the lift (enclosed) they ride
up at constant speed, in single file. After the finish line of their last lap
the diverter sends them to the rack, which shows the arrival order.

Nothing assumes a speed: speeds come out of the slopes, turns, obstacles and
collisions, and change with every course.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .course import BALL_R, Course

G = 9.81


@dataclass
class BallSpec:
    number: int  # 0 = cue ball
    name: str
    body_bgr: tuple[int, int, int]
    stripe: bool = False


# The venue's set, as photographed in the finish rack. Ball 9 is a yellow stripe in the real
# set; VENUE_SET_FIXED replaces it with a pink solid, as recommended.
VENUE_SET = [
    BallSpec(1, "1 Yellow", (20, 175, 240)),
    BallSpec(2, "2 Blue", (160, 70, 25)),
    BallSpec(3, "3 Red", (35, 30, 205)),
    BallSpec(4, "4 Purple", (110, 30, 70)),
    BallSpec(5, "5 Orange", (25, 85, 240)),
    BallSpec(6, "6 Teal", (120, 135, 10)),
    BallSpec(7, "7 Maroon", (40, 25, 120)),
    BallSpec(8, "8 Black", (28, 26, 26)),
    BallSpec(9, "9 Yellow stripe", (20, 175, 240), stripe=True),
    BallSpec(0, "Cue", (232, 236, 238)),
]
VENUE_SET_FIXED = VENUE_SET[:8] + [BallSpec(9, "9 Pink", (150, 70, 235))] + VENUE_SET[9:]


@dataclass
class PhysicsConfig:
    dt: float = 0.0005
    laps: int = 1
    rolling_resistance: float = 0.012  # x g, printed PLA
    rr_spread: float = 0.06  # per-ball variation: near-identical balls
    drag: float = 0.01  # 1/m: 0.5 rho Cd A / m for a 14 g, 25 mm ball
    wall_restitution: float = 0.35
    wall_friction: float = 0.12
    ball_restitution: float = 0.75
    pin_restitution: float = 0.45
    bumper_restitution: float = 0.8
    bumper_kick: float = 0.25  # m/s added by a pinball bumper's ring
    spinner_restitution: float = 0.5
    bump_loss: float = 0.06  # share of speed lost on a speed bump
    joint_spacing: float = 0.2
    joint_kick: float = 0.05  # m/s lateral kick at a joint (random sign and size)
    lift_speed: float = 0.35  # m/s along the lift
    hold: float = 0.4  # start gate opens
    gate_s: float = 0.32
    max_time: float = 240.0


@dataclass
class Race:
    course: Course
    balls: list[BallSpec]
    laps: int
    t: np.ndarray  # (T,) sample times (1 ms)
    s: np.ndarray  # (T, K) total progress (laps included)
    u: np.ndarray  # (T, K)
    v: np.ndarray  # (T, K) speed along the track
    q: np.ndarray  # (T, K, 4) orientation quaternions (w, x, y, z), body -> world
    hop: np.ndarray  # (T, K) height above the floor (m) during a hop
    finish_times: np.ndarray  # (K,) crossing of the finish line on the last lap (inf if never)
    racked_at: np.ndarray  # (K,) when each ball reached the rack (inf if never)
    rack_order: list[int]  # ball indices, first arrival first (what the finish rack shows)
    start_order: list[int]
    duration: float
    seed: int = 0
    nudges: list = field(default_factory=list)

    @property
    def s_finish(self) -> float:
        """Total progress of the finish line on the last lap."""
        return (self.laps - 1) * self.course.length + self.course.finish_s

    def state(self, t: float):
        """(s, u, v, q, hop) of every ball at time ``t`` (linear interpolation; q normalised lerp)."""
        i = int(np.clip(np.searchsorted(self.t, t) - 1, 0, len(self.t) - 2))
        f = float(np.clip((t - self.t[i]) / (self.t[i + 1] - self.t[i]), 0.0, 1.0))
        lin = [a[i] * (1 - f) + a[i + 1] * f for a in (self.s, self.u, self.v, self.hop)]
        q0, q1 = self.q[i], self.q[i + 1]
        q1 = np.where((q0 * q1).sum(1, keepdims=True) < 0, -q1, q1)
        q = q0 * (1 - f) + q1 * f
        q /= np.linalg.norm(q, axis=1, keepdims=True)
        return lin[0], lin[1], lin[2], q, lin[3]

    def progress(self, t: float) -> np.ndarray:
        return self.state(t)[0]

    def on_track(self, t: float) -> np.ndarray:
        """Balls still on the circuit (not yet in the rack)."""
        return t < self.racked_at

    def rack_position(self, k: int) -> np.ndarray:
        """Centre of the ball in rack slot ``k`` (0 = first arrival, at the top)."""
        r = self.course.rack
        slot = len(self.balls) - 1 - k
        return r["base"] + np.array([0.0, 0.0, BALL_R + 0.004 + slot * (2 * BALL_R + 0.001)])


def _qmul(a, b):
    w1, x1, y1, z1 = a.T
    w2, x2, y2, z2 = b.T
    return np.c_[w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2, w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
                 w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2, w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2]


def quat_to_mat(q):
    w, x, y, z = np.moveaxis(np.asarray(q), -1, 0)
    return np.stack([np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], -1),
                     np.stack([2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)], -1),
                     np.stack([2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)], -1)], -2)


def _seg_point(p, a, b):
    """Closest point to ``p`` on segment a-b (2-D) and the parameter along it."""
    ab = b - a
    t = float(np.clip(np.dot(p - a, ab) / max(np.dot(ab, ab), 1e-12), 0, 1))
    return a + t * ab, t


def simulate(course: Course, balls: list[BallSpec], seed: int = 0, cfg: PhysicsConfig | None = None) -> Race:
    cfg = cfg or PhysicsConfig()
    rng = np.random.default_rng(seed)
    k = len(balls)
    r = BALL_R
    dt = cfg.dt
    Lc = course.length
    ob = course.obstacles
    order = rng.permutation(k)  # the draw: who starts where in the queue
    s = np.empty(k)
    for slot, b in enumerate(order):  # slot 0 sits at the gate
        s[b] = cfg.gate_s - r - 0.0005 - slot * (2 * r + 0.0008)
    u = rng.uniform(-0.002, 0.002, k)
    vs = np.zeros(k)
    vu = np.zeros(k)
    hop = np.zeros(k)
    vz = np.zeros(k)
    q = np.tile([1.0, 0.0, 0.0, 0.0], (k, 1))
    rr = cfg.rolling_resistance * (1 + cfg.rr_spread * rng.uniform(-1, 1, k))
    s_finish = (cfg.laps - 1) * Lc + course.finish_s
    s_divert = (cfg.laps - 1) * Lc + course.divert_s
    grid_step = 0.002
    grid = np.arange(0, Lc + grid_step, grid_step)
    P, T, Lat, U, W = course.frame(grid)
    kap = course.curvature(grid)
    gT, gL = (np.array([0, 0, -G]) * T).sum(1), (np.array([0, 0, -G]) * Lat).sum(1)
    in_lift = course.in_any(grid, [course.lift])
    posts = ob.posts
    bumps = np.array(ob.bumps, float).reshape(-1, 2)
    joint_of = np.floor(s / cfg.joint_spacing)

    rec_every = max(1, int(round(0.001 / dt)))
    times, S, Uu, V, Q, HOP = [], [], [], [], [], []
    finish = np.full(k, np.inf)
    racked = np.full(k, np.inf)
    rack_order: list[int] = []
    active = np.ones(k, bool)
    still = np.zeros(k)
    nudges = []
    t = 0.0
    step = 0
    while t < cfg.max_time and active.any():
        sl = np.mod(s, Lc)
        i = np.clip((sl / grid_step).astype(int), 0, len(grid) - 1)
        w_half = W[i] / 2 - r
        lift = in_lift[i]
        a_s = (5 / 7) * gT[i] - rr * G * np.sign(vs) * (np.abs(vs) > 1e-4) - cfg.drag * vs * np.abs(vs)
        a_u = (5 / 7) * gL[i] - kap[i] * vs**2 - 2.0 * vu
        at_wall = np.abs(u) >= w_half - 1e-5
        press = np.where(u > 0, a_u, -a_u)
        a_s -= np.where(at_wall & (press > 0), cfg.wall_friction * press * np.sign(vs), 0.0)
        vs += a_s * dt
        vu += a_u * dt
        # the lift carries balls up at its own speed, in single file, centred
        vs = np.where(lift, cfg.lift_speed, vs)
        vu = np.where(lift, -u * 20, vu)
        s_new = s + vs * dt / np.maximum(1 - kap[i] * u, 0.2)
        u_new = u + vu * dt
        hit = np.abs(u_new) > w_half
        vu = np.where(hit & (np.sign(vu) == np.sign(u_new)), -cfg.wall_restitution * vu, vu)
        u_new = np.clip(u_new, -w_half, w_half)
        # hops (after a speed bump): ballistic, then back on the floor
        vz -= G * dt * (hop > 0)
        hop = np.maximum(hop + vz * dt, 0.0)
        vz = np.where(hop <= 0, 0.0, vz)
        # speed bumps
        if len(bumps):
            for bs, bh in bumps:
                cross = (np.mod(s, Lc) < bs) & (np.mod(s_new, Lc) >= bs) & active
                if cross.any():
                    vs[cross] *= 1 - cfg.bump_loss * (bh / 0.004)
                    vz[cross] = np.maximum(vz[cross], np.sqrt(2 * G * bh) * np.minimum(1.0, np.abs(vs[cross]) / 0.8))
                    hop[cross] = np.maximum(hop[cross], 1e-4)
        # joints between printed pieces: a small random lateral kick
        jn = np.floor(s_new / cfg.joint_spacing)
        crossed = (jn > joint_of) & ~lift
        if crossed.any():
            vu[crossed] += rng.normal(0, cfg.joint_kick, crossed.sum()) * np.minimum(1.0, np.abs(vs[crossed]))
        joint_of = jn
        s, u = s_new, u_new
        # start gate (first lap only)
        if t < cfg.hold:
            stop = s > cfg.gate_s - r
            s[stop], vs[stop] = cfg.gate_s - r, np.minimum(vs[stop], 0)
        # obstacles
        sl = np.mod(s, Lc)
        if len(posts):
            dps = sl[:, None] - posts[None, :, 0]
            dpu = u[:, None] - posts[None, :, 1]
            dp = np.hypot(dps, dpu)
            for a, m in zip(*np.nonzero((dp < r + posts[None, :, 2]) & active[:, None])):
                n = np.array([dps[a, m], dpu[a, m]]) / max(dp[a, m], 1e-9)
                rel = vs[a] * n[0] + vu[a] * n[1]
                if rel < 0:
                    bumper = posts[m, 3] == 1
                    e = cfg.bumper_restitution if bumper else cfg.pin_restitution
                    imp = -(1 + e) * rel + (cfg.bumper_kick if bumper else 0.0)
                    vs[a] += imp * n[0]
                    vu[a] += imp * n[1] + rng.normal(0, 0.02)
                over = r + posts[m, 2] - dp[a, m]
                s[a] += over * n[0]
                u[a] += over * n[1]
        for s0, s1, u0, ht in ob.dividers:
            near = active & (sl > s0 - r - ht) & (sl < s1 + r) & (np.abs(u - u0) < r + ht)
            for a in np.flatnonzero(near):
                p = np.array([sl[a], u[a]])
                c, _ = _seg_point(p, np.array([s0, u0]), np.array([s1, u0]))
                d = np.linalg.norm(p - c)
                if d < r + ht:
                    n = (p - c) / max(d, 1e-9) if d > 1e-9 else np.array([0.0, 1.0 if rng.random() < 0.5 else -1.0])
                    rel = vs[a] * n[0] + vu[a] * n[1]
                    if rel < 0:
                        vs[a] -= 1.5 * rel * n[0]
                        vu[a] -= 1.5 * rel * n[1]
                    s[a] += (r + ht - d) * n[0]
                    u[a] += (r + ht - d) * n[1]
        if ob.spinners:
            arms = ob.spinner_arms(t)
            arm_r = 0.003
            for (hub, tip), (sp_s, sp_u, ln, n_, w_, ph) in zip(
                    arms, [sp for sp in ob.spinners for _ in range(int(sp[3]))]):
                near = active & (np.abs(sl - hub[0]) < ln + r + arm_r)
                for a in np.flatnonzero(near):
                    p = np.array([sl[a], u[a]])
                    c, _ = _seg_point(p, hub, tip)
                    d = np.linalg.norm(p - c)
                    if d < r + arm_r:
                        n = (p - c) / max(d, 1e-9)
                        rc = c - hub
                        v_arm = w_ * np.array([-rc[1], rc[0]])  # velocity of the arm at the contact
                        rel = (vs[a] - v_arm[0]) * n[0] + (vu[a] - v_arm[1]) * n[1]
                        if rel < 0:
                            imp = -(1 + cfg.spinner_restitution) * rel
                            vs[a] += imp * n[0]
                            vu[a] += imp * n[1]
                        s[a] += (r + arm_r - d) * n[0]
                        u[a] += (r + arm_r - d) * n[1]
        # ball-ball collisions (discs in the channel plane; distances wrap round the circuit)
        for _ in range(2):
            ds_ = s[:, None] - s[None, :]
            ds_ = ds_ - Lc * np.round(ds_ / Lc)
            du_ = u[:, None] - u[None, :]
            d = np.hypot(ds_, du_)
            close = np.triu(d < 2 * r, 1) & active[:, None] & active[None, :]
            ii, jj = np.nonzero(close)
            for a, b in zip(ii, jj):
                n = np.array([ds_[a, b], du_[a, b]]) / max(d[a, b], 1e-9)
                rel = (vs[a] - vs[b]) * n[0] + (vu[a] - vu[b]) * n[1]
                if rel < 0:
                    j = -(1 + cfg.ball_restitution) * rel / 2
                    vs[a] += j * n[0]
                    vu[a] += j * n[1]
                    vs[b] -= j * n[0]
                    vu[b] -= j * n[1]
                over = 2 * r - d[a, b]
                s[a] += over / 2 * n[0]
                u[a] += over / 2 * n[1]
                s[b] -= over / 2 * n[0]
                u[b] -= over / 2 * n[1]
            if len(ii) == 0:
                break
        i = np.clip((np.mod(s, Lc) / grid_step).astype(int), 0, len(grid) - 1)
        u = np.clip(u, -(W[i] / 2 - r), W[i] / 2 - r)
        if t < cfg.hold:
            s = np.minimum(s, cfg.gate_s - r)
        # rolling without slipping: spin about (floor normal x velocity)
        vel = vs[:, None] * T[i] + vu[:, None] * Lat[i]
        omega = np.cross(U[i], vel) / r
        ang = np.linalg.norm(omega, axis=1) * dt
        axis = omega / np.maximum(np.linalg.norm(omega, axis=1, keepdims=True), 1e-12)
        dq = np.c_[np.cos(ang / 2), axis * np.sin(ang / 2)[:, None]]
        q = np.where(active[:, None], _qmul(dq, q), q)
        if step % 200 == 0:
            q /= np.linalg.norm(q, axis=1, keepdims=True)
        t += dt
        step += 1
        # finish line on the last lap, then the diverter to the rack
        newly = np.isinf(finish) & (s >= s_finish) & active
        if newly.any():
            prev = s - vs * dt
            f = np.clip((s_finish - prev[newly]) / np.maximum(vs[newly] * dt, 1e-9), 0, 1)
            finish[newly] = t - dt + f * dt
        gone = active & (s >= s_divert)
        for b in np.flatnonzero(gone):
            racked[b] = t
            rack_order.append(int(b))
            active[b] = False
            vs[b] = vu[b] = 0.0
            s[b] = s_divert
        # a marshal frees a ball that has not moved for 1.5 s (as at a real track)
        if step % 100 == 0:
            moving = np.abs(vs) > 0.03
            still = np.where(moving | ~active | (t < cfg.hold), 0.0, still + 100 * dt)
            stuck = still > 1.5
            if stuck.any():
                vs[stuck] += 0.25
                vu[stuck] += rng.normal(0, 0.1, stuck.sum())
                still[stuck] = 0.0
                nudges.append((round(t, 3), [int(x) for x in np.flatnonzero(stuck)]))
        if step % rec_every == 0:
            times.append(t)
            S.append(s.copy())
            Uu.append(u.copy())
            V.append(vs.copy())
            Q.append(q.copy())
            HOP.append(hop.copy())
    return Race(course=course, balls=balls, laps=cfg.laps, t=np.array(times), s=np.array(S), u=np.array(Uu),
                v=np.array(V), q=np.array(Q), hop=np.array(HOP), finish_times=finish, racked_at=racked,
                rack_order=rack_order, start_order=[int(b) for b in order], duration=t, seed=seed, nudges=nudges)
