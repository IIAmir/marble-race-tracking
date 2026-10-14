"""Real-time runtime: edge-node processes -> fusion process -> leaderboard publisher.

Each edge node is an OS process that owns a set of cameras, grabs frames on the
wall clock, runs the colour detector and ships compact detections (a few hundred
bytes per frame) to the fusion process. In production the frame grab is the
camera SDK (GenICam/GigE Vision); here it is the simulator, so the full system
runs in real time without hardware.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import queue
import threading
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

from .detector import ColorDetector
from .fusion import FusionEngine, ReorderBuffer
from .race import SCENARIOS, build_cameras
from .sim import DEFAULT_MARBLES, RaceSimulator, serpentine_track


def build_world(mode: str, scenario: str, seed: int, fps: float, finish_cam: bool):
    """Deterministic: every process rebuilds the identical track, race and rig."""
    cfg = SCENARIOS[scenario].race
    track = serpentine_track(cfg)
    sim = RaceSimulator(track, DEFAULT_MARBLES, cfg, seed=seed)
    cams = build_cameras(mode, track, cfg, seed, fps, finish_cam=finish_cam)
    s_finish = track.s_range[1] - cfg.finish_before_end
    return cfg, track, sim, cams, s_finish


def edge_node(node: int, cam_idx: list[int], args: dict, out_q, ready, t0_wall, stop):
    cfg, track, sim, cams, _ = build_world(args["mode"], args["scenario"], args["seed"], args["fps"], args["finish_cam"])
    mine = [cams[k] for k in cam_idx]
    dets = {}
    for c in mine:
        big = c.size[0] * c.size[1] > 1.5e6
        dets[c.cam_id] = ColorDetector(c.calibration(DEFAULT_MARBLES, sim), fg_scale=0.5 if big else 1.0)
    colors = [m.bgr for m in DEFAULT_MARBLES]
    ready.release()
    while t0_wall.value == 0.0:
        time.sleep(0.005)
    t0 = t0_wall.value
    next_n = {c.cam_id: 0 for c in mine}
    dropped = 0
    busy = 0.0
    while not stop.is_set():
        # Next due frame across this node's cameras.
        due = min(mine, key=lambda c: next_n[c.cam_id] / (c.fps or args["fps"]) + c.phase)
        f = due.fps or args["fps"]
        t_cap = next_n[due.cam_id] / f + due.phase
        if t_cap > args["duration"]:
            break
        wait = t0 + t_cap - time.time()
        if wait > 0:
            time.sleep(wait)
        elif -wait > 1.5 / f:
            # Fell behind by more than a frame: drop it, as a camera driver would.
            next_n[due.cam_id] += 1
            dropped += 1
            continue
        start = time.perf_counter()
        xy, vis = sim.world_positions(t_cap)
        frame = due.render(xy, vis, colors, cfg.illumination(t_cap))  # = camera grab
        d = dets[due.cam_id].detect(frame, t_cap)
        busy += time.perf_counter() - start
        out_q.put(("frame", due.cam_id, t_cap, d))
        next_n[due.cam_id] += 1
    out_q.put(("done", node, dropped, busy))


class LeaderboardServer:
    """Tiny HTTP publisher: GET /state (JSON) and GET / (overlay page polling it).

    The overlay page is what a broadcast scene would load as a browser source.
    """

    PAGE = """<!doctype html><meta charset=utf-8><title>Leaderboard</title>
<style>body{font:600 18px system-ui;background:#111;color:#eee;margin:16px}
td{padding:4px 10px}.coasting td{opacity:.55}.finished td:first-child{color:#ffd54a}</style>
<table id=t></table><script>
async function tick(){const s=await (await fetch('state')).json();
document.getElementById('t').innerHTML=s.board.map(e=>`<tr class=${e.status}><td>${e.rank}</td>
<td>${e.marble_id}</td><td>${e.finish_time!=null?e.finish_time.toFixed(3)+' s':'+'+e.gap_m.toFixed(2)+' m'}</td>
<td>${e.photo_finish?'PHOTO':e.status}</td></tr>`).join('')}
setInterval(tick,100)</script>"""

    def __init__(self, port: int):
        self.state = {"board": []}
        srv = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = (json.dumps(srv.state) if self.path.startswith("/state") else srv.PAGE).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json" if self.path.startswith("/state") else "text/html")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


def run_live(mode="multi", scenario="nominal", seed=0, fps=30.0, nodes=3, finish_cam=True,
             duration=None, serve_port=None, publish_hz=10.0, on_publish=None) -> dict:
    cfg, track, sim, cams, s_finish = build_world(mode, scenario, seed, fps, finish_cam)
    duration = duration or sim.duration + 0.3
    args = dict(mode=mode, scenario=scenario, seed=seed, fps=fps, finish_cam=finish_cam, duration=duration)
    ids = [m.marble_id for m in DEFAULT_MARBLES]
    nodes = max(1, min(nodes, len(cams)))
    split = [list(range(len(cams)))[i::nodes] for i in range(nodes)]

    ctx = mp.get_context("fork")
    q, ready, stop = ctx.Queue(), ctx.Semaphore(0), ctx.Event()
    t0_wall = ctx.Value("d", 0.0)
    procs = [ctx.Process(target=edge_node, args=(i, split[i], args, q, ready, t0_wall, stop), daemon=True)
             for i in range(nodes)]
    for p in procs:
        p.start()
    for _ in procs:
        ready.acquire()

    engine = FusionEngine(ids, s_finish, track.s_range[1], cameras={c.cam_id: c.s_range for c in cams},
                          static_blind_zones=list(cfg.tunnels), rank_margin=cfg.marble_radius,
                          photo_finish_s=0.0005 if finish_cam else 0.006)
    buf = ReorderBuffer(latency=0.05)
    server = LeaderboardServer(serve_port) if serve_port else None
    t0_wall.value = t0 = time.time() + 0.2

    latencies, publish_lat, done, stats = [], [], 0, {"dropped": 0, "edge_busy_s": 0.0}
    next_pub = 0.0
    ok = total = 0
    while done < nodes:
        try:
            msg = q.get(timeout=0.01)
        except queue.Empty:
            msg = None
        now = time.time() - t0
        if msg is not None and msg[0] == "done":
            done += 1
            stats["dropped"] += msg[2]
            stats["edge_busy_s"] += msg[3]
        elif msg is not None:
            _, cam_id, t_cap, d = msg
            buf.push(t_cap, cam_id, d)
        for t_cap, cam_id, d in buf.pop_ready(now):
            engine.process(t_cap, d, cam_id)
            latencies.append(time.time() - t0 - t_cap)
        engine.tick(now - buf.latency)
        if now >= next_pub:
            next_pub = now + 1 / publish_hz
            board = engine.leaderboard()
            truth = sim.progress(engine.now)
            seen = [e for e in board if e.status == "tracking"]
            good = all(truth[ids.index(a.marble_id)] >= truth[ids.index(b.marble_id)] - 4 * cfg.marble_radius
                       for a, b in zip(seen, seen[1:]))
            ok += good
            total += 1
            # Age of the newest information on the published board.
            publish_lat.append(now - engine.now)
            if server:
                server.state = {"t": engine.now, "board": [asdict(e) for e in board],
                                "cameras": engine.camera_status()}
            if on_publish:
                on_publish(now, board, engine)
    stop.set()
    for p in procs:
        p.join(timeout=2)
    for t_cap, cam_id, d in buf.flush():
        engine.process(t_cap, d, cam_id)
    if server:
        server.close()

    board = engine.leaderboard()
    truth_finish = sim.finish_times(s_finish)
    lat = np.array(latencies) * 1e3
    plat = np.array(publish_lat) * 1e3
    return {
        "mode": mode,
        "scenario": scenario,
        "cameras": len(cams),
        "edge_nodes": nodes,
        "frames_processed": int(len(lat)),
        "frames_dropped_realtime": stats["dropped"],
        "wall_duration_s": float(duration),
        "edge_cpu_utilisation": stats["edge_busy_s"] / (duration * nodes),
        "capture_to_fusion_ms": {k: float(np.percentile(lat, p)) for k, p in (("p50", 50), ("p95", 95), ("p99", 99))},
        "board_age_ms": {k: float(np.percentile(plat, p)) for k, p in (("p50", 50), ("p95", 95), ("p99", 99))},
        "live_rank_accuracy_visible": ok / max(1, total),
        "final_order": [e.marble_id for e in board],
        "truth_order": [ids[i] for i in np.argsort(truth_finish)],
        "finish_err_ms_max": float(max(abs((e.finish_time or np.inf) - truth_finish[ids.index(e.marble_id)])
                                       for e in board) * 1e3),
    }
