import numpy as np

from marbletrack.color import ColorModel, bgr_to_lab, palette_separation
from marbletrack.detector import ColorDetector
from marbletrack.sim import DEFAULT_MARBLES, RaceConfig, RaceSimulator, multi_camera_rig, serpentine_track


def _camera():
    cfg = RaceConfig()
    track = serpentine_track(cfg)
    sim = RaceSimulator(track, DEFAULT_MARBLES, cfg, seed=1)
    cam = multi_camera_rig(track, cfg, seed=1)[2]
    return cfg, track, cam, cam.calibration(DEFAULT_MARBLES, sim)


def test_colour_model_rejects_specular_outliers():
    rng = np.random.default_rng(0)
    red = np.tile([[40, 40, 210]], (500, 1)) + rng.normal(0, 4, (500, 3))
    glare = np.tile([[250, 250, 250]], (40, 1))
    model = ColorModel.fit(bgr_to_lab(np.clip(np.vstack([red, glare]), 0, 255)))
    assert model.d2(bgr_to_lab([[40, 40, 210]]))[0] < 3
    assert model.d2(bgr_to_lab([[250, 250, 250]]))[0] > 100


def test_detector_finds_touching_marbles_of_different_colours():
    cfg, track, cam, calib = _camera()
    det = ColorDetector(calib)
    s_mid = sum(cam.s_range) / 2
    s = np.array([s_mid, s_mid + 2 * cfg.marble_radius * 0.95])  # touching
    xy, _ = track.point_at(s)
    frame = cam.render(xy, [True, True], [DEFAULT_MARBLES[0].bgr, DEFAULT_MARBLES[4].bgr])
    dets = det.detect(frame, 0.0)
    ids = sorted(int(np.argmin(d.color_d2)) for d in dets)
    assert ids == [0, 4]
    for d in dets:
        k = int(np.argmin(d.color_d2))
        true_s = s[0] if k == 0 else s[1]
        assert abs(d.candidates[0].s - true_s) < 0.01


def test_palette_is_separable_for_default_marbles():
    *_, calib = _camera()
    sep = palette_separation(calib.palette)
    assert sep.min() > 50


def _angled():
    from marbletrack.sim3d import angled_camera_rig
    cfg = RaceConfig(tunnels=())
    track = serpentine_track(cfg)
    sim = RaceSimulator(track, DEFAULT_MARBLES, cfg, seed=1)
    cams = angled_camera_rig(track, cfg, seed=1)
    return cfg, track, sim, cams


def test_angled_views_measure_progress_despite_parallax_and_walls():
    cfg, track, sim, cams = _angled()
    for cam in cams[:8]:  # eight different viewing directions
        det = ColorDetector(cam.calibration(DEFAULT_MARBLES, sim))
        lo, hi = cam.s_range
        for s in np.linspace(lo + 0.08, hi - 0.08, 5):
            for lat in (-0.012, 0.0, 0.012):  # near lane, centre, far lane (walls hide part of the marble)
                xy, tan = track.point_at(np.array([s]))
                pos = xy[0] + lat * np.array([-tan[0, 1], tan[0, 0]])
                dets = det.detect(cam.render([pos], [True], [DEFAULT_MARBLES[3].bgr]), 0.0)
                assert len(dets) == 1, (cam.cam_id, s, lat)
                assert int(np.argmin(dets[0].color_d2)) == 3
                assert abs(dets[0].candidates[0].s - s) < 0.006, (cam.cam_id, s, lat, dets[0].candidates[0].s)


def test_marble_beyond_section_end_is_not_reported_as_clipped_fragment():
    cfg, track, sim, cams = _angled()
    for cam in cams:
        det = ColorDetector(cam.calibration(DEFAULT_MARBLES, sim))
        lo, hi = cam.s_range
        for s in (hi + 0.02, lo - 0.02):  # just outside the section: may be partly in view
            if not 0.05 < s < track.s_range[1] - 0.05:
                continue
            xy, _ = track.point_at(np.array([s]))
            for d in det.detect(cam.render([xy[0]], [True], [DEFAULT_MARBLES[0].bgr]), 0.0):
                # whatever is reported must be where the marble really is
                assert abs(d.candidates[0].s - s) < 0.01, (cam.cam_id, s, d.candidates[0].s)


def test_shrink_matches_area_resize_and_is_fast():
    import time

    import cv2

    from marbletrack.detector import shrink

    rng = np.random.default_rng(0)
    img = cv2.GaussianBlur(rng.integers(0, 256, (1080, 1920, 3), dtype=np.uint8), (0, 0), 3)
    size = (739, 416)  # 1080p at the processing scale of a 25 mm marble seen by an AIDA camera
    ref = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
    got = shrink(img, size)
    assert got.shape == ref.shape and np.abs(got.astype(int) - ref).mean() < 2.0
    assert shrink(img, (960, 540)).shape == (540, 960, 3)
    t = time.perf_counter()
    for _ in range(5):
        shrink(img, size)
    fast = time.perf_counter() - t
    t = time.perf_counter()
    for _ in range(5):
        cv2.resize(img, size, interpolation=cv2.INTER_AREA)
    assert fast < time.perf_counter() - t
