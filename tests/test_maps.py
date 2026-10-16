"""Saved maps: one per route, reopened later, checked against what the cameras see now."""
import io
import json
import zipfile

import cv2
import numpy as np
import pytest

from marbletrack_app import maps
from marbletrack_app.app import App, UserError
from marbletrack_app.cameras import demo_world


@pytest.fixture(scope="module")
def views():
    w = demo_world()
    empty = lambda cid: w.render(w.camera(cid), 0.0, "empty")
    return w, empty("cam03"), empty("cam09"), w.render(w.camera("cam03"), 0.0, "parked")


def _nudge(img, dx=7.0, dy=-4.0, deg=0.8):
    h, w = img.shape[:2]
    R = cv2.getRotationMatrix2D((w / 2, h / 2), deg, 1.0)
    H = np.vstack([R, [0, 0, 1]])
    H[0, 2] += dx
    H[1, 2] += dy
    return cv2.warpPerspective(img, H, (w, h), borderMode=cv2.BORDER_REPLICATE), H


def test_view_check_same_moved_changed(views):
    w, saved, other, with_marbles = views
    pts, _ = w.waypoints("cam03", 0.2, jitter_px=0.0)
    r = maps.view_change(saved, with_marbles, pts)  # marbles on the track do not matter
    assert r["status"] == "same", r
    moved, H_true = _nudge(with_marbles)
    r = maps.view_change(saved, moved, pts)
    assert r["status"] == "moved", r
    got = np.array(maps.warp_points(r["H"], pts))
    want = cv2.perspectiveTransform(np.float32(pts).reshape(-1, 1, 2), H_true).reshape(-1, 2)
    assert np.max(np.linalg.norm(got - want, axis=1)) < 1.5  # waypoints land where they belong in the new view
    assert maps.view_change(saved, other, pts)["status"] == "changed"
    assert maps.view_change(saved, cv2.resize(saved, (320, 240)), pts)["status"] == "size"


def test_view_check_tolerates_a_change_of_light(views):
    w, saved, _, _ = views
    dim = cv2.convertScaleAbs(saved, alpha=0.75, beta=0)
    assert maps.view_change(saved, dim, w.waypoints("cam03", 0.2, jitter_px=0.0)[0])["status"] == "same"


def test_export_import_roundtrip_and_rejects_foreign_zips(tmp_path):
    app = App(home=tmp_path)
    app.update_race(name="Alpine route", notes="draw 12", track_length=20.0, finish_s=19.5)
    app.save()
    path = app.export_map()["path"]
    with zipfile.ZipFile(path) as z:
        assert "project.json" in z.namelist()
    data = open(path, "rb").read()
    st = app.import_map(data)
    assert st["project"]["name"] == "Alpine route" and st["project"]["notes"] == "draw 12"
    assert len(app.list_maps()["maps"]) == 2  # imported next to the original, never over it

    def zipped(files):
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            for n, d in files.items():
                z.writestr(n, d)
        return b.getvalue()

    for bad in (b"not a zip", zipped({"readme.txt": "x"}),
                zipped({"project.json": json.dumps({"name": "x"}), "../evil.png": "x"})):
        with pytest.raises(UserError):
            app.import_map(bad)


def test_same_name_never_overwrites_another_map(tmp_path):
    app = App(home=tmp_path)
    app.update_race(name="Route A", track_length=5.0)
    app.save()
    first = app.folder
    app.new_map("Route A")  # same name, different route
    assert app.folder != first
    assert sorted(m["name"] for m in app.list_maps()["maps"]) == ["Route A", "Route A"]
    assert json.load(open(first / "project.json"))["track_length"] == 5.0


@pytest.mark.slow
def test_reopen_map_check_views_and_carry_waypoints_over(tmp_path):
    app = App(home=tmp_path, demo=True)
    try:
        app.discover_cameras()
        keys = [c.key for c in app.project.cameras][:3]
        for c in app.project.cameras:
            app.update_camera(c.key, enabled=c.key in keys)
        app.update_race(name="Route 1")
        for k in keys:
            app.autofill_markers(k)
        app.capture_background()
        app.save()
        route1 = str(app.folder)

        # a new route keeps marbles and cameras, not the calibration
        app.new_map("Route 2")
        assert [m.id for m in app.project.marbles] == [m.id for m in maps_project(route1).marbles]
        assert all(not c.markers and not c.palette for c in app.project.cameras)
        assert not app.backgrounds

        # a year later: reopen route 1; one camera was nudged meanwhile
        app.open_map(route1)
        assert app.project.name == "Route 1" and set(app.backgrounds) == set(keys)
        nudged = keys[1]
        old_markers = [list(p) for p in app.project.camera(nudged).markers]
        # the live view stays put; moving the saved image the other way is the same as moving the camera
        moved_bg, H = _nudge(app.backgrounds[nudged])
        app.backgrounds[nudged] = cv2.warpPerspective(app.backgrounds[nudged], np.linalg.inv(H),
                                                      app.backgrounds[nudged].shape[1::-1], borderMode=cv2.BORDER_REPLICATE)
        app.project.camera(nudged).markers = maps.warp_points(np.linalg.inv(H), old_markers)
        st = app.check_views()
        status = {c["key"]: c["view"]["status"] for c in st["cameras"] if c["view"]}
        assert status[nudged] == "moved" and all(status[k] == "same" for k in keys if k != nudged), status
        assert any("moved" in x for x in st["steps"]["waypoints"])
        st = app.realign_cameras()
        back = np.array(app.project.camera(nudged).markers)
        assert np.max(np.linalg.norm(back - np.array(old_markers), axis=1)) < 2.0
        assert any("again" in x for x in st["steps"]["background"])  # recapture asked for
        app.capture_background([nudged])
        assert not app.step_status()["background"] and not app.step_status()["waypoints"]
    finally:
        app.shutdown()


def maps_project(folder):
    from pathlib import Path

    from marbletrack_app.config import load_project

    return load_project(Path(folder))
