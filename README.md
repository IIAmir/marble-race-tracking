# Marble Race Tracking

Real-time identification, tracking and ranking of colour-coded marbles from
**one top-down master camera** or **14 fixed track cameras** (plus an optional
high-frame-rate finish camera), with multi-camera fusion, occlusion handling
and a live leaderboard.

* **Architecture and implementation plan:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
* **Stakeholder feasibility report:** [`docs/feasibility-report.html`](docs/feasibility-report.html)
* **Interactive 3D replay:** [`demo/track3d.html`](demo/track3d.html) (open in a browser)

## How it works in one paragraph

Each camera subtracts the empty-track background, classifies foreground pixels
against per-camera CIELAB colour models (one per marble), groups pixels by
*(connected component, colour)* and projects each blob onto a calibrated
centerline whose waypoints carry track progress `s`. Only these detections
(a few hundred bytes per frame) leave the camera's edge node. A central fusion
engine assigns detections to marbles by colour and motion (Hungarian algorithm
with a colour ratio test), runs one Kalman filter per marble on `[s, v]`,
coasts hidden marbles only through known blind zones (tunnels, camera gaps,
failed cameras), times the finish with a least-squares fit around the line and
publishes a leaderboard in which no marble can gain a place while unseen.

## Quick start

```bash
pip install -r requirements.txt
python -m pytest                                   # unit + property-based invariant tests

python scripts/live_demo.py --mode multi --serve 8080          # real-time run, overlay at http://localhost:8080
python scripts/render_demo.py --mode single --out results/demo_single.mp4
python -m marbletrack.edgecases                     # F1-grade edge cases, one run each

python scripts/monte_carlo.py --seeds 10           # full stress suite      -> results/monte_carlo.json
python scripts/benchmark.py                        # detector cost per frame -> results/benchmark.json
python scripts/export_replays.py && python scripts/build_demo3d.py   # 3D replay page
python scripts/build_report.py                     # feasibility report from the results
```

## Layout

| Path | Contents |
|---|---|
| `marbletrack/track.py` | Centerline polyline with per-vertex progress; multi-candidate projection |
| `marbletrack/color.py` | Robust Lab colour models, palette separation check |
| `marbletrack/detector.py` | Edge detector (background, colour classification, grouping, projection, adaptation) |
| `marbletrack/fusion.py` | Association, Kalman filtering, blind zones, camera health, finish timing, leaderboard |
| `marbletrack/runtime.py` | Multi-process real-time runtime and HTTP/JSON leaderboard publisher |
| `marbletrack/sim.py` | Race simulator and virtual camera rigs (master, 14 segments, finish) |
| `marbletrack/race.py`, `marbletrack/edgecases.py` | Scenarios, scripted edge cases, ground-truth scoring |
| `scripts/` | Stress suite, benchmark, live demo, video/3D/report builders |
| `results/` | Measured outputs used by the report |
