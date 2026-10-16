"""A Monway-style course: a closed 3-D circuit through a diorama, different for every draw.

The route is built like the real layouts: a start gate high on a mountain, then a
shuffled sequence of building blocks (hairpin stacks, chicanes, banked sweepers,
pretzel loops that cross over themselves on a bridge, wiggles, steep drops, a
tunnel, a spiral tower) and obstacle zones on wide sections (pin fields,
pinball bumpers, lane splitters, rotating spinners, speed bumps, funnels). After
the finish line a diverter sends balls that have run their last lap into the
finish rack; everyone else rolls back along a low return path to a lift behind
the start, rides up and starts the next lap. So every course can run any
number of laps, and none of the tracker's settings can quietly depend on one
layout.

Geometry is per vertex of a dense centerline (1 cm): position, unit tangent, a
lateral unit vector across the channel (banked in turns), the floor normal, and
the channel's inner width. Progress ``s`` runs from the start line (0) once round
the circuit (``length``); a ball on lap n is at total progress ``n * length + s``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

BALL_R = 0.0125  # 25 mm billiard balls
NARROW = 0.036  # single file
STANDARD = 0.056  # two balls side by side
WALL_H = 0.018  # wall height above the floor (m)
WALL_T = 0.004  # wall thickness (m)


@dataclass
class Obstacles:
    """Everything a ball can hit besides the walls, in channel coordinates (s, u)."""

    posts: np.ndarray = field(default_factory=lambda: np.zeros((0, 4)))  # (s, u, radius, kind) kind 0 pin, 1 bumper
    dividers: list = field(default_factory=list)  # (s0, s1, u, half_thickness): lane splitters
    spinners: list = field(default_factory=list)  # (s, u, arm_length, arms, omega rad/s, phase)
    bumps: list = field(default_factory=list)  # (s, height): speed bumps across the channel

    def spinner_arms(self, t: float):
        """(N, 2, 2) arm segments (hub, tip) in (s, u) at time ``t``."""
        out = []
        for s, u, ln, n, w, ph in self.spinners:
            for k in range(int(n)):
                a = ph + w * t + 2 * np.pi * k / n
                out.append([[s, u], [s + ln * np.cos(a), u + ln * np.sin(a)]])
        return np.array(out, float).reshape(-1, 2, 2)


@dataclass
class Course:
    s: np.ndarray  # (N,) progress (m) from the start line
    P: np.ndarray  # (N, 3) centerline on the channel floor
    T: np.ndarray  # (N, 3) unit tangent
    L: np.ndarray  # (N, 3) unit lateral (to the left of travel, in the banked floor plane)
    U: np.ndarray  # (N, 3) floor normal
    width: np.ndarray  # (N,) inner width (m)
    tags: list
    tunnels: list[tuple[float, float]]
    helix: list[tuple[float, float]]  # s ranges of spiral-tower turns
    helix_axis: tuple | None  # (cx, cy, radius, z_lo, z_hi)
    lift: tuple[float, float]  # s range of the (enclosed) lift
    finish_s: float
    divert_s: float  # balls on their last lap leave for the rack here
    rack: dict  # where the finish rack stands
    obstacles: Obstacles
    seed: int = 0
    notes: dict = field(default_factory=dict)

    @property
    def length(self) -> float:
        return float(self.s[-1])

    def local(self, s_total):
        return np.mod(np.asarray(s_total, float), self.length)

    def frame(self, s):
        """Interpolated (P, T, L, U, width) at progress ``s`` (wraps round the circuit)."""
        s = self.local(s)
        i = np.clip(np.searchsorted(self.s, s, side="right") - 1, 0, len(self.s) - 2)
        f = ((s - self.s[i]) / (self.s[i + 1] - self.s[i]))[..., None]

        def lerp(a):
            return a[i] * (1 - f) + a[i + 1] * f

        def unit(v):
            return v / np.linalg.norm(v, axis=-1, keepdims=True)

        return lerp(self.P), unit(lerp(self.T)), unit(lerp(self.L)), unit(lerp(self.U)), lerp(self.width[:, None])[..., 0]

    def ball_centre(self, s, u, hop=0.0):
        """World position of a ball centre at progress ``s``, lateral offset ``u`` and hop height."""
        P, _, L, U, _ = self.frame(s)
        return P + np.asarray(u, float)[..., None] * L + (BALL_R + np.asarray(hop, float))[..., None] * U

    def curvature(self, s):
        """Signed horizontal curvature (1/m, positive = turning left) at ``s``."""
        ds = 0.01
        _, T0, _, _, _ = self.frame(np.asarray(s) - ds)
        _, T1, _, _, _ = self.frame(np.asarray(s) + ds)
        cross = T0[..., 0] * T1[..., 1] - T0[..., 1] * T1[..., 0]
        h0 = np.linalg.norm(T0[..., :2], axis=-1)
        h1 = np.linalg.norm(T1[..., :2], axis=-1)
        return cross / np.maximum(h0 * h1, 1e-9) / (2 * ds)

    def in_any(self, s, ranges) -> np.ndarray:
        s = self.local(s)
        out = np.zeros(np.shape(s), bool)
        for a, b in ranges:
            out |= (s >= a) & (s <= b)
        return out

    @property
    def blind(self) -> list[tuple[float, float]]:
        """Stretches no camera can see by construction: tunnels and the enclosed lift."""
        return sorted(list(self.tunnels) + [self.lift])


# ---------------------------------------------------------------------------- turtle
class _Turtle:
    """Builds the centerline piece by piece: heading, grade and bank change smoothly."""

    def __init__(self, z0: float, heading: float):
        self.p = np.array([0.0, 0.0, z0])
        self.h = heading
        self.pts, self.heads, self.grades, self.banks, self.widths, self.tags = [], [], [], [], [], []
        self.s = 0.0
        self.ss = []

    def _emit(self, grade, bank, width, tag):
        self.pts.append(self.p.copy())
        self.heads.append(self.h)
        self.grades.append(grade)
        self.banks.append(bank)
        self.widths.append(width)
        self.tags.append(tag)
        self.ss.append(self.s)

    def run(self, length, grade, curvature=0.0, bank=0.0, width=STANDARD, tag="", step=0.01):
        n = max(1, int(round(length / step)))
        ds = length / n
        for _ in range(n):
            self._emit(grade, bank, width, tag)
            self.h += curvature * ds / 2
            horiz = ds / np.sqrt(1 + grade**2)
            self.p = self.p + np.array([np.cos(self.h) * horiz, np.sin(self.h) * horiz, -grade * horiz])
            self.h += curvature * ds / 2
            self.s += ds

    def arc(self, radius, angle_deg, grade, bank_deg=8.0, width=STANDARD, tag=""):
        sign = np.sign(angle_deg)
        length = radius * np.radians(abs(angle_deg))
        # the floor tilts toward the inside of the turn
        self.run(length, grade, sign / radius, np.radians(bank_deg) * sign, width, tag)

    def arrays(self):
        return (np.array(self.ss), np.array(self.pts), np.array(self.heads), np.array(self.grades),
                np.array(self.banks), np.array(self.widths), list(self.tags))


def _smooth(x, k):
    if k <= 1:
        return x
    ker = np.hanning(k + 2)[1:-1]
    ker /= ker.sum()
    pad = np.r_[np.full(k, x[0]), x, np.full(k, x[-1])]
    return np.convolve(pad, ker, mode="same")[k:-k]


def _build(seed: int):
    """One random route: a shuffled sequence of building blocks, steered to stay on the table."""
    rng = np.random.default_rng(seed)
    t = _Turtle(0.0, rng.uniform(0, 2 * np.pi))
    tunnels, helix_turns, helix_axis, zones = [], [], None, []
    centre = np.zeros(2)

    def toward_centre():
        """Turn direction (+1 left, -1 right) that usually bends back toward the middle of the layout."""
        to_c = centre - t.p[:2]
        if np.linalg.norm(to_c) < 0.3:
            return rng.choice([-1, 1])
        cross = np.cos(t.h) * to_c[1] - np.sin(t.h) * to_c[0]
        side = 1 if cross > 0 else -1
        return side if rng.random() < 0.75 else -side

    def w_any():
        return float(rng.choice([NARROW, STANDARD, STANDARD, 0.07]))

    def hairpins():
        turn = toward_centre()
        w = w_any()
        for _ in range(int(rng.integers(2, 4))):
            t.run(rng.uniform(0.6, 1.0), rng.uniform(0.055, 0.08), width=w)
            t.arc(rng.uniform(0.16, 0.24), 180 * turn, rng.uniform(0.04, 0.06), bank_deg=rng.uniform(8, 16), width=w)
            turn = -turn

    def chicane():
        turn = toward_centre()
        w = w_any()
        for _ in range(int(rng.integers(3, 6))):
            t.arc(rng.uniform(0.15, 0.3), rng.uniform(55, 115) * turn, rng.uniform(0.04, 0.06),
                  bank_deg=rng.uniform(6, 14), width=w)
            t.run(rng.uniform(0.05, 0.2), 0.05, width=w)
            turn = -turn

    def sweeper():
        t.arc(rng.uniform(0.35, 0.6), rng.uniform(100, 200) * toward_centre(), rng.uniform(0.035, 0.05),
              bank_deg=rng.uniform(16, 24), width=float(rng.choice([STANDARD, 0.075, 0.09])))

    def wiggle():
        w = w_any()
        for _ in range(int(rng.integers(4, 7))):
            t.arc(rng.uniform(0.13, 0.35), rng.uniform(25, 80) * rng.choice([-1, 1]), rng.uniform(0.045, 0.065),
                  bank_deg=rng.uniform(4, 12), width=w)

    def pretzel():
        """A loop that comes back under itself: the track crosses over on a bridge."""
        t.run(0.15, 0.06)
        t.arc(rng.uniform(0.24, 0.32), rng.uniform(340, 380) * toward_centre(), 0.06, bank_deg=12, tag="cross")
        t.run(0.2, 0.06)

    def drop():
        t.run(0.08, 0.08)
        t.run(rng.uniform(0.2, 0.3), rng.uniform(0.14, 0.18), tag="drop")
        t.run(0.1, 0.05)

    def zone(kind, length, width, grade=None):
        """A wide obstacle zone, with tapers in and out (the tapers funnel balls together)."""
        t.run(0.12, 0.06, width=(STANDARD + width) / 2)
        a = t.s
        t.run(length, rng.uniform(0.05, 0.07) if grade is None else grade, width=width, tag="zone")
        zones.append((kind, a, t.s, width))
        t.run(0.12, 0.06, width=(STANDARD + width) / 2)

    def pins():
        zone("pins", rng.uniform(0.6, 1.0), rng.uniform(0.09, 0.13))

    def bumpers():
        zone("bumpers", rng.uniform(0.5, 0.8), rng.uniform(0.1, 0.14))

    def splitter():
        zone("splitter", rng.uniform(0.5, 0.9), rng.uniform(0.08, 0.1))

    def spinner():
        zone("spinner", rng.uniform(0.35, 0.5), rng.uniform(0.1, 0.13))

    def speed_bumps():
        zone("bumps", rng.uniform(0.5, 0.8), float(rng.choice([STANDARD, 0.07])), grade=rng.uniform(0.07, 0.09))

    def funnel():
        zone("funnel", rng.uniform(0.3, 0.45), rng.uniform(0.12, 0.15))

    def tunnel():
        a0 = t.s
        t.arc(rng.uniform(0.4, 0.7), rng.uniform(40, 90) * toward_centre(), rng.uniform(0.05, 0.07), tag="tunnel")
        t.run(rng.uniform(0.15, 0.35), 0.06, tag="tunnel")
        tunnels.append((a0 + 0.02, t.s - 0.02))
        t.run(0.3, 0.05)

    def helix():
        nonlocal helix_axis
        turn = toward_centre()
        radius = rng.uniform(0.17, 0.21)
        pitch = rng.uniform(0.085, 0.1)  # drop per turn: one level clears the next by ~6 cm
        grade = pitch / (2 * np.pi * radius)
        cdir = t.h + turn * np.pi / 2
        c = t.p[:2] + radius * np.array([np.cos(cdir), np.sin(cdir)])
        w = float(rng.choice([STANDARD, 0.064]))
        for _ in range(int(rng.integers(2, 4))):
            a = t.s
            t.arc(radius, 360 * turn, grade, bank_deg=10, tag="helix", width=w)
            helix_turns.append((a, t.s))
        helix_axis = (float(c[0]), float(c[1]), float(radius))
        t.run(0.3, 0.05)

    t.run(0.45, 0.03, tag="start")
    t.run(0.3, 0.06)
    shape = [hairpins, tunnel, helix, pretzel] + list(rng.choice(
        [chicane, sweeper, wiggle, drop, pretzel, hairpins, chicane, wiggle], size=int(rng.integers(1, 4))))
    obst = list(rng.choice([pins, bumpers, splitter, spinner, speed_bumps, funnel], size=int(rng.integers(4, 7))))
    obst += [pins, spinner]  # every course has a pin field and a spinner
    first = shape.pop(0)
    blocks = shape + obst
    rng.shuffle(blocks)
    def steer_home():
        """Far from the middle and heading away: bend back (keeps the layout on one table)."""
        to_c = centre - t.p[:2]
        dist = np.linalg.norm(to_c)
        if dist < 0.9:
            return
        want = np.arctan2(to_c[1], to_c[0])
        diff = (want - t.h + np.pi) % (2 * np.pi) - np.pi
        if abs(diff) > np.radians(50):
            t.arc(rng.uniform(0.22, 0.4), np.degrees(diff) * rng.uniform(0.8, 1.0), rng.uniform(0.04, 0.055),
                  bank_deg=rng.uniform(8, 16), width=float(rng.choice([STANDARD, 0.07])))

    for block in [first] + blocks:
        block()
        t.run(rng.uniform(0.1, 0.25), rng.uniform(0.04, 0.06))
        steer_home()
    # finish straight, the diverter to the rack, the start of the way back
    t.run(0.3, 0.04)
    f0 = t.s
    t.run(0.9, 0.025, tag="finish")
    finish_s = f0 + 0.5
    divert_s = f0 + 0.8
    t.run(0.25, 0.03)
    return t, tunnels, helix_turns, helix_axis, zones, finish_s, divert_s


def _close_loop(t: _Turtle):
    """Way back to the start: a low return path to a lift behind the start line, up, and in."""
    s, P, head, grade, bank, width, tags = t.arrays()
    h0 = head[0]
    d0 = np.array([np.cos(h0), np.sin(h0)])
    z_start = P[0, 2]
    E, hE = t.p.copy(), t.h
    lift_angle = np.radians(55.0)
    T_top = np.r_[P[0, :2] - 0.25 * d0, z_start + 0.006]
    z_base = min(E[2] - 0.02, P[:, 2].min() - 0.01)
    run_h = (T_top[2] - z_base) / np.tan(lift_angle)
    B = np.r_[T_top[:2] - run_h * d0, z_base]
    # return path: the shortest arc-straight-arc (Dubins) path, never tighter than 22 cm radius
    xy, rhead = _dubins(E[:2], hE, B[:2], h0, 0.22)
    seg = np.r_[0, np.cumsum(np.linalg.norm(np.diff(xy, axis=0), axis=1))]
    n = max(2, int(seg[-1] / 0.01))
    si = np.linspace(0, seg[-1], n + 1)[1:]
    rx, ry = np.interp(si, seg, xy[:, 0]), np.interp(si, seg, xy[:, 1])
    rhead = np.interp(si, seg, rhead)
    rz = E[2] + (B[2] - E[2]) * si / seg[-1]
    ret = np.c_[rx, ry, rz]
    curv = np.abs(np.gradient(rhead) / 0.01)
    # the lift: a straight incline at 55 degrees up to the start, enclosed
    nl = int(np.hypot(run_h, T_top[2] - z_base) / 0.01)
    fl = np.linspace(0, 1, nl + 1)[1:, None]
    lift = B + fl * (T_top - B)
    # transfer from the top of the lift to the start line
    tr = T_top + np.linspace(0, 1, 26)[1:-1, None] * (np.r_[P[0, :2], z_start] - T_top)
    pts = np.vstack([P, ret, lift, tr])
    heads = np.r_[head, rhead, np.full(len(lift), h0), np.full(len(tr), h0)]
    grades = np.r_[grade, np.full(len(ret), 0.02), np.full(len(lift), -np.tan(lift_angle)), np.full(len(tr), 0.02)]
    banks = np.r_[bank, np.zeros(len(ret) + len(lift) + len(tr))]
    widths = np.r_[width, np.full(len(ret) + len(lift) + len(tr), STANDARD)]
    tg = tags + ["return"] * len(ret) + ["lift"] * len(lift) + ["transfer"] * len(tr)
    return pts, heads, grades, banks, widths, tg, float(curv.max())


def _dubins(p0, h0, p1, h1, r, step=0.002):
    """Shortest CSC Dubins path (LSL, RSR, LSR, RSL) from (p0, h0) to (p1, h1): points and headings."""
    best = None
    for a, b in ((1, 1), (-1, -1), (1, -1), (-1, 1)):
        c0 = p0 + r * a * np.array([-np.sin(h0), np.cos(h0)])
        c1 = p1 + r * b * np.array([-np.sin(h1), np.cos(h1)])
        d = c1 - c0
        D = np.linalg.norm(d)
        if a == b:
            th = np.arctan2(d[1], d[0])
            straight = D
        else:
            if D < 2 * r:
                continue
            straight = np.sqrt(D**2 - 4 * r**2)
            th = np.arctan2(d[1], d[0]) + a * np.arcsin(2 * r / D)  # inner tangent
        t1 = (a * (th - h0)) % (2 * np.pi)
        t2 = (b * (h1 - th)) % (2 * np.pi)
        total = r * t1 + straight + r * t2
        if best is None or total < best[0]:
            best = (total, a, b, c0, c1, th, t1, t2, straight)
    _, a, b, c0, c1, th, t1, t2, straight = best
    pts, heads = [], []
    for ang in np.arange(0, t1, step / r):  # first arc
        h = h0 + a * ang
        pts.append(c0 + r * a * np.array([np.sin(h), -np.cos(h)]))
        heads.append(h)
    h = h0 + a * t1
    q0 = c0 + r * a * np.array([np.sin(h), -np.cos(h)])
    for d_ in np.arange(0, straight, step):
        pts.append(q0 + d_ * np.array([np.cos(h), np.sin(h)]))
        heads.append(h)
    for ang in np.arange(0, t2 + 1e-9, step / r):
        hh = h + b * ang
        pts.append(c1 + r * b * np.array([np.sin(hh), -np.cos(hh)]))
        heads.append(hh)
    return np.array(pts), np.unwrap(np.array(heads))


def _self_clearance(s, P):
    """Pairs of points of the circuit that are far apart along it but collide in space."""
    sub = slice(None, None, 5)
    ss, pp = s[sub], P[sub]
    L = s[-1]
    d_s = np.abs(ss[:, None] - ss[None, :])
    d_s = np.minimum(d_s, L - d_s)
    d_xy = np.linalg.norm(pp[:, None, :2] - pp[None, :, :2], axis=-1)
    d_z = np.abs(pp[:, None, 2] - pp[None, :, 2])
    clash = (d_s > 0.6) & (d_xy < 0.1) & (d_z < 0.065)
    return int(clash.sum())


def _obstacles(course: Course, zones, rng) -> Obstacles:
    ob = Obstacles()
    posts = []

    def half(s0):
        return float(np.interp(s0, course.s, course.width)) / 2

    for kind, a, b, w in zones:
        if kind == "pins":
            # one pin per row, cycling centre / left / right; every gap is either passable or closed
            ph = int(rng.integers(0, 3))
            for k, s0 in enumerate(np.arange(a + 0.08, b - 0.06, rng.uniform(0.065, 0.08))):
                p = half(s0) - (2 * BALL_R + 0.004 + 0.004)
                posts.append((s0, (0.0, p, -p)[(k + ph) % 3], 0.004, 0))
        elif kind == "bumpers":
            n = int(rng.integers(2, 4))
            for k, s0 in enumerate(np.linspace(a + 0.12, b - 0.12, n)):
                rb = rng.uniform(0.012, 0.016)
                lim = half(s0) - (2 * BALL_R + 0.004 + rb)  # a ball always fits beside a bumper
                posts.append((s0, float(rng.uniform(-1, 1) * max(0.0, lim) * 0.9) if k % 2 else 0.0, rb, 1))
        elif kind == "splitter":
            ob.dividers.append((a + 0.08, b - 0.08, 0.0, 0.003))
        elif kind == "spinner":
            s0 = (a + b) / 2
            arm = half(s0) - 2 * BALL_R - 0.004
            ob.spinners.append((s0, 0.0, max(0.02, arm), int(rng.choice([2, 3, 4])),
                                float(rng.uniform(2.5, 5.0) * rng.choice([-1, 1])), float(rng.uniform(0, 2 * np.pi))))
        elif kind == "bumps":
            for s0 in np.arange(a + 0.1, b - 0.05, rng.uniform(0.12, 0.18)):
                ob.bumps.append((float(s0), float(rng.uniform(0.003, 0.005))))
    ob.posts = np.array(posts, float).reshape(-1, 4)
    return ob


def make_course(seed: int = 0, z_end: float = 0.06, max_extent=(5.5, 4.0)) -> Course:
    for attempt in range(600):
        t, tunnels, helix, axis, zones, finish_s, divert_s = _build(seed * 1000 + attempt)
        pts, heads, grades, banks, widths, tags, ret_curv = _close_loop(t)
        s = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))]
        extent = pts[:, :2].max(0) - pts[:, :2].min(0)
        if (max(extent) <= max_extent[0] and min(extent) <= max_extent[1] and ret_curv < 1 / 0.12
                and _self_clearance(s, pts) == 0):
            break
    else:  # pragma: no cover
        raise RuntimeError("could not lay out a course without self-intersection")
    # close the loop: the last vertex is the first one again
    pts = np.vstack([pts, pts[:1]])
    s = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))]
    heads, grades, banks = np.r_[heads, heads[0]], np.r_[grades, grades[0]], np.r_[banks, banks[0]]
    widths, tags = np.r_[widths, widths[0]], tags + [tags[0]]
    P = pts.copy()
    P[:, 2] += z_end - P[:, 2].min()
    widths = _smooth(widths, 15)
    banks = _smooth(banks, 25)
    T = np.gradient(P, s, axis=0)
    T /= np.linalg.norm(T, axis=1, keepdims=True)
    horiz_left = np.c_[-np.sin(heads), np.cos(heads), np.zeros_like(heads)]
    # bank: rotate the lateral axis about the tangent; a positive bank lowers the left side (inside of a left turn)
    Lh = horiz_left - (horiz_left * T).sum(1, keepdims=True) * T
    Lh /= np.linalg.norm(Lh, axis=1, keepdims=True)
    Up0 = np.cross(T, Lh)
    c, sn = np.cos(-banks)[:, None], np.sin(-banks)[:, None]
    Lb = c * Lh + sn * Up0
    Ub = np.cross(T, Lb)
    lift_idx = np.flatnonzero(np.array([g == "lift" for g in tags]))
    lift = (float(s[lift_idx[0]]) - 0.03, float(s[lift_idx[-1]]) + 0.03)
    # the rack stands beside the diverter, on the right of travel
    i_div = int(np.searchsorted(s, divert_s))
    out_dir = -Lb[i_div].copy()
    out_dir[2] = 0.0
    out_dir /= np.linalg.norm(out_dir)
    rack = {"base": P[i_div] + 0.07 * out_dir, "out": out_dir, "along": T[i_div].copy(), "s": float(divert_s)}
    helix_axis = None
    if helix:
        hz = P[(s >= helix[0][0]) & (s <= helix[-1][1]), 2]
        helix_axis = (axis[0], axis[1], axis[2], float(hz.min() - 0.03), float(hz.max() + 0.06))
    course = Course(s=s, P=P, T=T, L=Lb, U=Ub, width=widths, tags=tags, tunnels=tunnels, helix=helix,
                    helix_axis=helix_axis, lift=lift, finish_s=float(finish_s), divert_s=float(divert_s), rack=rack,
                    obstacles=Obstacles(), seed=seed, notes={"attempts": attempt + 1, "zones": zones})
    course.obstacles = _obstacles(course, zones, np.random.default_rng(seed + 77))
    return course
