"""Application controller: project state, wizard actions, live mode.

Every public method is an action the UI can call (``POST /api/<method>``); each
returns plain JSON-able data. Errors meant for the operator are raised as
``UserError`` and shown verbatim.
"""
from __future__ import annotations

import json
import logging
import os
import pickle
import tempfile
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from . import __version__
from . import calibration as C
from . import maps, vmix
from .cameras import CameraInfo, DemoSource, LiveCamera, demo_world, discover, make_source
from .config import CameraSetup, MarbleDef, Project, app_dir, load_project, save_project, slug
from .engine import Runtime
from .ws_client import LeaderboardStreamer, check_url, test_connection

log = logging.getLogger("marbletrack.app")

STEPS = ["race", "cameras", "waypoints", "background", "colours", "server", "review"]


class UserError(Exception):
    pass


_PLACEHOLDER: bytes | None = None


def _placeholder() -> bytes:
    global _PLACEHOLDER
    if _PLACEHOLDER is None:
        _PLACEHOLDER = cv2.imencode(".jpg", np.full((120, 160, 3), (26, 19, 14), np.uint8))[1].tobytes()
    return _PLACEHOLDER


class App:
    def __init__(self, home: Path | None = None, demo: bool = False):
        self.home = home or app_dir()
        self.home.mkdir(parents=True, exist_ok=True)
        self.demo = demo
        self.lock = threading.RLock()
        self.discovered: list[CameraInfo] = []
        self.discovering = False
        self.hub: dict[str, LiveCamera] = {}
        self.backgrounds: dict[str, np.ndarray] = {}
        self._bg_dirty: set = set()
        self.samples: dict = {}  # (cam key, marble id) -> {"pixels": [...], "medians": [...]}
        self.runtime: Runtime | None = None
        self.streamer: LeaderboardStreamer | None = None
        self.vmix: vmix.VmixClient | None = None
        self.view_check: dict = {}  # camera key -> result of comparing its live view with the saved map
        self.mode = "setup"
        self.step = "race"
        self.demo_scene = "race"
        self.toasts: list = []
        self._temp_files: list[str] = []
        self.folder = self._last_folder()
        if self.folder and (self.folder / "project.json").exists():
            try:
                self.project = load_project(self.folder)
                self._load_backgrounds()
            except Exception as e:
                log.error("could not load project: %s", e)
                self.project = Project.new()
        else:
            self.project = Project.new()
            self.folder = None
        if self.project.cameras:  # a saved project: find its cameras in the background, so they show as connected
            threading.Thread(target=self._startup_discovery, daemon=True).start()

    def _startup_discovery(self):
        try:
            self.discover_cameras()
        except Exception as e:
            log.warning("startup camera discovery failed: %s", e)

    # ------------------------------------------------------------------ persistence
    def _settings_path(self) -> Path:
        return self.home / "settings.json"

    def _last_folder(self) -> Path | None:
        try:
            with open(self._settings_path(), encoding="utf-8") as f:
                p = json.load(f).get("last_project")
            return Path(p) if p else None
        except Exception:
            return None

    def _project_folder(self) -> Path:
        if self.folder is None:  # never reuse another map's folder, even if the names match
            self.folder = maps.unique_folder(self.home, self.project.name)
        return self.folder

    def save(self) -> dict:
        with self.lock:
            folder = self._project_folder()
            folder.mkdir(parents=True, exist_ok=True)
            for key, bg in self.backgrounds.items():
                name = f"background-{slug(key.replace(':', '-'))}.png"
                if key in self._bg_dirty or not (folder / name).exists():
                    if not cv2.imwrite(str(folder / name), bg):
                        raise UserError(f"could not write {folder / name}")
                    self._bg_dirty.discard(key)
                try:
                    self.project.camera(key).background_file = name
                except KeyError:
                    pass
            self.project.saved_at = maps.now_iso()
            save_project(self.project, folder)
            with open(self._settings_path(), "w", encoding="utf-8") as f:
                json.dump({"last_project": str(folder)}, f)
            return {"saved": str(folder)}

    def _load_backgrounds(self):
        for cam in self.project.cameras:
            if cam.background_file:
                img = cv2.imread(str(self.folder / cam.background_file))
                if img is not None:
                    self.backgrounds[cam.key] = img

    def new_project(self) -> dict:
        with self.lock:
            self._close_hub()
            self.project, self.folder = Project.new(), None
            self.backgrounds.clear()
            self.samples.clear()
            self.view_check.clear()
            self.step = "race"
        return self.state()

    # ------------------------------------------------------------------ maps (one saved project per route)
    def _map_folder(self, folder: str) -> Path:
        f = Path(folder).resolve()
        root = maps.projects_dir(self.home).resolve()
        if root not in f.parents or not (f / "project.json").exists():
            raise UserError("that map no longer exists")
        return f

    def _require_setup(self):
        if self.mode != "setup":
            raise UserError("stop tracking first (Back to setup)")

    def list_maps(self) -> dict:
        return {"maps": maps.list_maps(self.home), "current": str(self.folder) if self.folder else None}

    def _switch_to(self, folder: Path | None, project: Project):
        self._close_hub()
        self.project, self.folder = project, folder
        self.backgrounds.clear()
        self._bg_dirty.clear()
        self.samples.clear()
        self.view_check.clear()
        if folder is not None:
            self._load_backgrounds()
            with open(self._settings_path(), "w", encoding="utf-8") as f:
                json.dump({"last_project": str(folder)}, f)

    def open_map(self, folder: str) -> dict:
        """Open a saved map. Run ``check_views`` next: the cameras may have moved since it was saved."""
        self._require_setup()
        f = self._map_folder(folder)
        try:
            project = load_project(f)
        except Exception as e:
            raise UserError(f"could not open that map: {e}")
        with self.lock:
            if self.folder is not None and self.folder != f:
                self.save()
            self._switch_to(f, project)
            self.step = "review"
        return self.state()

    def new_map(self, name: str, from_folder: str | None = None) -> dict:
        """A new route. ``from_folder``: start from a copy of that map (everything kept; check the views).
        Otherwise: same marbles, cameras and output settings, but no waypoints, backgrounds or colours."""
        self._require_setup()
        name = str(name).strip()[:80] or "New route"
        with self.lock:
            if self.folder is not None:
                self.save()
            if from_folder:
                dst = maps.copy_map(self._map_folder(from_folder), self.home, name)
                project = load_project(dst)
                project.name, project.notes = name, ""
                self._switch_to(dst, project)
                self.step = "review"
            else:
                old = self.project
                project = Project(name=name, marbles=old.marbles, server=old.server, vmix=old.vmix,
                                  marker_spacing=old.marker_spacing, track_width_m=old.track_width_m,
                                  marble_diameter_mm=old.marble_diameter_mm, auto_new_race=old.auto_new_race)
                for c in old.cameras:
                    project.cameras.append(CameraSetup(key=c.key, backend=c.backend, device=c.device, name=c.name,
                                                       enabled=c.enabled, role=c.role, width=c.width, height=c.height,
                                                       fps=c.fps, auto_exposure=c.auto_exposure, exposure=c.exposure,
                                                       auto_white_balance=c.auto_white_balance, latency_ms=c.latency_ms))
                self._switch_to(None, project)
                self.step = "race"
            self.save()
        return self.state()

    def export_map(self, folder: str | None = None) -> dict:
        with self.lock:
            if folder is None:
                self.save()
                f = self._project_folder()
            else:
                f = self._map_folder(folder)
            path = maps.export_zip(f, self.home / "exports")
        return {"path": str(path)}

    def import_map(self, data: bytes) -> dict:
        self._require_setup()
        try:
            folder = maps.import_zip(data, self.home)
        except ValueError as e:
            raise UserError(str(e))
        return self.open_map(str(folder))

    def check_views(self) -> dict:
        """Compare every camera's live picture with the empty-track image saved in this map."""
        self._require_setup()
        cams = [c for c in self.project.active_cameras if c.key in self.backgrounds]
        results: dict = {}

        def one(cam):
            try:
                frame = self.frame(cam.key)
                r = maps.view_change(self.backgrounds[cam.key], frame, cam.markers or None)
            except Exception as e:
                r = {"status": "error", "detail": str(e), "shift_px": None, "H": None}
            r["checked_at"] = time.time()
            results[cam.key] = r

        threads = [threading.Thread(target=one, args=(c,)) for c in cams]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        with self.lock:
            self.view_check.update(results)
        moved = [self._cam(k).name for k, r in results.items() if r["status"] == "moved"]
        changed = [self._cam(k).name for k, r in results.items() if r["status"] in ("changed", "size")]
        if not moved and not changed and results:
            self.toast("ok", f"All {len(results)} camera views match this map.")
        return self.state()

    def realign_cameras(self, keys: list | None = None) -> dict:
        """Carry the waypoints of moved cameras over to their new view."""
        with self.lock:
            done = []
            for key, r in list(self.view_check.items()):
                if r.get("status") != "moved" or (keys is not None and key not in keys):
                    continue
                cam = self._cam(key)
                H = np.asarray(r["H"], dtype=np.float64)
                cam.markers = maps.warp_points(H, cam.markers) if cam.markers else []
                bg = self.backgrounds.get(key)
                if bg is not None:  # stand-in until the empty track is captured again
                    self.backgrounds[key] = cv2.warpPerspective(bg, H, (bg.shape[1], bg.shape[0]), borderMode=cv2.BORDER_REPLICATE)
                    self._bg_dirty.add(key)
                self.view_check[key] = {"status": "realigned", "detail": "waypoints carried over; capture the empty track again",
                                        "shift_px": r.get("shift_px"), "H": None}
                done.append(cam.name)
        if done:
            self.toast("ok", f"Waypoints carried over for {', '.join(done)}. Check them in step 3, then capture the empty track again (step 4).")
        return self.state()

    # ------------------------------------------------------------------ state for the UI
    def state(self) -> dict:
        with self.lock:
            p = self.project
            cams = []
            for c in p.cameras:
                live = self.hub.get(c.key)
                d = {k: v for k, v in c.__dict__.items() if k != "palette"}
                d["palette"] = {mid: {"clicks": s.clicks, "pixels": s.pixels, "copied_from": s.copied_from}
                                for mid, s in c.palette.items()}
                d["connected"] = any(i.key == c.key for i in self.discovered) or c.backend == "demo" and self.demo
                d["preview"] = {"fps": round(live.fps, 1) if live else 0.0, "error": live.error if live else ""}
                d["has_background"] = c.key in self.backgrounds
                d["palette_issues"] = C.palette_issues(c, p) if c.palette else []
                d["problems"] = C.camera_problems(c, p, c.key in self.backgrounds) if c.enabled else []
                d["s_range"] = c.s_range
                d["stretches"] = c.stretches
                d["s_ranges"] = c.s_ranges
                v = self.view_check.get(c.key)
                d["view"] = None if v is None else {k: v.get(k) for k in ("status", "detail", "shift_px")}
                if len(c.markers) >= 2:
                    d["band"] = C.band_halfwidth_px(c, p).round(1).tolist()
                    ppm = C.px_per_metre(c)
                    d["mm_per_px"] = [round(1000 / float(ppm.max()), 1), round(1000 / float(ppm.min()), 1)]
                cams.append(d)
            return {
                "version": __version__,
                "mode": self.mode,
                "step": self.step,
                "steps": self.step_status(),
                "demo": self.demo,
                "demo_scene": self.demo_scene,
                "discovering": self.discovering,
                "project": {k: v for k, v in p.to_dict().items() if k != "cameras"},
                "cameras": cams,
                "coverage": p.coverage(),
                "folder": str(self.folder) if self.folder else None,
                "maps_dir": str(maps.projects_dir(self.home)),
                "live": self.live_status() if self.mode == "live" else None,
            }

    def step_status(self) -> dict:
        p = self.project
        act = p.active_cameras
        out = {}
        race = []
        if p.track_length <= 0:
            race.append("Enter the track length.")
        if p.laps < 1:
            race.append("The race needs at least one lap.")
        if not (0 < p.finish_s <= p.track_length):
            race.append("The finish line must be between the start and the end of the track.")
        if len(p.marbles) < 2:
            race.append("Add at least two marbles.")
        names = [m.name.strip().lower() for m in p.marbles]
        if any(not n for n in names):
            race.append("Every marble needs a name.")
        dupes = sorted({m.name for m in p.marbles if names.count(m.name.strip().lower()) > 1})
        if dupes:
            race.append(f"Two marbles are called {dupes[0]}: give each its own name." if len(dupes) == 1
                        else f"Names used twice: {', '.join(dupes)}.")
        out["race"] = race
        cams = []
        if not act:
            cams.append("Turn on at least one camera.")
        for c in act:
            if not (any(i.key == c.key for i in self.discovered) or (c.backend == "demo" and self.demo)):
                cams.append(f"{c.name} is not connected.")
        out["cameras"] = cams
        vc = self.view_check
        out["waypoints"] = [f"{c.name}: place at least two waypoints." for c in act if len(c.markers) < 2]
        out["waypoints"] += [f"{c.name} looks at a different view than in this map: redo its waypoints." for c in act
                             if vc.get(c.key, {}).get("status") in ("changed", "size") and len(c.markers) >= 2]
        out["waypoints"] += [f"{c.name} has moved since this map was saved: carry its waypoints over (Review)." for c in act
                             if vc.get(c.key, {}).get("status") == "moved"]
        out["background"] = [f"{c.name}: capture the empty track." for c in act if c.key not in self.backgrounds]
        out["background"] += [f"{c.name}: capture the empty track again (the camera's view changed)." for c in act
                              if c.key in self.backgrounds and vc.get(c.key, {}).get("status") in ("realigned", "redo")]
        colours = []
        for c in act:
            missing = [m.name for m in p.marbles if m.id not in c.palette]
            if missing:
                colours.append(f"{c.name}: {len(missing)} marble{'s' if len(missing) != 1 else ''} without a colour sample.")
        out["colours"] = colours
        srv = p.server
        out["server"] = [] if not srv.enabled else ([e] if (e := check_url(srv.url)) else [])
        out["review"] = [x for k in STEPS[:-1] for x in out[k]]
        return out

    def set_step(self, step: str) -> dict:
        if step not in STEPS:
            raise UserError("unknown step")
        with self.lock:
            self.step = step
            if self.demo:
                self._set_demo_scene({"background": "empty", "colours": "parked", "waypoints": "parked"}.get(step, "race"))
        return {"step": step}

    def _set_demo_scene(self, scene: str):
        self.demo_scene = scene
        DemoSource.scene = scene

    def set_demo_scene(self, scene: str) -> dict:
        if scene not in ("empty", "parked", "race"):
            raise UserError("unknown scene")
        self._set_demo_scene(scene)
        return {"scene": scene}

    # ------------------------------------------------------------------ step 1: race
    def update_race(self, **fields) -> dict:
        allowed = {"name", "notes", "laps", "track_length", "finish_s", "start_s", "marker_spacing", "track_width_m",
                   "marble_diameter_mm", "auto_new_race", "blind_zones", "wide_zones", "rack_auto_confirm"}
        with self.lock:
            for k, v in fields.items():
                if k not in allowed:
                    raise UserError(f"unknown field {k}")
                if k in ("track_length", "finish_s", "start_s", "marker_spacing", "track_width_m", "marble_diameter_mm"):
                    v = float(v)
                    if v < 0 or not np.isfinite(v):
                        raise UserError("values must be positive numbers")
                if k == "name":
                    v = str(v).strip()[:80] or "My marble race"
                if k == "notes":
                    v = str(v)[:2000]
                if k == "laps":
                    v = int(np.clip(int(v), 1, 99))
                if k == "blind_zones":
                    v = [[float(a), float(b)] for a, b in v if float(b) > float(a)]
                if k == "wide_zones":
                    v = [[float(a), float(b), float(w)] for a, b, w in v
                         if float(b) > float(a) and 0 < float(w) < 2 and np.isfinite(float(w))]
                setattr(self.project, k, v)
        return self.state()

    def set_marbles(self, marbles: list) -> dict:
        with self.lock:
            out, seen = [], set()
            for m in marbles:
                name = str(m.get("name", "")).strip()[:40]
                mid = str(m.get("id") or slug(name).lower() or f"m{len(out) + 1}")
                while mid in seen:
                    mid += "-2"
                seen.add(mid)
                color = str(m.get("color", "#888888"))
                if not (len(color) == 7 and color.startswith("#")):
                    color = "#888888"
                out.append(MarbleDef(mid, name, color))
            if len(out) > 64:
                raise UserError("at most 64 marbles")
            removed = {m.id for m in self.project.marbles} - {m.id for m in out}
            for c in self.project.cameras:
                for mid in removed:
                    c.palette.pop(mid, None)
            self.project.marbles = out
        return self.state()

    # ------------------------------------------------------------------ step 2: cameras
    def discover_cameras(self) -> dict:
        with self.lock:
            if self.discovering:
                return self.state()
            self.discovering = True
            self._close_hub()  # discovery opens devices; our own previews would block it
        try:
            found = discover(include_demo=self.demo)
        finally:
            self.discovering = False
        with self.lock:
            self.discovered = found
            known = {c.key for c in self.project.cameras}
            for info in found:
                if info.key not in known:
                    self.project.cameras.append(CameraSetup(
                        key=info.key, backend=info.backend, device=info.device, name=info.name,
                        width=info.width, height=info.height, fps=info.fps or 30.0,
                        role="finish" if info.device == "finish" else "section",
                        auto_exposure=info.backend == "demo"))
            if self.demo and found and all(not c.markers for c in self.project.cameras) and self.project.track_length <= 0:
                w = demo_world()
                self.project.track_length = round(float(w.track.s_range[1]), 3)
                self.project.finish_s = round(float(w.s_finish), 3)
                self.project.blind_zones = [list(map(float, z)) for z in w.cfg.tunnels]
                self.project.marbles = [MarbleDef(m.marble_id.lower(), m.marble_id, "#%02x%02x%02x" % m.bgr[::-1]) for m in w.marbles]
        return self.state()

    def update_camera(self, key: str, **fields) -> dict:
        allowed = {"name", "enabled", "role", "auto_exposure", "exposure", "auto_white_balance", "latency_ms",
                   "readout_ms", "width_scale", "fps"}
        with self.lock:
            cam = self._cam(key)
            for k, v in fields.items():
                if k not in allowed:
                    raise UserError(f"unknown field {k}")
                if k == "name":
                    v = str(v).strip()[:60] or cam.name
                if k == "role" and v not in ("section", "finish", "rack"):
                    raise UserError("role must be section, finish or rack")
                if k == "width_scale":
                    v = float(np.clip(float(v), 0.4, 3.0))
                if k == "readout_ms":
                    v = float(np.clip(float(v or 0), 0.0, 60.0))
                setattr(cam, k, v)
            if {"auto_exposure", "exposure", "auto_white_balance"} & fields.keys() and key in self.hub:
                src = self.hub[key].source
                applied = src.apply(cam.auto_exposure, cam.exposure, cam.auto_white_balance)
                if not all(applied.values()):
                    self.toast("warn", f"{cam.name}: the driver ignored some settings ({', '.join(k for k, v in applied.items() if not v)}).")
        return self.state()

    def move_camera(self, key: str, delta: int) -> dict:
        with self.lock:
            cams = self.project.cameras
            i = next(i for i, c in enumerate(cams) if c.key == key)
            j = int(np.clip(i + int(delta), 0, len(cams) - 1))
            cams.insert(j, cams.pop(i))
        return self.state()

    def forget_camera(self, key: str) -> dict:
        with self.lock:
            self._stop_cam(key)
            self.project.cameras = [c for c in self.project.cameras if c.key != key]
            self.backgrounds.pop(key, None)
        return self.state()

    def _cam(self, key: str) -> CameraSetup:
        try:
            return self.project.camera(key)
        except KeyError:
            raise UserError("that camera is no longer in the project")

    # ------------------------------------------------------------------ live previews (setup)
    def live_camera(self, key: str) -> LiveCamera:
        with self.lock:
            if self.mode != "setup":
                raise UserError("previews come from the tracker while live")
            lc = self.hub.get(key)
            if lc is None or (lc.error and not lc.thread.is_alive()):
                cam = self._cam(key)
                src = make_source(cam.backend, cam.device, cam.width, cam.height, cam.fps)
                lc = LiveCamera(key, src).start()
                if not lc.error:
                    try:
                        lc.source.apply(cam.auto_exposure, cam.exposure, cam.auto_white_balance)
                        w, h = lc.source.size
                        if w and h and (cam.width, cam.height) != (w, h):
                            if cam.markers or key in self.backgrounds:
                                self.toast("warn", f"{cam.name} now runs at {w}×{h} instead of {cam.width}×{cam.height}: redo its waypoints and background.")
                            cam.width, cam.height = w, h
                    except Exception as e:
                        log.warning("camera settings: %s", e)
                self.hub[key] = lc
            return lc

    def _stop_cam(self, key):
        lc = self.hub.pop(key, None)
        if lc:
            lc.stop()

    def _close_hub(self):
        cams = list(self.hub.values())
        self.hub.clear()
        for lc in cams:  # signal all first, then wait: closing 15 cameras one by one is slow
            lc._stop.set()
        for lc in cams:
            lc.stop()

    def frame(self, key: str) -> np.ndarray:
        lc = self.live_camera(key)
        frame, _ = lc.wait(lc.seq - 1 if lc.frame is not None else 0, timeout=3.0)
        if frame is None:
            raise UserError(lc.error or "the camera has not delivered an image yet")
        return frame

    # ------------------------------------------------------------------ step 3: waypoints
    def add_marker(self, key: str, x: float, y: float) -> dict:
        with self.lock:
            cam = self._cam(key)
            if cam.width and not (0 <= x <= cam.width and 0 <= y <= cam.height):
                raise UserError("click inside the image")
            if cam.markers:
                last = cam.markers[-1]
                if np.hypot(x - last[0], y - last[1]) < 3:
                    raise UserError("that is on top of the previous waypoint")
            cam.markers.append([round(float(x), 2), round(float(y), 2)])
            self._renumber(cam)
        return self.state()

    def _renumber(self, cam: CameraSetup, start: float | None = None):
        """Distances: first waypoint at ``start`` (kept if already set), then one spacing per waypoint."""
        if cam.role == "rack":  # slots, one marble apart: the "distances" only set the marble size in pixels
            pitch = self.project.marble_diameter_mm / 1000.0
            cam.marker_s = [round(i * pitch, 4) for i in range(len(cam.markers))]
            return
        sp = self.project.marker_spacing
        if start is None:
            start = cam.marker_s[0] if cam.marker_s else self._suggest_start(cam)
        old = cam.marker_s
        new = [round(start + i * sp, 4) for i in range(len(cam.markers))]
        # keep distances the operator typed in, as long as they still increase
        for i in range(1, min(len(old), len(new))):
            if old[i] > new[i - 1]:
                new[i] = old[i]
        for i in range(1, len(new)):
            if new[i] <= new[i - 1]:
                new[i] = round(new[i - 1] + sp, 4)
        cam.marker_s = new

    def _suggest_start(self, cam: CameraSetup) -> float:
        """Continue where the previous camera's waypoints end (sections overlap a little)."""
        prev = None
        for c in self.project.cameras:
            if c.key == cam.key:
                break
            if c.enabled and c.role == "section" and c.s_range:
                prev = c
        if cam.role == "finish":
            return round(max(0.0, self.project.finish_s - 2 * self.project.marker_spacing), 4)
        if prev is None:
            return 0.0
        sp = self.project.marker_spacing
        return round(max(0.0, prev.s_range[1] - sp), 4)

    def undo_marker(self, key: str) -> dict:
        with self.lock:
            cam = self._cam(key)
            if cam.breaks and cam.breaks[-1] >= len(cam.markers):
                cam.breaks.pop()  # undo a "new stretch" that has no waypoint yet
            elif cam.markers:
                cam.markers.pop()
                cam.marker_s = cam.marker_s[:len(cam.markers)]
                cam.breaks = [b for b in cam.breaks if b < len(cam.markers)]
        return self.state()

    def new_stretch(self, key: str) -> dict:
        """The next waypoint starts another stretch of track this camera also sees
        (the other leg of a hairpin, a lower pass under a bridge). Set its distance."""
        with self.lock:
            cam = self._cam(key)
            if len(cam.markers) < 2:
                raise UserError("place at least two waypoints on the first stretch first")
            if cam.markers and (not cam.breaks or cam.breaks[-1] < len(cam.markers)):
                if cam.breaks and len(cam.markers) - cam.breaks[-1] < 2:
                    raise UserError("the current stretch needs at least two waypoints")
                cam.breaks.append(len(cam.markers))
        return self.state()

    def clear_markers(self, key: str) -> dict:
        with self.lock:
            cam = self._cam(key)
            cam.markers, cam.marker_s, cam.breaks = [], [], []
            if self.view_check.get(cam.key, {}).get("status") in ("changed", "size", "moved"):
                self.view_check[cam.key] = {"status": "redo", "detail": "redo its waypoints, then capture the empty track", "shift_px": None, "H": None}
        return self.state()

    def set_marker_distance(self, key: str, index: int, s: float) -> dict:
        with self.lock:
            cam = self._cam(key)
            s = float(s)
            if not (0 <= index < len(cam.markers)):
                raise UserError("no such waypoint")
            if index == 0:
                shift = s - cam.marker_s[0]
                cam.marker_s = [round(v + shift, 4) for v in cam.marker_s]
            else:
                if s <= cam.marker_s[index - 1]:
                    raise UserError(f"must be more than {cam.marker_s[index - 1]:g} m (the previous waypoint)")
                if index + 1 < len(cam.marker_s) and s >= cam.marker_s[index + 1]:
                    raise UserError(f"must be less than {cam.marker_s[index + 1]:g} m (the next waypoint)")
                cam.marker_s[index] = round(s, 4)
        return self.state()

    def move_marker(self, key: str, index: int, x: float, y: float) -> dict:
        with self.lock:
            cam = self._cam(key)
            if not (0 <= index < len(cam.markers)):
                raise UserError("no such waypoint")
            cam.markers[index] = [round(float(x), 2), round(float(y), 2)]
        return self.state()

    def autofill_markers(self, key: str) -> dict:
        """Demo cameras only: place the waypoints an installer would click (with 1 px of hand jitter)."""
        with self.lock:
            cam = self._cam(key)
            if cam.backend != "demo":
                raise UserError("automatic waypoints exist only for demo cameras")
            uv, s = demo_world().waypoints(cam.device, self.project.marker_spacing if cam.role == "section" else 0.05,
                                           jitter_px=1.0, seed=sum(map(ord, key)))  # deterministic on every OS
            cam.markers = [[round(a, 2), round(b, 2)] for a, b in uv]
            cam.marker_s = s
        return self.state()

    # ------------------------------------------------------------------ step 4: background
    def capture_background(self, keys: list | None = None, frames: int = 12) -> dict:
        with self.lock:
            targets = [c for c in self.project.active_cameras if keys is None or c.key in keys]
            if self.demo:
                self._set_demo_scene("empty")
        results, threads = {}, []

        def grab(cam):
            try:
                lc = self.live_camera(cam.key)
                imgs, seq = [], lc.seq
                deadline = time.time() + 6
                while len(imgs) < frames and time.time() < deadline:
                    f, seq = lc.wait(seq, timeout=1.0)
                    if f is not None:
                        imgs.append(f.copy())
                if len(imgs) < 3:
                    raise UserError(lc.error or "too few frames from the camera")
                mask = C.track_mask(cam, self.project, (imgs[0].shape[1], imgs[0].shape[0])) if len(cam.markers) >= 2 else None
                chk = C.background_check(imgs, mask)
                bg = C.median_background(imgs)
                with self.lock:
                    self.backgrounds[cam.key] = bg
                    self._bg_dirty.add(cam.key)
                    if self.view_check.get(cam.key, {}).get("status") in ("realigned", "same"):
                        self.view_check.pop(cam.key)
                    cam.width, cam.height = bg.shape[1], bg.shape[0]
                results[cam.key] = {"ok": True, "static": chk["ok"], "moving": round(chk["moving_fraction"] * 100, 2)}
            except Exception as e:
                results[cam.key] = {"ok": False, "error": str(e)}

        for cam in targets:
            t = threading.Thread(target=grab, args=(cam,))
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        bad = [self._cam(k).name for k, r in results.items() if r["ok"] and not r["static"]]
        if bad:
            self.toast("warn", f"Something moved while capturing {', '.join(bad)}. Capture again once the track is still.")
        return {"results": results, **self.state()}

    def snapshot(self, key: str, width: int = 320) -> bytes:
        """Latest image of a camera as JPEG (thumbnails poll this; one stream per tile would exhaust the
        browser's six connections per host)."""
        if self.mode == "live" and self.runtime is not None:
            item = self.runtime.previews.get(key)
            return item[0] if item is not None else _placeholder()
        lc = self.live_camera(key)
        if lc.frame is None:
            lc.wait(0, timeout=3.0)
        jpg, _ = lc.jpeg(width, 70)
        if jpg is None:
            raise UserError(lc.error or "no image yet")
        return jpg

    def background_jpeg(self, key: str) -> bytes:
        bg = self.backgrounds.get(key)
        if bg is None:
            raise UserError("no background yet")
        return cv2.imencode(".jpg", bg, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()

    # ------------------------------------------------------------------ step 5: colours
    def sample_colour(self, key: str, marble_id: str, x: float, y: float) -> dict:
        return {**self._sample(key, marble_id, x, y), **self.state()}

    def _sample(self, key: str, marble_id: str, x: float, y: float) -> dict:
        with self.lock:
            cam = self._cam(key)
            if not any(m.id == marble_id for m in self.project.marbles):
                raise UserError("pick a marble first")
            if len(cam.markers) < 2:
                raise UserError("place this camera's waypoints first (step 3)")
            bg = self.backgrounds.get(key)
        frame = self.frame(key)
        r_exp = float(np.median(C.marble_radius_px(cam, self.project)))  # perspective model before the measured scale
        mask = C.track_mask(cam, self.project, (frame.shape[1], frame.shape[0]))
        try:
            seg = C.segment_click(frame, bg, (x, y), r_exp * max(cam.radius_scale, 0.5), mask)
        except C.CalibrationError as e:
            raise UserError(str(e))
        from marbletrack.color import bgr_to_lab

        with self.lock:
            store = self.samples.setdefault((key, marble_id), {"pixels": [], "medians": [], "radii": []})
            store["pixels"].append(seg["pixels"])
            store["medians"].append(np.median(bgr_to_lab(seg["pixels"]), axis=0))
            store["radii"].append(seg["radius"] / max(r_exp, 1e-6))
            keep = C.consistent_clicks(store["medians"])
            meds = [m for m, k in zip(store["medians"], keep) if k]
            pix = np.concatenate([p for p, k in zip(store["pixels"], keep) if k])[-6000:]
            try:
                cam.palette[marble_id] = C.fit_sample(pix, len(meds), meds)
            except ValueError as e:
                raise UserError(f"not enough pixels to learn this colour ({e}); move closer or click a bigger marble")
            radii = [r for (k, _), st in self.samples.items() if k == key for r in st["radii"]]
            cam.radius_scale = float(np.median(radii))
            cam.marble_radius_px = round(float(np.median(C.marble_radius_px(cam, self.project))), 1)
            issues = [i for i in C.palette_issues(cam, self.project) if marble_id in (i["a"], i["b"])]
        name = lambda mid: next((m.name for m in self.project.marbles if m.id == mid), mid)
        return {"centre": seg["centre"], "radius": seg["radius"], "pixels": int(len(seg["pixels"])),
                "ignored_clicks": int(len(keep) - sum(keep)),
                "issues": [{"other": name(i["b"] if i["a"] == marble_id else i["a"]), "d2": i["d2"]} for i in issues]}

    def clear_colour(self, key: str, marble_id: str) -> dict:
        with self.lock:
            self._cam(key).palette.pop(marble_id, None)
            self.samples.pop((key, marble_id), None)
        return self.state()

    def copy_colours(self, src: str, dst: str) -> dict:
        with self.lock:
            n = C.copy_palette(self._cam(src), self._cam(dst), only_missing=True)
            self._cam(dst).radius_scale = self._cam(src).radius_scale
        self.toast("ok", f"Copied {n} colour{'s' if n != 1 else ''}. Copied colours are a fallback: sampling on this camera is more reliable.")
        return self.state()

    def open_guide(self) -> dict:
        """Open the user guide (PDF, in Persian) in the PC's PDF viewer."""
        from .server import web_root

        name = "MarbleTrack-Guide-FA.pdf"
        path = web_root() / "guide" / name  # the Windows build bundles it here
        if not path.is_file():
            path = Path(__file__).resolve().parents[1] / "docs" / name  # running from the repository
        if not path.is_file():
            raise UserError("the guide is not included in this build")
        if os.name == "nt":
            os.startfile(path)  # noqa: S606 - a local file the app ships
        else:
            import webbrowser

            webbrowser.open(path.as_uri())
        return {"ok": True, "path": str(path)}

    def demo_setup_all(self) -> dict:
        """Demo only: everything an installer does, on every demo camera, in one go (for showing the app)."""
        if not self.demo:
            raise UserError("only with demo cameras (start with --demo)")
        if not self.project.cameras:
            self.discover_cameras()
        cams = [c for c in self.project.active_cameras if c.backend == "demo"]
        for c in cams:
            self.autofill_markers(c.key)
        self.capture_background([c.key for c in cams])
        for c in cams:
            self.autofill_colours(c.key)
        with self.lock:
            if not self.project.server.url.strip():
                self.project.server.enabled = False  # nothing to stream to: do not block going live
            self.step = "review"
        self.save()
        return {**self.state(), "demo_cameras": len(cams)}

    def autofill_colours(self, key: str) -> dict:
        """Demo cameras only: click every parked marble, as the operator would."""
        cam = self._cam(key)
        if cam.backend != "demo":
            raise UserError("automatic colour sampling exists only for demo cameras")
        w = demo_world()
        dcam = w.camera(cam.device)
        self._set_demo_scene("parked")
        lc = self.live_camera(key)
        done, fails = set(), {}
        deadline = time.time() + 4 * w.PARK_PERIOD
        while len(done) < len(self.project.marbles) and time.time() < deadline:
            frame, _ = lc.wait(lc.seq, timeout=2.0)
            t = lc.t - lc.source.t0
            xy, vis = w.parked_positions(dcam, t)
            if w.parked_positions(dcam, t + 0.3)[1].tolist() != vis.tolist():
                time.sleep(0.4)  # the group is about to change
                continue
            uv, _ = dcam.project(np.c_[xy, np.full(len(xy), w.cfg.marble_radius)])
            for k, m in enumerate(self.project.marbles):
                if k < len(vis) and vis[k] and m.id not in done and fails.get(m.id, 0) < 5:
                    try:
                        self._sample(key, m.id, float(uv[k][0]), float(uv[k][1]))
                        done.add(m.id)
                    except UserError as e:
                        fails[m.id] = fails.get(m.id, 0) + 1
                        log.info("demo autofill %s %s: %s", key, m.id, e)
            if len(done) < len(self.project.marbles):
                time.sleep(0.3)
        return self.state()

    # ------------------------------------------------------------------ step 6: server
    def update_server(self, **fields) -> dict:
        with self.lock:
            srv = self.project.server
            for k, v in fields.items():
                if k == "url":
                    srv.url = str(v).strip()
                elif k == "token":
                    srv.token = str(v).strip()
                elif k == "station_id":
                    srv.station_id = str(v).strip()[:60] or "track-1"
                elif k == "publish_hz":
                    srv.publish_hz = float(np.clip(float(v), 1, 30))
                elif k == "enabled":
                    srv.enabled = bool(v)
                else:
                    raise UserError(f"unknown field {k}")
        return self.state()

    def test_server(self) -> dict:
        srv = self.project.server
        return test_connection(srv.url, srv.token, srv.station_id)

    def update_vmix(self, **fields) -> dict:
        with self.lock:
            v = self.project.vmix
            for k, val in fields.items():
                if k == "enabled":
                    v.enabled = bool(val)
                elif k == "port":
                    v.port = int(np.clip(int(val), 1, 65535))
                elif k == "rows":
                    v.rows = int(np.clip(int(val), 0, 64))
                elif k in ("host", "input", "name_field", "info_field", "color_field", "clock_field", "status_field", "lap_field"):
                    val = str(val).strip()[:120]
                    if k.endswith("_field") and val and "{n}" not in val and k in ("name_field", "info_field", "color_field"):
                        raise UserError("Row fields need {n} where the row number goes, e.g. P{n}Name.Text")
                    setattr(v, k, val or ("127.0.0.1" if k == "host" else val))
                else:
                    raise UserError(f"unknown field {k}")
        return self.state()

    def update_record(self, **fields) -> dict:
        with self.lock:
            r = self.project.record
            for k, val in fields.items():
                if k == "enabled":
                    r.enabled = bool(val)
                elif k == "folder":
                    r.folder = str(val).strip()[:400]
                elif k == "jpeg_quality":
                    r.jpeg_quality = int(np.clip(int(val), 50, 100))
                else:
                    raise UserError(f"unknown field {k}")
        return self.state()

    def test_vmix(self) -> dict:
        return vmix.test_connection(self.project.vmix)

    # ------------------------------------------------------------------ live
    def go_live(self) -> dict:
        t_start = time.perf_counter()
        with self.lock:
            problems = self.step_status()["review"]
            if problems:
                raise UserError(problems[0])
            self.save()
            t = time.perf_counter()
            self._close_hub()
            log.info("go live: cameras released in %.1f s", time.perf_counter() - t)
            demo_pickles = {}
            if self.demo:
                self._set_demo_scene("race")
                demo_pickles = {c.key: self._write_demo_pickle(c.device) for c in self.project.active_cameras if c.backend == "demo"}
            try:
                rec = None
                if self.project.record.enabled:
                    from . import recording

                    rec = recording.start_session(recording.session_root(self.project, self._project_folder()),
                                                  self.project, self.backgrounds)
                    log.info("recording this session to %s", rec)
                rt = Runtime(self.project, self.backgrounds, on_event=self._on_event, demo_pickles=demo_pickles,
                             record_dir=rec)
            except C.CalibrationError as e:
                raise UserError(str(e))
            srv = self.project.server
            if srv.enabled:
                self.streamer = LeaderboardStreamer(srv.url, srv.token, srv.station_id, __version__,
                                                    {"project": self.project.name, "track_length": self.project.track_length, "laps": self.project.laps,
                                                     "finish_s": self.project.finish_s,
                                                     "marbles": [m.__dict__ for m in self.project.marbles],
                                                     "cameras": [{"key": c.key, "name": c.name, "role": c.role} for c in self.project.active_cameras]}).start()
            if self.project.vmix.enabled:
                self.vmix = vmix.VmixClient(self.project.vmix, laps=self.project.laps).start()
            self.runtime = rt
            self.mode = "live"
            rt.start()
            log.info("live in %.1f s", time.perf_counter() - t_start)
        return self.state()

    def _write_demo_pickle(self, cam_id: str) -> str:
        path = os.path.join(tempfile.gettempdir(), f"marbletrack-demo-{os.getpid()}-{cam_id}.pkl")
        if not os.path.exists(path):
            with open(path, "wb") as f:
                pickle.dump(demo_world().slim(cam_id), f)
            self._temp_files.append(path)
        return path

    def stop_live(self) -> dict:
        with self.lock:
            if self.runtime:
                self.runtime.stop()
            if self.streamer:
                self.streamer.stop()
            if self.vmix:
                self.vmix.stop()
            self.runtime, self.streamer, self.vmix = None, None, None
            self.mode = "setup"
            self.step = "review"
        return self.state()

    def new_race(self) -> dict:
        if not self.runtime:
            raise UserError("not live")
        self.runtime.new_race()
        return self.live_status()

    def focus_camera(self, key: str | None = None) -> dict:
        if self.runtime:
            self.runtime.set_preview(key)
        return {"focus": key}

    def _on_event(self, kind: str, data: dict):
        if kind == "board" and data.get("frozen"):
            return  # the operator holds the board on air as it is
        if kind == "board" and self.vmix is not None:
            if data.get("hidden"):
                self.vmix.publish_board([], None, "hidden")
            else:
                self.vmix.publish_board(data["board"], data["clock"], data["state"], data.get("result", "provisional"))
        if kind == "rack_check" and data.get("status") in ("differs", "unreadable"):
            self.toast("bad", f"Finish rack and board disagree: {data.get('detail')}. Check before confirming the result.")
        s = self.streamer
        if s is None:
            return
        if kind == "board":
            rt = self.runtime
            s.publish_board(data["race_id"], data["state"], data["clock"], data["board"], rt.cam_state if rt else {},
                            data.get("late", False), result=data.get("result"), hidden=data.get("hidden", False))
        elif kind in ("race_started", "finish", "race_complete", "new_race", "camera_offline", "camera_online", "camera_error",
                    "operator", "rack_check", "finish_estimated"):
            s.publish_event(kind, {k: v for k, v in data.items()})

    def live_harvest(self, label: str | None = None) -> dict:
        """Start (label = a marble id, or "empty") or stop (None) saving training crops."""
        rt = self.runtime
        if rt is None:
            raise UserError("not live")
        if label is not None and label != "empty" and label not in {m.id for m in self.project.marbles}:
            raise UserError("unknown marble")
        rt.harvest(label, self._project_folder() / "training")
        return {"harvesting": label}

    def live_operator(self, action: str, **args) -> dict:
        rt = self.runtime
        if rt is None:
            raise UserError("not live")
        try:
            return rt.operator(action, **args)
        except (ValueError, KeyError) as e:
            raise UserError(str(e))

    def live_status(self) -> dict:
        rt = self.runtime
        out = rt.status() if rt else {}
        out["server"] = self.streamer.status() if self.streamer else {"state": "off", "detail": "Streaming is turned off"}
        out["vmix"] = self.vmix.status() if self.vmix else {"state": "off", "detail": "vMix output is turned off"}
        out["toasts"] = self.pop_toasts()
        return out

    # ------------------------------------------------------------------ misc
    def toast(self, kind: str, text: str):
        with self.lock:
            self.toasts.append({"kind": kind, "text": text, "t": time.time()})
            self.toasts = self.toasts[-10:]

    def pop_toasts(self) -> list:
        with self.lock:
            out, self.toasts = self.toasts, []
            return out

    def shutdown(self):
        try:
            if self.mode == "live":
                self.stop_live()
        finally:
            self._close_hub()
            for f in self._temp_files:
                try:
                    os.remove(f)
                except OSError:
                    pass
