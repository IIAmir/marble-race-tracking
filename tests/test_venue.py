"""The venue simulator: courses, physics, rendering, and calibration the way an operator does it."""
from dataclasses import replace

import numpy as np
import pytest

from marbletrack.venue.course import BALL_R, make_course
from marbletrack.venue.physics import VENUE_SET_FIXED, PhysicsConfig, quat_to_mat, simulate


@pytest.fixture(scope="module")
def course():
    return make_course(3)


def test_courses_are_closed_circuits_and_reproducible(course):
    again = make_course(3)
    assert np.allclose(course.P, again.P) and course.length == again.length
    assert np.linalg.norm(course.P[0] - course.P[-1]) < 1e-9  # back to the start line
    seg = np.linalg.norm(np.diff(course.P, axis=0), axis=1)
    assert seg.max() < 0.025  # no jumps anywhere, the lift and the return path included
    assert 0 < course.finish_s < course.divert_s < course.lift[0] < course.lift[1] <= course.length
    assert {t for t in course.tags} >= {"start", "finish", "lift", "return", "helix", "tunnel", "zone"}
    kinds = {z[0] for z in course.notes["zones"]}
    assert "pins" in kinds and "spinner" in kinds


def test_obstacles_always_leave_room_for_a_ball(course):
    ob = course.obstacles
    for s, u, r, kind in ob.posts:
        half = float(np.interp(s, course.s, course.width)) / 2
        gaps = [half + u - r, half - u - r]  # beside the post, either side
        assert max(gaps) >= 2 * BALL_R + 0.003
        assert all(g >= 2 * BALL_R + 0.003 or g < 0.004 for g in gaps if g > 0) or max(gaps) >= 2 * BALL_R + 0.003
    for s, u, ln, n, w, ph in ob.spinners:
        half = float(np.interp(s, course.s, course.width)) / 2
        assert half - ln >= 2 * BALL_R  # a ball fits past the tip of an arm


@pytest.mark.slow
def test_physics_short_race_rolls_downhill_without_assumed_speeds(course):
    race = simulate(course, VENUE_SET_FIXED, seed=3, cfg=PhysicsConfig(max_time=6.0))
    s = race.s
    assert np.all(s[-1] > s[0] + 2.0)  # everyone is on the way after 6 s
    assert 0.4 < np.percentile(race.v, 99) < 3.0  # speeds come from the slopes
    gaps = np.abs(s[:, :, None] - s[:, None, :]) + np.eye(len(race.balls)) * 9
    lat = np.abs(race.u[:, :, None] - race.u[:, None, :])
    assert np.all(np.hypot(gaps, lat) > 2 * BALL_R - 0.002)  # balls never overlap


@pytest.mark.slow
def test_render_and_operator_calibration_find_a_parked_ball(course):
    from marbletrack.detector import ColorDetector
    from marbletrack.venue.camera import AIDA, CameraSpec, VenueCamera, look_at
    from marbletrack.venue.run import calibrate
    from marbletrack.venue.scene import build_scene
    from marbletrack.venue.shading import ShadowMap

    scene = build_scene(course, 3)
    shadows = ShadowMap(scene, res=1024)
    s0 = course.finish_s - 0.4  # a stretch no scenery hides from this camera
    P, T, L, U, _ = course.frame(np.array([s0]))
    target = P[0] + BALL_R * U[0]
    side = np.cross(T[0], [0, 0, 1.0])
    side /= np.linalg.norm(side)
    C = target + 0.5 * (0.6 * side + 0.8 * np.array([0, 0, 1.0]) - 0.2 * T[0])
    optics = replace(AIDA, size=(640, 360))
    cam = VenueCamera(CameraSpec("t", optics, C, look_at(C, target)), scene, shadows, course, seed=1)
    ball = VENUE_SET_FIXED[2]  # red 3
    c = course.ball_centre(np.array([s0]), np.array([0.0]))[0]
    frame = cam.render(None, 0.0, parked=[(ball, c, quat_to_mat(np.array([1.0, 0, 0, 0])))])
    uv, _ = cam.project(c[None])
    x, y = np.round(uv[0]).astype(int)
    b, g, r = frame[y, x].astype(int)
    assert r > g + 40 and r > b + 40  # a red ball is where it should be
    got = calibrate(cam, course, VENUE_SET_FIXED, 1, np.random.default_rng(0))
    assert got is not None
    setup, project, bg, calib = got
    det = ColorDetector(calib)
    dets = det.detect(frame, 0.0)
    assert len(dets) == 1 and int(np.argmin(dets[0].color_d2)) == 2
    assert abs(dets[0].candidates[0].s - s0) < 0.01


@pytest.mark.slow
def test_finish_rack_camera_reads_rendered_billiard_balls(course):
    """The rack reader on rendered balls (number discs, shading, the rack tube), colours learned by clicks."""
    from marbletrack.color import bgr_to_lab
    from marbletrack.venue.camera import AIDA, CameraSpec, VenueCamera, look_at
    from marbletrack.venue.scene import build_scene
    from marbletrack.venue.shading import ShadowMap
    from marbletrack_app import calibration as C
    from marbletrack_app.calibration import to_model
    from marbletrack_app.rack import read_rack

    scene = build_scene(course, 3)
    shadows = ShadowMap(scene, res=1024)
    r = course.rack
    n = len(VENUE_SET_FIXED)
    centres = [r["base"] + np.array([0.0, 0.0, BALL_R + 0.004 + (n - 1 - k) * (2 * BALL_R + 0.001)]) for k in range(n)]
    mid = (centres[0] + centres[-1]) / 2
    cam = None
    spots = [(az, dist, dz) for az in (180, 160, 200, 140, 220) for dz in (0.0, 0.08, 0.16) for dist in (0.3, 0.4)]
    for az, dist, dz in spots:  # facing the rack, where an installer would put the camera
        a = np.radians(az)
        d = np.cos(a) * r["out"] + np.sin(a) * np.cross([0.0, 0.0, 1.0], r["out"])
        C0 = mid + dist * d + np.array([0.0, 0.0, dz])
        if C0[2] < float(scene.height(C0[0], C0[1])) + 0.02:
            continue
        cam = VenueCamera(CameraSpec("rack", replace(AIDA, size=(640, 360)), C0, look_at(C0, mid)), scene, shadows, course, seed=2)
        if cam.visible(np.array(centres)).all():
            break
    assert cam is not None and cam.visible(np.array(centres)).all()
    uv, z = cam.project(np.array(centres))
    radii = cam.f * BALL_R / z
    rot = quat_to_mat(np.array([1.0, 0, 0, 0]))
    bg = cam.render(None, 0.0)
    rng = np.random.default_rng(0)
    palette = []
    for spec in VENUE_SET_FIXED:  # the operator clicks each marble sitting in the rack, in three slots
        pix, meds = [], []
        for k in rng.choice(n, 3, replace=False):
            frame = cam.render(None, 0.0, parked=[(spec, centres[k], rot)])
            seg = C.segment_click(frame, bg, uv[k], radii[k])
            pix.append(seg["pixels"])
            meds.append(np.median(bgr_to_lab(seg["pixels"]), axis=0))
        palette.append(to_model(C.fit_sample(np.concatenate(pix), 3, meds)))
    ids = [f"b{b.number}" for b in VENUE_SET_FIXED]
    order = list(rng.permutation(n))
    quats = rng.normal(size=(n, 4))
    quats /= np.linalg.norm(quats, axis=1, keepdims=True)  # any orientation: number discs may face the camera
    parked = [(VENUE_SET_FIXED[b], centres[k], quat_to_mat(quats[k])) for k, b in enumerate(order)]
    frame = cam.render(None, 0.0, parked=parked[:7])  # seven marbles in so far
    got = read_rack(frame, bg, uv, radii, palette, ids)
    assert got[:7] == [ids[b] for b in order[:7]] and got[7:] == [None] * 3
