"""Live tracking: one process per camera (grab + detect), one fusion thread.

Camera processes own their camera (a camera can be opened by one process at a
time), run the colour detector on every frame and send compact detections to
the main process. The fusion thread orders them by capture time
(``ReorderBuffer``), runs the ``FusionEngine`` and publishes the leaderboard.

Race sessions
-------------
* The race clock starts when the first marble passes ``project.start_s``.
* A race is complete when every marble has finished, or when nothing has been
  seen for 15 s after the first finish.
* With ``auto_new_race`` a new race begins when marbles appear at the start
  again after a completed race. "New race" in the UI does the same at once.
"""
from __future__ import annotations

import logging
import multiprocessing as mp
import pickle
import queue
import threading
import time
import traceback
import uuid
from collections import deque
from dataclasses import asdict, replace

import cv2
import numpy as np

from marbletrack.fusion import FusionEngine, ReorderBuffer

from . import calibration as C
from .cameras import make_source
from .config import Project

log = logging.getLogger("marbletrack.engine")


MIN_WORK_RADIUS_PX = 5.0  # smallest marble radius the detector should see after downscaling


def _work_scale(w: int, h: int, min_radius_px: float | None = None) -> float:
    """Processing scale for a camera's frames.

    The stress suite ran its detector at 640x480, so larger frames are processed
    downscaled to about that pixel count, which keeps 14 cameras within a CPU budget.
    A wide view in which marbles are small (an overhead 4K camera sees a 25 mm marble
    as ~10 px) is downscaled less, so that the smallest marble keeps a radius of at
    least ``MIN_WORK_RADIUS_PX``: below that, colour pixels and blob areas get too
    few to identify it reliably.
    """
    k = np.sqrt(640 * 480 / max(1, w * h))
    if min_radius_px:
        k = max(k, MIN_WORK_RADIUS_PX / min_radius_px)
    return float(min(1.0, max(0.25, k)))


def _min_radius_px(calib) -> float:
    if calib.area_px is not None and len(calib.area_px):
        return float(np.sqrt(np.min(calib.area_px) / np.pi))
    return float(calib.marble_radius_px)


def camera_worker(spec: dict, calib_blob: bytes, t0: float, out_q, cmd_q, stop_evt):  # runs in a child process
    from marbletrack.detector import ColorDetector

    key = spec["key"]
    src = recorder = None
    try:
        import os
        if spec.get("demo_pickle"):
            os.environ["MARBLETRACK_DEMO_PICKLE"] = spec["demo_pickle"]
        src = make_source(spec["backend"], spec["device"], spec["width"], spec["height"], spec["fps"], t0=t0)
        src.open()
        applied = src.apply(spec["auto_exposure"], spec["exposure"], spec["auto_white_balance"])
        calib = pickle.loads(calib_blob)
        h, w = calib.track_mask.shape
        frame, _ = src.read()
        for _ in range(50):
            if frame is not None:
                break
            frame, _ = src.read()
        if frame is None:
            raise RuntimeError("camera delivers no frames")
        if frame.shape[1] != w or frame.shape[0] != h:
            raise RuntimeError(f"camera now delivers {frame.shape[1]}x{frame.shape[0]} but was set up at {w}x{h}: redo its setup")
        det = ColorDetector(calib, work_scale=_work_scale(w, h, _min_radius_px(calib)),
                            readout=spec.get("readout_ms", 0.0) / 1000.0)
        if spec.get("record_dir"):
            from .recording import FrameRecorder

            recorder = FrameRecorder(spec["record_dir"], key, spec["fps"], spec.get("record_quality", 92))
        out_q.put(("started", key, applied))
        latency = spec.get("latency_ms", 0.0) / 1000.0
        colors = [tuple(int(h[i:i + 2], 16) for i in (5, 3, 1)) for h in spec.get("colors", [])]  # #rrggbb -> BGR
        preview_every, preview_w = 0.25, 400
        next_preview = 0.0
        n, busy, stat_t = 0, 0.0, time.perf_counter()
        rack = spec.get("rack")  # finish-rack camera: read the slots instead of tracking
        next_rack = 0.0
        while not stop_evt.is_set():
            try:
                while True:
                    cmd = cmd_q.get_nowait()
                    if cmd[0] == "preview":
                        preview_every, preview_w = 1.0 / max(0.5, cmd[1]), int(cmd[2])
            except queue.Empty:
                pass
            frame, t_cap = src.read()
            if frame is None:
                out_q.put(("nofr", key, time.perf_counter() - t0))
                time.sleep(0.01)
                continue
            t = t_cap - t0 - latency
            if recorder is not None:
                recorder.add(frame, t_cap - t0, time.perf_counter() - t0)
            start = time.perf_counter()
            if rack is not None:
                dets = []
                if t >= next_rack:
                    from .rack import read_rack

                    next_rack = t + 0.25
                    slots = read_rack(frame, calib.background, rack["slots"], rack["radii"], det.palette, rack["ids"])
                    out_q.put(("rack", key, t, slots))
            else:
                dets = det.detect(frame, t)
                out_q.put(("det", key, t, dets))
            busy += time.perf_counter() - start
            n += 1
            if t >= next_preview:
                next_preview = t + preview_every
                small_w = min(preview_w, frame.shape[1])
                k = small_w / frame.shape[1]
                img = cv2.resize(frame, (small_w, round(frame.shape[0] * k)), interpolation=cv2.INTER_AREA) if k < 1 else frame
                boxes = [[float(d.xy[0]), float(d.xy[1]), float(np.sqrt(d.area / np.pi) / max(det.work_scale, 1e-6)), int(np.argmin(d.color_d2))] for d in dets]
                img = img.copy() if img is frame else img
                for x, y, r, ci in boxes:
                    col = colors[ci] if ci < len(colors) else (255, 255, 255)
                    c, rr = (int(round(x * k)), int(round(y * k))), max(4, int(round(r * k * 1.5)))
                    cv2.circle(img, c, rr + 1, (20, 20, 20), 3, cv2.LINE_AA)
                    cv2.circle(img, c, rr, col, 2, cv2.LINE_AA)
                ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])
                out_q.put(("preview", key, jpg.tobytes(), boxes, (frame.shape[1], frame.shape[0])))
            now = time.perf_counter()
            if now - stat_t >= 1.0:
                out_q.put(("stats", key, {"fps": n / (now - stat_t), "proc_ms": 1000 * busy / max(n, 1)}))
                n, busy, stat_t = 0, 0.0, now
    except Exception as e:
        out_q.put(("error", key, f"{e}", traceback.format_exc()))
    finally:
        if src is not None:
            try:
                src.close()
            except Exception:
                pass
        if recorder is not None:
            try:
                out_q.put(("recorded", key, recorder.close()))
            except Exception:
                pass


class RaceSession:
    def __init__(self, project: Project, cameras: dict, has_finish_cam: bool):
        self.id = uuid.uuid4().hex[:12]
        self.project = project
        ids = [m.id for m in project.marbles]
        blind = [tuple(z) for z in project.blind_zones]
        laps = max(1, int(project.laps))
        lap = project.track_length if laps > 1 else None
        self.engine = FusionEngine(ids, (laps - 1) * project.track_length + project.finish_s, project.track_length,
                                   cameras=cameras, laps=laps, lap_length=lap,
                                   static_blind_zones=blind, rank_margin=project.marble_diameter_mm / 2000.0,
                                   photo_finish_s=0.0005 if has_finish_cam else 0.006)
        self.t0: float | None = None  # race clock origin (app time)
        self.state = "waiting"  # waiting -> running -> complete
        self.finished_ids: set[str] = set()
        self.first_finish: float | None = None
        self.last_seen = 0.0
        self.quiet = False
        self.late = False
        self.n_events = 0
        self.created = time.time()
        # result: provisional until the operator (or the rack check) confirms it
        self.confirmed = False
        self.manual_order: list[str] | None = None  # operator's final order, overriding the tracker's


class Runtime:
    """Owns the camera processes and the fusion thread while the app is live."""

    def __init__(self, project: Project, backgrounds: dict, on_event=None, demo_pickles: dict | None = None,
                 record_dir=None):
        self.project = project
        self.record_dir = record_dir  # a session folder (recording.start_session) or None
        self.session_log = None
        if record_dir is not None:
            from .recording import SessionLog

            self.session_log = SessionLog(record_dir)
        self.on_event = on_event or (lambda kind, data: None)
        self.calibs = {}
        for cam in project.active_cameras:
            self.calibs[cam.key] = C.build_calibration(cam, project, backgrounds.get(cam.key))
        if not self.calibs:
            raise C.CalibrationError("no camera is enabled")
        self.demo_pickles = demo_pickles or {}
        self.ctx = mp.get_context("spawn")  # what Windows does anyway; same behaviour everywhere
        self.out_q = self.ctx.Queue(maxsize=5000)
        self.stop_evt = self.ctx.Event()
        self.procs, self.cmd_qs = {}, {}
        self.cam_state = {c.key: {"name": c.name, "online": False, "fps": 0.0, "proc_ms": 0.0, "error": "", "dets": 0,
                                  "role": c.role, "applied": {}} for c in project.active_cameras}
        self.previews: dict = {}  # key -> (jpeg, boxes, size, time)
        self.preview_cond = threading.Condition()
        self.buf = ReorderBuffer(latency=0.08)
        self.lock = threading.RLock()
        self.t0 = time.perf_counter()
        self.has_finish = any(c.role == "finish" for c in project.active_cameras)
        self.cameras_s = {c.key: c.s_ranges for c in project.active_cameras if c.role != "rack"}
        self.rack: dict = {}  # rack camera -> latest slot reading
        self._rack_reported: dict = {}  # race id -> last rack_check result sent
        self.race = RaceSession(project, self.cameras_s, self.has_finish)
        self.log: deque = deque(maxlen=200)
        self.board: list = []
        self.thread = threading.Thread(target=self._fusion_loop, daemon=True, name="fusion")
        self._running = False
        self.history: deque = deque(maxlen=20)
        self._publish_every = 1.0 / max(1.0, project.server.publish_hz)
        self._next_pub = 0.0
        self.frozen = False  # operator: hold the board on air as it is
        self.hidden = False  # operator: take the board off air

    # -- lifecycle ----------------------------------------------------------------
    def start(self):
        self._running = True
        for cam in self.project.active_cameras:
            spec = dict(key=cam.key, backend=cam.backend, device=cam.device, width=cam.width, height=cam.height,
                        fps=cam.fps, auto_exposure=cam.auto_exposure, exposure=cam.exposure,
                        auto_white_balance=cam.auto_white_balance, latency_ms=cam.latency_ms, readout_ms=cam.readout_ms,
                        demo_pickle=self.demo_pickles.get(cam.key),
                        colors=[m.color for m in self.project.marbles],
                        rack=self._rack_spec(cam) if cam.role == "rack" else None,
                        record_dir=str(self.record_dir) if self.record_dir is not None else None,
                        record_quality=self.project.record.jpeg_quality)
            cq = self.ctx.Queue()
            p = self.ctx.Process(target=camera_worker, name=f"cam-{cam.key}",
                                 args=(spec, pickle.dumps(self.calibs[cam.key]), self.t0, self.out_q, cq, self.stop_evt),
                                 daemon=True)
            p.start()
            self.procs[cam.key], self.cmd_qs[cam.key] = p, cq
        self.thread.start()
        self._event("tracking_started", {"cameras": len(self.procs)})

    def stop(self):
        self._running = False
        self.stop_evt.set()
        for p in self.procs.values():
            p.join(timeout=3)
            if p.is_alive():
                p.terminate()
        if self.thread.is_alive():
            self.thread.join(timeout=3)
        self._drain_recorded()
        self._event("tracking_stopped", {})
        if self.session_log is not None:
            self.session_log.close()

    def _drain_recorded(self):
        """Collect the camera processes' recording summaries (sent as they exit)."""
        while True:
            try:
                msg = self.out_q.get(timeout=0.2)
            except (queue.Empty, EOFError, OSError):
                return
            if msg[0] == "recorded":
                self._handle(msg, self.now())

    def set_preview(self, key: str | None, focus_fps=12.0, focus_w=960, grid_fps=4.0, grid_w=400):
        for k, q in self.cmd_qs.items():
            q.put(("preview", focus_fps, focus_w) if k == key else ("preview", grid_fps, grid_w))

    def now(self) -> float:
        return time.perf_counter() - self.t0

    # -- race control -------------------------------------------------------------
    def new_race(self, reason="operator"):
        with self.lock:
            old = self.race
            if old.state != "waiting" or old.engine.leaderboard():
                self.history.appendleft(self._result(old))
            self.race = RaceSession(self.project, self.cameras_s, self.has_finish)
            self.board = []
        self._event("new_race", {"race_id": self.race.id, "reason": reason})

    def _rack_spec(self, cam) -> dict:
        return {"slots": [list(map(float, m)) for m in cam.markers],
                "radii": [float(r) for r in C.marble_radius_px(cam, self.project)],
                "ids": [m.id for m in self.project.marbles]}

    def _check_rack(self, race: RaceSession):
        """Compare the finish rack with the board once the race is complete; report changes."""
        if race.state != "complete" or not self.rack:
            return
        from .rack import compare

        slots = next(iter(self.rack.values()))["slots"]
        order = [e["marble_id"] for e in self._board(race) if e.get("finish_time") is not None]
        res = compare(slots, order)
        key = (res["match"], tuple(map(str, res["rack"])))
        if self._rack_reported.get(race.id) == key:
            return
        self._rack_reported[race.id] = key
        if res["match"] and self.project.rack_auto_confirm and not race.confirmed:
            race.confirmed = True
            res["confirmed"] = True
        self._event("rack_check", {"race_id": race.id, **res})

    # -- operator console -----------------------------------------------------------
    def operator(self, action: str, **args) -> dict:
        """Corrections from the race-control desk. Every one is logged with its time."""
        ids = {m.id for m in self.project.marbles}
        with self.lock:
            race = self.race
            if action == "swap":
                a, b = args["a"], args["b"]
                if a == b or a not in ids or b not in ids:
                    raise ValueError("pick two different marbles")
                race.engine.swap_identities(a, b)
                detail = {"a": a, "b": b}
            elif action == "set_order":
                # may be partial: marbles not listed follow in the tracker's order
                order = list(dict.fromkeys(m for m in args["order"] if m in ids))
                race.manual_order = order or None
                detail = {"order": order}
            elif action == "confirm":
                race.confirmed = bool(args.get("confirmed", True))
                detail = {"confirmed": race.confirmed}
            elif action == "freeze":
                self.frozen = bool(args.get("on", True))
                detail = {"on": self.frozen}
            elif action == "hide":
                self.hidden = bool(args.get("on", True))
                detail = {"on": self.hidden}
            else:
                raise ValueError(f"unknown action {action}")
            self._next_pub = 0.0  # publish the change at once
        self._event("operator", {"action": action, "race_id": race.id, **detail})
        return detail

    def _result(self, race: RaceSession) -> dict:
        return {"race_id": race.id, "state": race.state, "board": self._board(race)}

    # -- fusion loop ----------------------------------------------------------------
    def _fusion_loop(self):
        while self._running:
            try:
                msg = self.out_q.get(timeout=0.01)
            except queue.Empty:
                msg = None
            except (EOFError, OSError):
                break
            self.step(msg, self.now())
            for k, p in self.procs.items():
                st = self.cam_state[k]
                if not p.is_alive() and not st["error"] and self._running:
                    st["error"], st["online"] = "camera process stopped", False

    def step(self, msg, now: float):
        """One turn of the fusion loop: take a camera message (or none), fuse, publish when due.

        The live loop calls it with the wall clock; ``replay`` calls it with the
        recorded arrival times, so a recorded session is tracked exactly as live.
        """
        if msg is not None:
            self._handle(msg, now)
        with self.lock:
            race = self.race
            for t, cam, dets in self.buf.pop_ready(now):
                race.engine.process(t, dets, cam)
                if dets:
                    race.last_seen = t
            race.engine.tick(now - self.buf.latency)
            for ev in race.engine.events[race.n_events:]:
                if ev.kind in ("camera_offline", "camera_online"):
                    self._event(ev.kind, {"camera": ev.subject, "name": self.cam_state.get(ev.subject, {}).get("name", ev.subject)})
            race.n_events = len(race.engine.events)
            self._race_logic(race, now)
            if now >= self._next_pub:
                self._next_pub = now + self._publish_every
                self.board = self._board(race)
                data = {"race_id": race.id, "state": race.state, "clock": self.clock(race, now),
                        "late": race.late, "board": self.board,
                        "result": "confirmed" if race.confirmed else "provisional",
                        "frozen": self.frozen, "hidden": self.hidden}
                if self.session_log is not None:
                    self.session_log.board(now, data)
                self._event("board", data, log=False)

    def _handle(self, msg, now):
        kind, key = msg[0], msg[1]
        st = self.cam_state.get(key)
        if st is None:
            return
        if kind == "det":
            _, _, t, dets = msg
            self.buf.push(t, key, dets)
            st["online"], st["dets"], st["last"] = True, len(dets), now
        elif kind == "preview":
            _, _, jpg, boxes, size = msg
            with self.preview_cond:
                self.previews[key] = (jpg, boxes, size, now)
                self.preview_cond.notify_all()
        elif kind == "rack":
            _, _, t, slots = msg
            self.rack[key] = {"t": t, "slots": slots}
            st["online"], st["last"] = True, now
            with self.lock:
                self._check_rack(self.race)
        elif kind == "stats":
            st.update(msg[2])
        elif kind == "recorded":
            st["recorded"] = msg[2]
            self._event("camera_recorded", {"camera": key, "name": st["name"], **msg[2]})
        elif kind == "started":
            st["applied"], st["error"] = msg[2], ""
            self._event("camera_started", {"camera": key, "name": st["name"]})
        elif kind == "error":
            st["error"], st["online"] = msg[2], False
            log.error("camera %s: %s\n%s", key, msg[2], msg[3] if len(msg) > 3 else "")
            self._event("camera_error", {"camera": key, "name": st["name"], "error": msg[2]})

    def _race_logic(self, race: RaceSession, now: float):
        eng = race.engine
        board = eng.leaderboard()
        lead = max((e.progress_m for e in board if e.status not in ("unseen",)), default=None)
        if race.state == "waiting" and lead is not None and lead >= self.project.start_s:
            fast = max((e.speed_mps for e in board), default=0.0)
            race.t0 = eng.now - max(0.0, lead - self.project.start_s) / max(fast, 0.3)
            # first seen well past the start (tracking began mid-race): the clock is an estimate
            race.late = lead > self.project.start_s + 0.5
            race.state = "running"
            self._event("race_started", {"race_id": race.id, "late": race.late})
        if race.state == "running":
            for e in board:
                if e.finish_time is not None and e.marble_id not in race.finished_ids:
                    race.finished_ids.add(e.marble_id)
                    race.first_finish = race.first_finish or now
                    self._event("finish", {"race_id": race.id, "marble_id": e.marble_id, "name": self._name(e.marble_id),
                                           "rank": e.rank, "time": round(e.finish_time - race.t0, 4)})
            done = len(race.finished_ids) == len(self.project.marbles)
            stale = race.first_finish is not None and now - max(race.last_seen, race.first_finish) > 15
            if done or stale:
                race.state = "complete"
                self._event("race_complete", {"race_id": race.id, "board": self._board(race)})
                self._check_rack(race)
        elif race.state == "complete" and self.project.auto_new_race:
            # marbles back on the track after a quiet spell (the track was cleared, then reloaded)
            if race.last_seen and now - race.last_seen > 3:
                race.quiet = True
            if race.quiet and now - race.last_seen < 0.5:
                self.new_race("marbles back on the track")

    def clock(self, race: RaceSession, now: float) -> float | None:
        if race.t0 is None:
            return None
        if race.state == "complete" and race.finished_ids:
            last = max((e.finish_time for e in race.engine.leaderboard() if e.finish_time is not None), default=None)
            if last is not None:
                return last - race.t0
        return now - self.buf.latency - race.t0

    def _name(self, mid):
        return next((m.name for m in self.project.marbles if m.id == mid), mid)

    def _board(self, race: RaceSession) -> list[dict]:
        out = []
        colors = {m.id: m for m in self.project.marbles}
        entries = race.engine.leaderboard()
        if race.manual_order:
            pos = {mid: k for k, mid in enumerate(race.manual_order)}
            listed = sorted((e for e in entries if e.marble_id in pos), key=lambda e: pos[e.marble_id])
            entries = listed + [e for e in entries if e.marble_id not in pos]
            entries = [replace(e, rank=k + 1) for k, e in enumerate(entries)]
        for e in entries:
            d = asdict(e)
            m = colors.get(e.marble_id)
            d["name"], d["color"] = (m.name, m.color) if m else (e.marble_id, "#888888")
            if e.finish_time is not None and race.t0 is not None:
                d["finish_time"] = round(e.finish_time - race.t0, 4)
            d["progress_m"], d["speed_mps"], d["gap_m"] = round(d["progress_m"], 3), round(d["speed_mps"], 3), round(d["gap_m"], 3)
            d["manual"] = bool(race.manual_order and e.marble_id in race.manual_order)
            out.append(d)
        return out

    def _event(self, kind: str, data: dict, log: bool = True):
        if log and self.session_log is not None:
            self.session_log.event(self.now(), kind, data)
        if log:
            self.log.appendleft({"t": time.time(), "kind": kind, **{k: v for k, v in data.items() if k != "board"}})
        try:
            self.on_event(kind, data)
        except Exception:
            log_exc = logging.getLogger("marbletrack.engine")
            log_exc.exception("event handler failed")

    def status(self) -> dict:
        with self.lock:
            race = self.race
            return {"race": {"id": race.id, "state": race.state, "clock": self.clock(race, self.now()), "late": race.late,
                             "confirmed": race.confirmed, "manual": bool(race.manual_order)},
                    "frozen": self.frozen, "hidden": self.hidden,
                    "rack": {k: v["slots"] for k, v in self.rack.items()},
                    "board": self.board, "cameras": self.cam_state, "log": list(self.log)[:60],
                    "history": list(self.history)[:5]}
