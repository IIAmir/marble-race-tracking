"""A venue race end to end: course, physics, the 14 cameras, the app's calibration and tracker, scored.

Everything between the rendered frames and the leaderboard is the production
code path of the Windows app:

* **Calibration as an operator does it**: waypoints clicked every 20 cm on each
  stretch of track a camera sees (1 px of hand jitter, a 2 mm tape-measure
  error; a new stretch where the camera sees another part of the track), the
  empty track captured over a second (spinners keep turning), every ball
  clicked three times for its colours (``marbletrack_app.calibration``), the
  band widened to the widest channel in view, the measured ball size.
* **Detection** with the app's processing scale (``engine._work_scale``).
* **NDI timing**: each camera stamps frames with its own clock (any offset),
  after its encoder latency; the network adds delay and jitter; the app's
  ``SenderClock`` maps the stamps to the PC clock and the latency measured on
  site (with a 1 ms error) is subtracted. The rolling shutter's row-to-row
  timing error stays in, as it would.
* **Fusion** configured as the app configures it (laps, stretches, the tunnel
  and the lift entered as places no camera can see), with the app's reorder delay.

Cameras are independent until fusion, so each camera's frames are rendered and
detected in its own worker process.
"""
from __future__ import annotations

import os
import pickle
import tempfile
import time
import zlib
from dataclasses import dataclass, field

import numpy as np

from marbletrack.fusion import FusionEngine, ReorderBuffer
from marbletrack.scoring import Scorer

from .course import BALL_R, STANDARD, make_course
from .physics import VENUE_SET, VENUE_SET_FIXED, PhysicsConfig, Race, quat_to_mat, simulate

SPACING = 0.2


class CalibrationFailed(Exception):
    pass


@dataclass
class VenueScenario:
    name: str = "venue"
    description: str = "The venue: 13 AIDA cameras + OBSBOT overhead, 60 fps, NDI timing."
    laps: int = 1
    balls: str = "fixed"  # fixed: 9 replaced by a pink ball; real: the yellow-stripe 9
    fps: float = 60.0
    overhead: bool = True
    n_track: int = 13
    light_ramp: tuple | None = None  # (t0, t1, final brightness factor)
    frame_drop: float = 0.002
    net_delay: tuple = (0.002, 0.004)  # minimum, mean of the exponential extra (s)
    latency_error_ms: float = 1.0  # how well the per-camera latency was measured on site
    outages: tuple = ()  # (camera id, t_off, t_on)
    duration_cap: float | None = None  # stop the race early (tests)
    extra: dict = field(default_factory=dict)


SCENARIOS = {s.name: s for s in [
    VenueScenario("venue", "One lap, the recommended ball set (9 replaced by a pink ball)."),
    VenueScenario("venue_laps", "Three laps of the circuit: lift, lapping, the finish on the last lap.", laps=3),
    VenueScenario("venue_real_set", "The current ball set: 1 and 9 are both yellow (9 is a stripe).", balls="real"),
    VenueScenario("venue_lighting", "The room light drops 25 % during the race.", light_ramp=(5.0, 15.0, 0.75)),
    VenueScenario("venue_camera_failure", "Two track cameras stop for 6 s mid-race.",
                  outages=(("cam03", 8.0, 14.0), ("cam07", 12.0, 18.0))),
]}


# ---------------------------------------------------------------------------- calibration (as an operator)
def _visible_runs(cam, course, step=0.01, min_len=0.15):
    s = np.arange(0.0, course.length, step)
    X = course.ball_centre(s, np.zeros(len(s)))
    vis = cam.visible(X, margin=10)
    vis &= ~course.in_any(s, course.blind)
    runs, start = [], None
    for k, v in enumerate(vis):
        if v and start is None:
            start = k
        if (not v or k == len(vis) - 1) and start is not None:
            end = k if v else k - 1
            if s[end] - s[start] >= min_len:
                runs.append((float(s[start]), float(s[end])))
            start = None
    return runs


def wide_zones(course, tol=0.002):
    """The route plan's wider sections, as the operator enters them: [[from, to, width], ...] (mm precision)."""
    wide = course.width > STANDARD + tol
    out, k = [], 0
    while k < len(wide):
        if wide[k]:
            j = k
            while j + 1 < len(wide) and wide[j + 1]:
                j += 1
            out.append([round(float(course.s[k]), 2), round(float(course.s[j]), 2), round(float(course.width[k:j + 1].max()), 3)])
            k = j + 1
        else:
            k += 1
    return out


def calibrate(cam, course, balls, laps, rng):
    """What the operator's setup produces for this camera: (CameraSetup, Project, background, calibration)."""
    from marbletrack_app import calibration as C
    from marbletrack_app.config import CameraSetup, MarbleDef, Project

    project = Project(name="venue", track_length=course.length, finish_s=course.finish_s, laps=laps,
                      marker_spacing=SPACING, track_width_m=STANDARD, marble_diameter_mm=2000 * BALL_R,
                      blind_zones=[list(z) for z in course.blind], wide_zones=wide_zones(course),
                      marbles=[MarbleDef(f"b{b.number}", b.name, "#%02x%02x%02x" % b.body_bgr[::-1]) for b in balls])
    markers, marks_s, breaks = [], [], []
    for lo, hi in _visible_runs(cam, course):
        grid = np.arange(np.ceil(lo / SPACING) * SPACING, hi + 1e-9, SPACING)
        grid = grid[grid < course.length]
        if len(grid) < 2:
            continue
        if markers:
            breaks.append(len(markers))
        uv, _ = cam.project(course.ball_centre(grid, np.zeros(len(grid))))
        uv = uv + rng.normal(0, 1.0, uv.shape)
        markers += [[round(float(x), 2), round(float(y), 2)] for x, y in uv]
        marks_s += [round(float(v + rng.normal(0, 0.002)), 4) for v in grid]
    if len(markers) < 2:
        raise CalibrationFailed("sees no track")
    marks_s = list(np.maximum.accumulate(np.array(marks_s) + np.arange(len(marks_s)) * 1e-6))
    W, H = cam.size
    setup = CameraSetup(key=cam.cam_id, backend="venue", device=cam.cam_id, name=cam.cam_id, width=W, height=H,
                        fps=cam.fps, markers=markers, marker_s=marks_s, breaks=breaks)
    # The operator drags the band-width slider until the blue band covers the channel everywhere.
    s_m = np.asarray(marks_s)
    _, _, Lv, _, w_true = course.frame(np.mod(s_m, course.length))
    c0 = course.ball_centre(s_m, np.zeros(len(s_m)))
    edge = [cam.project(c0 + sign * (w_true / 2)[:, None] * Lv)[0] for sign in (-1, 1)]
    mid = cam.project(c0)[0]
    need = np.maximum(np.linalg.norm(edge[0] - mid, axis=1), np.linalg.norm(edge[1] - mid, axis=1))
    have = C.band_halfwidth_px(setup, project)
    setup.width_scale = float(np.clip(np.max(need / np.maximum(have, 1e-6)) * 1.08, 0.5, 2.5))
    # empty track: the median of 12 frames over a second (spinners keep turning)
    bg = C.median_background([cam.render(None, 0.08 * k) for k in range(12)])
    mask = C.track_mask(setup, project, (W, H))
    # colours: every ball clicked at three places where this camera sees the track
    runs = [(a, b) for a, b in _visible_runs(cam, course) if b - a > 0.1]
    radii = []
    for spec in balls:
        mid = f"b{spec.number}"
        pix, meds, tries = [], [], 0
        while len(meds) < 3 and tries < 12:
            tries += 1
            lo, hi = runs[int(rng.integers(len(runs)))]
            s0 = rng.uniform(lo + 0.03, hi - 0.03)
            wdt = float(course.frame(np.array([s0]))[4][0])
            u0 = rng.uniform(-1, 1) * max(0.0, wdt / 2 - BALL_R) * 0.6
            q = rng.normal(size=4)
            q /= np.linalg.norm(q)
            c = course.ball_centre(np.array([s0]), np.array([u0]))[0]
            frame = cam.render(None, float(rng.uniform(0, 5)), parked=[(spec, c, quat_to_mat(q))])
            uv, _ = cam.project(c[None])
            click = uv[0] + rng.normal(0, 1.5, 2)
            r_exp = float(np.interp(s0, setup.marker_s, C.marble_radius_px(setup, project)))
            try:
                seg = C.segment_click(frame, bg, click, r_exp, mask)
            except C.CalibrationError:
                continue
            pix.append(seg["pixels"])
            meds.append(np.median(C.bgr_to_lab(seg["pixels"]), axis=0))
            radii.append(seg["radius"] / max(r_exp, 1e-6))
        if not pix:
            raise CalibrationFailed(f"could not sample ball {spec.name} ({len(radii)} other samples)")
        setup.palette[mid] = C.fit_sample(np.concatenate(pix), len(meds), meds)
    setup.radius_scale = float(np.median(radii)) if radii else 1.0
    calib = C.build_calibration(setup, project, bg)
    return setup, project, bg, calib


# ---------------------------------------------------------------------------- one camera's race
def _camera_job(args):
    seed, sc_name, spec, race_path, work_dir = args
    from marbletrack.detector import ColorDetector
    from marbletrack_app import calibration as C
    from marbletrack_app.engine import _min_radius_px, _work_scale
    from marbletrack_app.ndi import SenderClock

    from .camera import VenueCamera
    from .scene import build_scene
    from .shading import ShadowMap

    sc = SCENARIOS[sc_name] if isinstance(sc_name, str) else sc_name
    with open(race_path, "rb") as f:
        race: Race = pickle.load(f)
    course = race.course
    t0 = time.time()
    scene = build_scene(course, seed)
    shadows = ShadowMap(scene)
    salt = zlib.crc32(spec.cam_id.encode())
    cam = VenueCamera(spec, scene, shadows, course, seed=salt % 10000)
    rng = np.random.default_rng(seed * 7919 + salt)
    try:
        setup, project, bg, calib = calibrate(cam, course, race.balls, race.laps, rng)
    except CalibrationFailed as e:
        return {"cam_id": spec.cam_id, "ok": False, "reason": str(e)}
    setup_s = time.time() - t0
    W, H = cam.size
    det = ColorDetector(calib, work_scale=_work_scale(W, H, _min_radius_px(calib)))
    o = spec.optics
    enc_latency = 0.035 if "AIDA" in o.model else 0.05
    clock_offset = rng.uniform(-3, 3)  # the camera's own clock
    true_latency = o.readout / 2 + o.exposure / 2 + enc_latency  # mid-frame exposure -> sender stamp
    latency_cfg = true_latency + rng.normal(0, sc.latency_error_ms / 1000)
    clock = SenderClock()
    outage = next(((a, b) for c, a, b in sc.outages if c == spec.cam_id), None)
    end = race.duration + 0.5 if sc.duration_cap is None else min(race.duration + 0.5, sc.duration_cap)
    out = []
    n_frames = 0
    det_s = render_s = 0.0
    k = 0
    while True:
        t = spec.phase + k / o.fps
        k += 1
        if t > end:
            break
        if outage and outage[0] <= t < outage[1]:
            continue
        illum = 1.0
        if sc.light_ramp:
            a, b, f = sc.light_ramp
            illum = 1.0 + (f - 1.0) * float(np.clip((t - a) / (b - a), 0, 1))
        r0 = time.perf_counter()
        frame = cam.render(race, t, illum)
        r1 = time.perf_counter()
        sender = t + o.readout + o.exposure + enc_latency + clock_offset + rng.normal(0, 0.0003)
        arrival = t + o.readout + o.exposure + enc_latency + sc.net_delay[0] + rng.exponential(sc.net_delay[1])
        t_rep = clock.map(sender, arrival) - latency_cfg  # what the app stamps the frame with
        dets = det.detect(frame, t_rep)
        r2 = time.perf_counter()
        render_s += r1 - r0
        det_s += r2 - r1
        n_frames += 1
        if rng.random() < sc.frame_drop:
            continue
        out.append((arrival, t_rep, dets))
    return {"cam_id": spec.cam_id, "ok": True, "role": spec.role, "frames": out, "pieces": calib.pieces,
            "n_frames": n_frames, "render_ms": 1000 * render_s / max(1, n_frames), "detect_ms": 1000 * det_s / max(1, n_frames),
            "setup_s": setup_s, "markers": len(setup.markers), "stretches": len(setup.stretches),
            "palette_issues": [(i["a"], i["b"], i["d2"]) for i in C.palette_issues(setup, project)],
            "mm_per_px": float(1000 / np.median(C.px_per_metre(setup))),
            "work_scale": det.work_scale}


# ---------------------------------------------------------------------------- the race
def prepare(seed: int, sc: VenueScenario, cache_dir: str | None = None):
    balls = VENUE_SET if sc.balls == "real" else VENUE_SET_FIXED
    path = None
    if cache_dir:
        path = os.path.join(cache_dir, f"venue_race_{seed}_{sc.laps}_{sc.balls}.pkl")
        if os.path.exists(path):
            with open(path, "rb") as f:
                return pickle.load(f), path
    course = make_course(seed)
    race = simulate(course, balls, seed, PhysicsConfig(laps=sc.laps))
    if path:
        with open(path, "wb") as f:
            pickle.dump(race, f)
    return race, path


def run_venue_race(seed: int = 0, scenario: VenueScenario | str = "venue", workers: int = 4,
                   cache_dir: str | None = None, log=print) -> dict:
    from multiprocessing import get_context

    from .rig import place_cameras
    from .scene import build_scene

    sc = SCENARIOS[scenario] if isinstance(scenario, str) else scenario
    t_start = time.time()
    race, path = prepare(seed, sc, cache_dir)
    course = race.course
    tmp = None
    if path is None:
        fd, tmp = tempfile.mkstemp(suffix=".pkl")
        with os.fdopen(fd, "wb") as f:
            pickle.dump(race, f)
        path = tmp
    scene = build_scene(course, seed)
    specs, cov = place_cameras(course, scene, seed, n_track=sc.n_track, overhead=sc.overhead)
    log(f"[venue {sc.name} seed {seed}] lap {course.length:.1f} m x {race.laps}, race {race.duration:.1f} s, "
        f"{len(specs)} cameras, track coverage {cov['covered_racing']:.0%}")
    jobs = [(seed, sc, spec, path, cache_dir) for spec in specs]
    # the biggest job (4K overhead) first, so it does not finish last
    jobs.sort(key=lambda j: -j[2].optics.size[0])
    results = []
    with get_context("spawn").Pool(workers) as pool:
        for res in pool.imap_unordered(_camera_job, jobs):
            results.append(res)
            if res["ok"]:
                log(f"  {res['cam_id']}: {res['n_frames']} frames, {res['stretches']} stretch(es), "
                    f"{res['mm_per_px']:.2f} mm/px, render {res['render_ms']:.0f} ms + detect {res['detect_ms']:.0f} ms/frame, "
                    f"setup {res['setup_s']:.0f} s" + (f", palette issues {res['palette_issues']}" if res["palette_issues"] else ""))
            else:
                log(f"  {res['cam_id']}: {res['reason']}")
    if tmp:
        os.remove(tmp)
    if cache_dir:  # the detections, to re-run fusion and scoring without rendering again
        with open(os.path.join(cache_dir, f"venue_dets_{seed}_{sc.name}.pkl"), "wb") as f:
            pickle.dump({"sc": sc, "results": results, "coverage": cov}, f)
    report = fuse_and_score(race, [r for r in results if r["ok"]], sc)
    report.update(seed=seed, scenario=sc.name, wall_s=time.time() - t_start, coverage=cov,
                  cameras=[{k: v for k, v in r.items() if k != "frames"} for r in results])
    return report


def rescore(seed: int, scenario: str, cache_dir: str) -> dict:
    """Fusion and scoring again on the detections a previous run saved (no rendering)."""
    with open(os.path.join(cache_dir, f"venue_dets_{seed}_{scenario}.pkl"), "rb") as f:
        saved = pickle.load(f)
    sc = saved["sc"]
    race, _ = prepare(seed, sc, cache_dir)
    return fuse_and_score(race, [r for r in saved["results"] if r["ok"]], sc)


def fuse_and_score(race: Race, cams: list[dict], sc: VenueScenario) -> dict:
    """Feed every camera's detections to the fusion engine in arrival order, as the app does, and score it."""
    course = race.course
    ids = [f"b{b.number}" for b in race.balls]
    laps = race.laps
    L = course.length
    s_fin = (laps - 1) * L + course.finish_s
    engine = FusionEngine(ids, s_fin, L, cameras={c["cam_id"]: c["pieces"] for c in cams},
                          static_blind_zones=list(course.blind), rank_margin=BALL_R, photo_finish_s=0.006,
                          laps=laps, lap_length=L if laps > 1 else None)
    buf = ReorderBuffer(latency=0.08)
    stream = sorted((a, t, c["cam_id"], d) for c in cams for a, t, d in c["frames"])
    tie = 4 * BALL_R
    truth_finish = race.finish_times

    def truth(t):
        return race.progress(t)

    scorer = Scorer(ids, truth, truth_finish, s_fin, tie)
    last_eval = -1.0
    board = []
    for arrival, t, cam_id, dets in stream:
        buf.push(t, cam_id, dets)
        for tc, cid, d in buf.pop_ready(arrival):
            engine.process(tc, d, cid)
        engine.tick(arrival - buf.latency)
        if arrival - last_eval >= 1 / 30:
            board = scorer.evaluate(engine, engine.now)
            last_eval = arrival
    for tc, cid, d in buf.flush():
        engine.process(tc, d, cid)
    engine.tick(engine.now + 1.0)
    board = scorer.evaluate(engine, engine.now)
    res = scorer.final(board)
    rack = [ids[i] for i in race.rack_order]
    res.update(rack_order=rack, rack_matches_board=[e.marble_id for e in board] == rack,
               issues=scorer.issues[:200], n_issues=len(scorer.issues), laps=laps, lap_m=L,
               frames=sum(len(c["frames"]) for c in cams))
    return res
