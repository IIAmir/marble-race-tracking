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
from . import vmix
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
        if self.folder is None:
            self.folder = self.home / "projects" / slug(self.project.name)
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
            self.step = "race"
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
                "live": self.live_status() if self.mode == "live" else None,
            }

    def step_status(self) -> dict:
        p = self.project
        act = p.active_cameras
        out = {}
        race = []
        if p.track_length <= 0:
            race.append("Enter the track length.")
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
        out["waypoints"] = [f"{c.name}: place at least two waypoints." for c in act if len(c.markers) < 2]
        out["background"] = [f"{c.name}: capture the empty track." for c in act if c.key not in self.backgrounds]
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
        allowed = {"name", "track_length", "finish_s", "start_s", "marker_spacing", "track_width_m",
                   "marble_diameter_mm", "auto_new_race", "blind_zones"}
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
                if k == "blind_zones":
                    v = [[float(a), float(b)] for a, b in v if float(b) > float(a)]
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
                   "width_scale", "fps"}
        with self.lock:
            cam = self._cam(key)
            for k, v in fields.items():
                if k not in allowed:
                    raise UserError(f"unknown field {k}")
                if k == "name":
                    v = str(v).strip()[:60] or cam.name
                if k == "role" and v not in ("section", "finish"):
                    raise UserError("role must be section or finish")
                if k == "width_scale":
                    v = float(np.clip(float(v), 0.4, 3.0))
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
            if cam.markers:
                cam.markers.pop()
                cam.marker_s = cam.marker_s[:len(cam.markers)]
        return self.state()

    def clear_markers(self, key: str) -> dict:
        with self.lock:
            cam = self._cam(key)
            cam.markers, cam.marker_s = [], []
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
            pix = np.concatenate(store["pixels"])[-6000:]
            try:
                cam.palette[marble_id] = C.fit_sample(pix, len(store["medians"]), store["medians"])
            except ValueError as e:
                raise UserError(f"not enough pixels to learn this colour ({e}); move closer or click a bigger marble")
            radii = [r for (k, _), st in self.samples.items() if k == key for r in st["radii"]]
            cam.radius_scale = float(np.median(radii))
            cam.marble_radius_px = round(float(np.median(C.marble_radius_px(cam, self.project))), 1)
            issues = [i for i in C.palette_issues(cam, self.project) if marble_id in (i["a"], i["b"])]
        name = lambda mid: next((m.name for m in self.project.marbles if m.id == mid), mid)
        return {"centre": seg["centre"], "radius": seg["radius"], "pixels": int(len(seg["pixels"])),
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
                elif k in ("host", "input", "name_field", "info_field", "color_field", "clock_field", "status_field"):
                    val = str(val).strip()[:120]
                    if k.endswith("_field") and val and "{n}" not in val and k in ("name_field", "info_field", "color_field"):
                        raise UserError("Row fields need {n} where the row number goes, e.g. P{n}Name.Text")
                    setattr(v, k, val or ("127.0.0.1" if k == "host" else val))
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
                rt = Runtime(self.project, self.backgrounds, on_event=self._on_event, demo_pickles=demo_pickles)
            except C.CalibrationError as e:
                raise UserError(str(e))
            srv = self.project.server
            if srv.enabled:
                self.streamer = LeaderboardStreamer(srv.url, srv.token, srv.station_id, __version__,
                                                    {"project": self.project.name, "track_length": self.project.track_length,
                                                     "finish_s": self.project.finish_s,
                                                     "marbles": [m.__dict__ for m in self.project.marbles],
                                                     "cameras": [{"key": c.key, "name": c.name, "role": c.role} for c in self.project.active_cameras]}).start()
            if self.project.vmix.enabled:
                self.vmix = vmix.VmixClient(self.project.vmix).start()
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
        if kind == "board" and self.vmix is not None:
            self.vmix.publish_board(data["board"], data["clock"], data["state"])
        s = self.streamer
        if s is None:
            return
        if kind == "board":
            rt = self.runtime
            s.publish_board(data["race_id"], data["state"], data["clock"], data["board"], rt.cam_state if rt else {}, data.get("late", False))
        elif kind in ("race_started", "finish", "race_complete", "new_race", "camera_offline", "camera_online", "camera_error"):
            s.publish_event(kind, {k: v for k, v in data.items()})

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
