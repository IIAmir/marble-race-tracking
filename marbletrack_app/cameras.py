"""Camera discovery and frame sources.

Backends
--------
* ``opencv``  USB/UVC cameras through DirectShow (Windows) or the platform default.
  Names come from ``cv2_enumerate_cameras`` or ``pygrabber`` when installed.
* ``pylon``   Basler GigE/USB3 industrial cameras through ``pypylon`` (optional).
* ``demo``    Virtual cameras rendered by the race simulator (``marbletrack.sim3d``):
  the same 14 angled cameras and finish camera the stress suite was run with, so the
  whole app can be set up and run without hardware.

Every source returns ``(frame_bgr, t_capture)`` with ``t_capture`` on
``time.perf_counter()``, which is a system-wide monotonic clock on Windows (QPC)
and Linux, so timestamps from different camera processes are comparable.
"""
from __future__ import annotations

import logging
import os
import pickle
import sys
import threading
import time
from dataclasses import dataclass

import cv2
import numpy as np

log = logging.getLogger("marbletrack.cameras")
IS_WINDOWS = sys.platform == "win32"


@dataclass
class CameraInfo:
    key: str
    backend: str
    device: str
    name: str
    width: int = 0
    height: int = 0
    fps: float = 0.0
    note: str = ""  # e.g. "in use by another application"

    def to_dict(self) -> dict:
        return self.__dict__.copy()


# ---------------------------------------------------------------------------- OpenCV
def _opencv_api() -> int:
    return cv2.CAP_DSHOW if IS_WINDOWS else cv2.CAP_ANY


def _opencv_names() -> dict[int, str]:
    """Friendly device names by index, when a helper package is available."""
    try:
        from cv2_enumerate_cameras import enumerate_cameras  # type: ignore

        return {c.index: c.name for c in enumerate_cameras(_opencv_api())}
    except Exception:
        pass
    if IS_WINDOWS:
        try:
            from pygrabber.dshow_graph import FilterGraph  # type: ignore

            return dict(enumerate(FilterGraph().get_input_devices()))
        except Exception:
            pass
    return {}


def discover_opencv(max_index: int = 10) -> list[CameraInfo]:
    names = _opencv_names()
    indices = sorted(names) if names else range(max_index)
    found, misses = [], 0
    for i in indices:
        cap = cv2.VideoCapture(int(i), _opencv_api())
        try:
            if not cap.isOpened():
                misses += 1
                if not names and misses >= 3:
                    break  # indices are contiguous; three misses in a row means no more cameras
                continue
            ok, frame = cap.read()
            w, h = (frame.shape[1], frame.shape[0]) if ok else (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
            fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            found.append(CameraInfo(f"opencv:{i}", "opencv", str(i), names.get(i, f"USB camera {i + 1}"), w, h, float(fps),
                                    "" if ok else "opens but returns no image (in use by another app?)"))
            misses = 0
        finally:
            cap.release()
    return found


class OpenCVSource:
    def __init__(self, device: str, width=0, height=0, fps=30.0):
        self.device, self.req = int(device), (width, height, fps)
        self.cap = None

    def open(self):
        self.cap = cv2.VideoCapture(self.device, _opencv_api())
        if not self.cap.isOpened():
            raise RuntimeError(f"camera {self.device} could not be opened (unplugged or in use by another app)")
        w, h, fps = self.req
        if IS_WINDOWS:  # MJPG lets USB2/3 cameras deliver full HD at 30+ fps over DirectShow
            self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        if w and h:
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        if fps:
            self.cap.set(cv2.CAP_PROP_FPS, fps)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # newest frame, not a queue of old ones

    def apply(self, auto_exposure: bool, exposure: float | None, auto_wb: bool) -> dict:
        """Lock exposure / white balance. Returns what the driver accepted."""
        res = {}
        if IS_WINDOWS:  # DirectShow convention: 0.75 = auto, 0.25 = manual
            res["auto_exposure"] = bool(self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.75 if auto_exposure else 0.25))
        else:
            res["auto_exposure"] = bool(self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 3 if auto_exposure else 1))
        if not auto_exposure and exposure is not None:
            res["exposure"] = bool(self.cap.set(cv2.CAP_PROP_EXPOSURE, float(exposure)))
        res["auto_white_balance"] = bool(self.cap.set(cv2.CAP_PROP_AUTO_WB, 1 if auto_wb else 0))
        return res

    def read(self):
        ok, frame = self.cap.read()
        t = time.perf_counter()
        return (frame if ok else None), t

    @property
    def size(self):
        return int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    def close(self):
        if self.cap is not None:
            self.cap.release()


# ---------------------------------------------------------------------------- Basler (pypylon)
def discover_pylon() -> list[CameraInfo]:
    try:
        from pypylon import pylon  # type: ignore
    except Exception:
        return []
    out = []
    try:
        for d in pylon.TlFactory.GetInstance().EnumerateDevices():
            serial = d.GetSerialNumber()
            out.append(CameraInfo(f"pylon:{serial}", "pylon", serial, f"{d.GetModelName()} ({serial})"))
    except Exception as e:  # pragma: no cover - hardware specific
        log.warning("pylon enumeration failed: %s", e)
    return out


class PylonSource:  # pragma: no cover - needs Basler hardware
    def __init__(self, serial: str, width=0, height=0, fps=30.0):
        self.serial, self.req = serial, (width, height, fps)
        self.cam = None

    def open(self):
        from pypylon import pylon  # type: ignore

        tl = pylon.TlFactory.GetInstance()
        dev = next(d for d in tl.EnumerateDevices() if d.GetSerialNumber() == self.serial)
        self.cam = pylon.InstantCamera(tl.CreateDevice(dev))
        self.cam.Open()
        w, h, fps = self.req
        try:
            if w and h:
                self.cam.Width.Value, self.cam.Height.Value = w, h
            self.cam.AcquisitionFrameRateEnable.Value = True
            self.cam.AcquisitionFrameRate.Value = float(fps)
        except Exception as e:
            log.info("pylon %s: %s", self.serial, e)
        self.conv = pylon.ImageFormatConverter()
        self.conv.OutputPixelFormat = pylon.PixelType_BGR8packed
        self.cam.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)

    def apply(self, auto_exposure, exposure, auto_wb) -> dict:
        res = {}
        try:
            self.cam.ExposureAuto.Value = "Continuous" if auto_exposure else "Off"
            res["auto_exposure"] = True
            if not auto_exposure and exposure is not None:
                self.cam.ExposureTime.Value = float(exposure)  # microseconds
                res["exposure"] = True
        except Exception:
            res["auto_exposure"] = False
        try:
            self.cam.BalanceWhiteAuto.Value = "Continuous" if auto_wb else "Off"
            res["auto_white_balance"] = True
        except Exception:
            res["auto_white_balance"] = False
        return res

    def read(self):
        from pypylon import pylon  # type: ignore

        r = self.cam.RetrieveResult(1000, pylon.TimeoutHandling_Return)
        t = time.perf_counter()
        if r is None or not r.GrabSucceeded():
            return None, t
        img = self.conv.Convert(r).GetArray()
        r.Release()
        return img, t

    @property
    def size(self):
        return int(self.cam.Width.Value), int(self.cam.Height.Value)

    def close(self):
        if self.cam is not None:
            self.cam.Close()


# ---------------------------------------------------------------------------- demo rig
class DemoWorld:
    """A virtual track with 14 angled cameras and a finish camera.

    ``scene`` is what the virtual track shows: ``empty`` (for the background
    capture), ``parked`` (marbles resting in every camera's view, for colour
    calibration) or ``race`` (an endless series of races with short breaks).
    Each race uses its own seed, so results differ from race to race.
    """

    GAP = 4.0  # empty-track break between races (s)

    def __init__(self, seed: int = 0, finish_fps: float = 120.0):
        from marbletrack.race import SCENARIOS, build_cameras
        from marbletrack.sim import DEFAULT_MARBLES, serpentine_track

        self.cfg = SCENARIOS["nominal"].race
        self.track = serpentine_track(self.cfg)
        self.marbles = DEFAULT_MARBLES
        self.cams = build_cameras("multi", self.track, self.cfg, seed, 30.0, finish_cam=True)
        for c in self.cams:
            if c.cam_id == "finish":
                c.fps = finish_fps
            c.fps = c.fps or 30.0
        self.seed = seed
        self.s_finish = self.track.s_range[1] - self.cfg.finish_before_end
        self._races: list = []

    # Shared, deterministic race timeline: every process computes the same races.
    def _race(self, k: int):
        from marbletrack.sim import RaceSimulator

        while len(self._races) <= k:
            n = len(self._races)
            start = 0.0 if n == 0 else self._races[-1][0] + self._races[-1][1].duration + 1.0 + self.GAP
            if n == 0:
                start = self.GAP
            self._races.append((start, RaceSimulator(self.track, self.marbles, self.cfg, seed=self.seed * 1000 + n)))
        return self._races[k]

    def race_at(self, t: float):
        k = 0
        while True:
            start, sim = self._race(k)
            end = start + sim.duration + 1.0
            if t < start:
                return None, 0.0  # break between races: empty track
            if t < end:
                return sim, t - start
            k += 1

    def colors(self):
        return [m.bgr for m in self.marbles]

    def _park_range(self, cam):
        """Longest stretch this camera sees that is not inside a tunnel (cached)."""
        cache = self.__dict__.setdefault("_park_cache", {})
        if cam.cam_id not in cache:
            # inside the stretch the demo's own waypoint marks cover (the tracker's band ends there)
            _, marks = self.waypoints(cam.cam_id, 0.05 if cam.cam_id == "finish" else 0.2, jitter_px=0.0)
            _, s_vis = self.waypoints(cam.cam_id, 0.02, jitter_px=0.0)
            if len(marks) >= 2:
                s_vis = [x for x in s_vis if marks[0] + 0.03 <= x <= marks[-1] - 0.03]
            s_vis = np.array([x for x in s_vis if not any(a - 0.04 <= x <= b + 0.04 for a, b in self.cfg.tunnels)])
            if len(s_vis) < 2:
                cache[cam.cam_id] = tuple(cam.s_range)
            else:
                runs = np.split(s_vis, np.flatnonzero(np.diff(s_vis) > 0.03) + 1)
                best = max(runs, key=lambda r: r[-1] - r[0])
                cache[cam.cam_id] = (float(best[0]), float(best[-1]))
        return cache[cam.cam_id]

    PARK_PERIOD = 6.0  # cameras with a short view show the parked marbles in groups, in turn

    def parked_positions(self, cam, t: float = 0.0):
        """Marbles resting in this camera's view, at least 5 cm apart (blobs must not touch).

        A short view (the finish camera) cannot hold every marble that far apart, so
        they are shown in groups that take turns, as an installer would place them.
        """
        lo, hi = self._park_range(cam)
        k = len(self.marbles)
        margin = min(0.08, 0.1 * (hi - lo))
        lo, hi = lo + margin, hi - margin
        span = hi - lo
        per = max(1, min(k, int(span / 0.05) + 1))
        groups = int(np.ceil(k / per))
        g = int(t // self.PARK_PERIOD) % groups
        idx = np.arange(k)
        vis = (idx // per) == g
        slot = idx % per
        s = lo + span * (slot + 0.5) / per
        xy, tan = self.track.point_at(s)
        nrm = np.stack([-tan[:, 1], tan[:, 0]], axis=1)
        lat = np.where(slot % 2, 0.008, -0.008)
        return xy + lat[:, None] * nrm, vis

    def render(self, cam, t: float, scene: str):
        if scene == "empty":
            return cam.render(np.zeros((0, 2)), np.zeros(0, bool), [], 1.0)
        if scene == "parked":
            xy, vis = self.parked_positions(cam, t)
            return cam.render(xy, vis, self.colors(), 1.0)
        sim, tt = self.race_at(t)
        if sim is None:
            return cam.render(np.zeros((0, 2)), np.zeros(0, bool), [], 1.0)
        xy, vis = sim.world_positions(tt)
        return cam.render(xy, vis, self.colors(), 1.0)

    def slim(self, cam_id: str) -> "DemoWorld":
        """A copy holding only one camera: what a camera process needs (the full rig is ~0.5 GB)."""
        import copy

        w = copy.copy(self)
        w.cams = [self.camera(cam_id)]
        w._races = []
        return w

    def camera(self, cam_id: str):
        return next(c for c in self.cams if c.cam_id == cam_id)

    def waypoints(self, cam_id: str, spacing: float, jitter_px: float = 1.0, seed: int = 0):
        """Where an installer would click: marble-centre points every ``spacing`` metres."""
        cam = self.camera(cam_id)
        lo, hi = cam.s_range
        lo, hi = max(0.0, lo - 0.3), min(self.track.s_range[1], hi + 0.3)  # every mark in the frame, not just the nominal section
        s = np.arange(np.ceil(lo / spacing - 1e-9) * spacing, hi + 1e-9, spacing)
        xy, _ = self.track.point_at(s)
        uv, z = cam.project(np.c_[xy, np.full(len(s), self.cfg.marble_radius)])
        w, h = cam.size
        rng = np.random.default_rng(seed)
        uv = uv + rng.normal(0, jitter_px, uv.shape)
        m = 6  # a mark right at the image edge is not clickable
        keep = (z > 0) & (uv[:, 0] >= m) & (uv[:, 0] < w - m) & (uv[:, 1] >= m) & (uv[:, 1] < h - m)
        # one contiguous run (a far loop of the track may also be in the frame)
        idx = np.flatnonzero(keep)
        if len(idx):
            runs = np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1)
            near = min(runs, key=lambda r: abs(s[r].mean() - (cam.s_range[0] + cam.s_range[1]) / 2))
            keep = np.zeros_like(keep)
            keep[near] = True
        return uv[keep].tolist(), [round(float(x), 4) for x in s[keep]]


_demo_cache: dict = {}
_demo_lock = threading.Lock()


def demo_world(seed: int = 0) -> DemoWorld:
    """Building the rig takes a few seconds; build once per process and share."""
    with _demo_lock:
        if seed not in _demo_cache:
            path = os.environ.get("MARBLETRACK_DEMO_PICKLE")
            if path and os.path.exists(path):
                with open(path, "rb") as f:
                    _demo_cache[seed] = pickle.load(f)
            else:
                _demo_cache[seed] = DemoWorld(seed)
        return _demo_cache[seed]


def discover_demo() -> list[CameraInfo]:
    w = demo_world()
    return [CameraInfo(f"demo:{c.cam_id}", "demo", c.cam_id,
                       "Finish camera (demo)" if c.cam_id == "finish" else f"Demo {c.cam_id.replace('cam', 'camera ')}",
                       c.size[0], c.size[1], float(c.fps)) for c in w.cams]


class DemoSource:
    """Paced like a real camera: one frame every 1/fps seconds of wall time."""

    scene = "race"  # class-level default; the setup UI switches it for every demo camera at once

    def __init__(self, cam_id: str, t0: float | None = None):
        self.cam_id, self.t0 = cam_id, t0
        self.world = None
        self.next_t = None

    def open(self):
        self.world = demo_world()
        self.cam = self.world.camera(self.cam_id)
        self.t0 = self.t0 if self.t0 is not None else time.perf_counter()
        self.period = 1.0 / float(self.cam.fps)

    def apply(self, *a) -> dict:
        return {"auto_exposure": True, "exposure": True, "auto_white_balance": True}

    def read(self):
        now = time.perf_counter()
        if self.next_t is None:
            self.next_t = now
        if self.next_t > now:
            time.sleep(self.next_t - now)
        t = self.next_t
        self.next_t = max(self.next_t + self.period, time.perf_counter() - self.period)  # drop frames if behind
        return self.world.render(self.cam, t - self.t0, DemoSource.scene), t

    @property
    def size(self):
        return self.cam.size

    def close(self):
        pass


# ---------------------------------------------------------------------------- factory
def discover(include_demo: bool = False) -> list[CameraInfo]:
    cams = []
    for fn in (discover_opencv, discover_pylon):
        try:
            cams += fn()
        except Exception as e:  # never let one backend break discovery
            log.warning("camera discovery (%s) failed: %s", fn.__name__, e)
    if include_demo:
        cams += discover_demo()
    return cams


def make_source(backend: str, device: str, width=0, height=0, fps=30.0, t0=None):
    if backend == "opencv":
        return OpenCVSource(device, width, height, fps)
    if backend == "pylon":
        return PylonSource(device, width, height, fps)
    if backend == "demo":
        return DemoSource(device, t0)
    raise ValueError(f"unknown camera backend {backend!r}")


class LiveCamera:
    """Setup-time camera: a grab thread keeps the newest frame for previews and clicks."""

    def __init__(self, info_key: str, source):
        self.key, self.source = info_key, source
        self.frame, self.t, self.seq, self.error = None, 0.0, 0, ""
        self.fps = 0.0
        self._stop = threading.Event()
        self._cond = threading.Condition()
        self._jpeg_cache: dict = {}
        self.last_used = time.monotonic()
        self.thread = threading.Thread(target=self._run, daemon=True, name=f"grab-{info_key}")

    IDLE_AFTER = 3.0  # s without anyone looking: drop to 2 frames/s (15 cameras at full rate would starve the UI)

    def touch(self):
        self.last_used = time.monotonic()

    def start(self):
        try:
            self.source.open()
        except Exception as e:
            self.error = str(e)
            return self
        self.thread.start()
        return self

    def _run(self):
        last, n = time.perf_counter(), 0
        fails = 0
        while not self._stop.is_set():
            if time.monotonic() - self.last_used > self.IDLE_AFTER:
                self._stop.wait(0.5)
                if self._stop.is_set():
                    break
            try:
                frame, t = self.source.read()
            except Exception as e:
                frame, t = None, time.perf_counter()
                self.error = str(e)
            if frame is None:
                fails += 1
                if fails > 30:
                    self.error = self.error or "no frames from the camera"
                time.sleep(0.02)
                continue
            fails, self.error = 0, ""
            with self._cond:
                self.frame, self.t = frame, t
                self.seq += 1
                self._cond.notify_all()
            n += 1
            if t - last >= 1.0:
                self.fps, last, n = n / (t - last), t, 0

    def wait(self, after_seq: int, timeout: float = 1.0):
        self.touch()
        with self._cond:
            self._cond.wait_for(lambda: self.seq > after_seq or self._stop.is_set(), timeout)
            return self.frame, self.seq

    def jpeg(self, max_w: int = 960, quality: int = 80):
        self.touch()
        frame, seq = self.frame, self.seq
        if frame is None:
            return None, seq
        key = (seq, max_w)
        if key not in self._jpeg_cache:
            h, w = frame.shape[:2]
            img = cv2.resize(frame, (max_w, round(h * max_w / w)), interpolation=cv2.INTER_AREA) if w > max_w else frame
            self._jpeg_cache = {key: cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])[1].tobytes()}
        return self._jpeg_cache[key], seq

    def stop(self):
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        if self.thread.is_alive():
            self.thread.join(timeout=2)
        try:
            self.source.close()
        except Exception:
            pass
