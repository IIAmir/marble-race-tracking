"""End-to-end race runs on simulated cameras, scored against ground truth."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .detector import ColorDetector
from .fusion import FusionEngine, ReorderBuffer
from .scoring import Scorer
from .sim import (
    DEFAULT_MARBLES,
    RaceConfig,
    RaceSimulator,
    finish_camera,
    multi_camera_rig,
    serpentine_track,
    single_camera_rig,
)


_DEBUG = bool(os.environ.get("MARBLETRACK_DEBUG"))


@dataclass
class Scenario:
    name: str
    description: str
    race: RaceConfig = field(default_factory=RaceConfig)
    modes: tuple[str, ...] = ("single", "multi")
    frame_drop: float = 0.0  # probability a frame never reaches the fusion host
    net_delay: tuple[float, float] = (0.005, 0.03)  # edge -> fusion delivery delay (s)
    outages: tuple[tuple[int, float, float], ...] = ()  # (camera index, t_off, t_on)
    script: Callable | None = None  # (track, cfg, rng) -> (times, traj, lateral) scripted race
    overlap: float = 0.12  # camera overlap (multi-camera rig)
    calib_bias: float = 0.0  # 1-sigma per-camera centerline calibration error (m)
    fps: float | None = None  # segment camera frame rate override


SCENARIOS = {
    s.name: s
    for s in [
        Scenario("nominal", "Default race: 10 marbles, one tunnel, overtakes throughout."),
        Scenario(
            "pack",
            "Tight pack: near-identical speeds and wide lateral wander, so marbles constantly "
            "overlap and hide each other.",
            RaceConfig(speed_range=(1.40, 1.46), wobble=0.10, lateral_amp=0.016),
        ),
        Scenario(
            "occlusion",
            "Three tunnels hide 1.3 m of track (10% of the course); overtakes happen inside them.",
            RaceConfig(tunnels=((3.6, 4.0), (6.2, 6.7), (10.9, 11.3))),
        ),
        Scenario(
            "camera_failure",
            "Two of the 14 cameras die mid-race for 3 s each; their sections become blind zones.",
            modes=("multi",),
            outages=((4, 2.0, 5.0), (9, 5.0, 8.0)),
        ),
        Scenario(
            "network",
            "10% of frames lost, delivery jitter up to 120 ms (beyond the 50 ms reorder window).",
            frame_drop=0.10,
            net_delay=(0.005, 0.12),
        ),
        Scenario(
            "lighting",
            "Scene brightness falls 20% during the race (lamp drift, sunlight change).",
            RaceConfig(light_ramp=(2.0, 6.0, 0.8)),
        ),
        Scenario(
            "worst_case",
            "Everything at once: tight pack, three tunnels, 10% frame loss with 120 ms jitter, "
            "20% lighting drop.",
            RaceConfig(speed_range=(1.40, 1.46), wobble=0.10, lateral_amp=0.016,
                       tunnels=((3.6, 4.0), (6.2, 6.7), (10.9, 11.3)), light_ramp=(2.0, 6.0, 0.8)),
            frame_drop=0.10,
            net_delay=(0.005, 0.12),
        ),
    ]
}


@dataclass
class RaceReport:
    scenario: str
    mode: str
    view: str
    seed: int
    n_cameras: int
    frames: int
    rank_accuracy: float  # share of instants whose ranking of all *visible* marbles is correct
    rank_accuracy_all: float  # same, including marbles hidden in tunnels / camera gaps
    rank_accuracy_latency: float  # visible ranking correct allowing the 100 ms display budget
    phantom_overtakes: int  # displayed passes that never happened on the track
    overtakes_shown: int
    final_order_correct: bool  # official order (photo-finish pairs flagged for review count as ties)
    final_order_strict: bool  # exact order, no review allowed
    photo_finishes: int  # marbles flagged for photo-finish review
    finish_err_ms_mean: float
    finish_err_ms_max: float
    pos_err_cm_rms: float  # tracked (not coasting) marbles
    id_errors: int  # tracked marble-instants more than 15 cm from the truth
    late_batches: int
    detect_ms_per_frame: float
    fuse_ms_per_batch: float
    truth_order: list[str] = field(default_factory=list)
    final_order: list[str] = field(default_factory=list)
    events: list = field(default_factory=list)
    replay: dict | None = None


def build_cameras(mode, track, cfg, seed=0, fps=30.0, overlap=0.12, view=None, finish_cam=True):
    """Camera rig for a mode. 14-camera mode and the finish camera use 45-degree
    angled perspective cameras by default; ``view="topdown"`` gives the planar rig."""
    from .sim3d import angled_camera_rig, angled_finish_camera

    view = view or ("topdown" if mode == "single" else "angled")
    if mode == "single":
        cams = single_camera_rig(track, cfg, seed)
    elif view == "angled":
        cams = angled_camera_rig(track, cfg, seed=seed, fps=fps, overlap=overlap)
    else:
        cams = multi_camera_rig(track, cfg, seed=seed, fps=fps, overlap=overlap)
    if finish_cam:
        s_finish = track.s_range[1] - cfg.finish_before_end
        cams.append(angled_finish_camera(track, cfg, s_finish, seed) if view != "topdown" or mode == "single"
                    else finish_camera(track, cfg, s_finish, seed))
    return cams


def run_race(mode: str = "single", seed: int = 0, fps: float = 30.0,
             scenario: Scenario | str = "nominal", finish_cam: bool = True,
             record: bool = False, on_frame=None, view: str | None = None) -> RaceReport:
    sc = SCENARIOS[scenario] if isinstance(scenario, str) else scenario
    cfg = sc.race
    marbles = DEFAULT_MARBLES
    ids = [m.marble_id for m in marbles]
    colors = [m.bgr for m in marbles]
    track = serpentine_track(cfg)
    L = track.s_range[1]
    fps = sc.fps or fps
    if sc.script is not None:
        sim = RaceSimulator.scripted(track, marbles, cfg, *sc.script(track, cfg, np.random.default_rng(seed)))
    else:
        sim = RaceSimulator(track, marbles, cfg, seed=seed)
    cams = build_cameras(mode, track, cfg, seed, fps, sc.overlap, view, finish_cam)
    s_finish = L - cfg.finish_before_end
    bias_rng = np.random.default_rng(seed + 11)
    detectors = {c.cam_id: ColorDetector(c.calibration(marbles, sim, s_bias=float(bias_rng.normal(0, sc.calib_bias))))
                 for c in cams}

    # Timing resolution: ~1 ms with a 240 fps finish camera, ~5 ms at 30 fps.
    engine = FusionEngine(ids, s_finish, L, cameras={c.cam_id: c.s_range for c in cams},
                          static_blind_zones=list(cfg.tunnels), rank_margin=cfg.marble_radius,
                          photo_finish_s=0.0005 if finish_cam else 0.006)
    buf = ReorderBuffer(latency=0.05)
    rng = np.random.default_rng(seed + 7)
    truth_finish = sim.finish_times(s_finish)
    outages = {cams[k].cam_id: (a, b) for k, a, b in sc.outages if k < len(cams)}

    end = sim.duration + 0.3
    events = sorted((n / (c.fps or fps) + c.phase, k) for k, c in enumerate(cams)
                    for n in range(int(end * (c.fps or fps))))
    arrivals = []  # (arrival time, capture time, cam_id, detections)
    det_time = fuse_time = 0.0
    n_det = n_batches = 0
    tie = 4 * cfg.marble_radius  # two diameters: closer than this counts as a tie
    replay = ({"t": [], "truth": [], "lat": [], "est": [], "v": [], "obs_s": [], "obs_cam": [], "color": [], "status": [],
               "order": [], "cams": []} if record else None)
    cam_index = {c.cam_id: k for k, c in enumerate(cams)}

    def record(t, board, truth):
        if replay is None:
            return
        est = {e.marble_id: e for e in board}
        replay["t"].append(round(t, 4))
        replay["truth"].append([round(float(v), 4) for v in truth])
        replay["est"].append([round(est[i].progress_m, 4) for i in ids])
        replay["lat"].append([round(float(v), 4) for v in sim.lateral(t)])
        replay["v"].append([round(est[i].speed_mps, 3) for i in ids])
        replay["obs_s"].append([None if m.last_obs_s is None else round(m.last_obs_s, 4) for m in engine.marbles])
        replay["obs_cam"].append([cam_index.get(m.last_obs_cam, -1) for m in engine.marbles])
        replay["color"].append([[round(m.last_color[0], 1), round(min(m.last_color[1], 9999.0), 1)]
                                for m in engine.marbles])
        replay["status"].append([est[i].status for i in ids])
        replay["order"].append([ids.index(e.marble_id) for e in board])
        replay["cams"].append([int(v) for v in engine.camera_status().values()])

    scorer = Scorer(ids, sim.progress, truth_finish, L - cfg.finish_before_end, tie, on_eval=record)

    def evaluate(t):
        return scorer.evaluate(engine, t)

    last_eval = -1.0
    for t, k in events:
        cam = cams[k]
        off = outages.get(cam.cam_id)
        if off and off[0] <= t < off[1]:
            continue  # camera dead: no frame, no heartbeat
        xy, vis = sim.world_positions(t)
        frame = cam.render(xy, vis, colors, cfg.illumination(t))
        t0 = time.perf_counter()
        dets = detectors[cam.cam_id].detect(frame, t)
        det_time += time.perf_counter() - t0
        n_det += 1
        if on_frame is not None:
            on_frame(cam, frame, dets, engine, t)
        if rng.random() >= sc.frame_drop:
            arrivals.append((t + rng.uniform(*sc.net_delay), t, cam.cam_id, dets))
        arrivals.sort(key=lambda a: a[0])
        while arrivals and arrivals[0][0] <= t:
            _, tc, cid, d = arrivals.pop(0)
            buf.push(tc, cid, d)
        for tc, cid, d in buf.pop_ready(t):
            t0 = time.perf_counter()
            engine.process(tc, d, cid)
            fuse_time += time.perf_counter() - t0
            n_batches += 1
        engine.tick(t - buf.latency)
        if t - last_eval >= 1 / fps - 1e-9:
            evaluate(engine.now)
            last_eval = t
    for _, tc, cid, d in sorted(arrivals, key=lambda a: a[0]):
        buf.push(tc, cid, d)
    for tc, cid, d in buf.flush():
        engine.process(tc, d, cid)
        n_batches += 1
    board = evaluate(engine.now)

    res = scorer.final(board)
    est = {e.marble_id: e.finish_time for e in board}
    if replay is not None:
        replay.update(
            ids=ids,
            colors=["#%02x%02x%02x" % (c[2], c[1], c[0]) for c in colors],
            track=[[round(float(x), 4), round(float(y), 4)] for x, y in track.resample(0.05).points],
            length=L,
            s_finish=s_finish,
            tunnels=list(cfg.tunnels),
            cam_ranges=[list(c.s_range) for c in cams],
            cam_footprints=[c.footprint() for c in cams],
            cam_positions=[[round(float(v), 4) for v in c.position] if hasattr(c, "position") else None for c in cams],
            marble_radius=cfg.marble_radius,
            track_width=cfg.track_width,
            table=[cfg.width, cfg.height],
            cam_ids=[c.cam_id for c in cams],
            truth_finish=[float(v) for v in truth_finish],
            est_finish=[est[i] for i in ids],
            finish_sigma=[m.finish_sigma for m in engine.marbles],
            photo=[next(e.photo_finish for e in board if e.marble_id == i) for i in ids],
            rank_margin=engine.rank_margin,
            fps=fps,
        )
    return RaceReport(
        scenario=sc.name,
        mode=mode,
        view=view or ("topdown" if mode == "single" else "angled"),
        seed=seed,
        n_cameras=len(cams),
        frames=n_det,
        late_batches=buf.late,
        detect_ms_per_frame=det_time / max(1, n_det) * 1e3,
        fuse_ms_per_batch=fuse_time / max(1, n_batches) * 1e3,
        events=[(round(e.t, 3), e.kind, e.subject) for e in engine.events],
        replay=replay,
        **res,
    )
