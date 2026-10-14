# Marble Race Tracking

Real-time identification, tracking and ranking of colour-coded marbles from
**14 fixed track cameras mounted at about 45°** or **one overhead master
camera**, plus a high-frame-rate finish camera, with multi-camera fusion,
occlusion handling and a live leaderboard.

* **Architecture and implementation plan:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
* **Production-readiness report:** [`docs/readiness-report.html`](docs/readiness-report.html)
* **Live race simulator:** [`demo/simulator.html`](demo/simulator.html), open in a browser (see below)
* **Interactive 3D replay** of recorded pipeline output: [`demo/track3d.html`](demo/track3d.html)

## How it works in one paragraph

Each camera subtracts the empty-track background, classifies foreground pixels
against per-camera CIELAB colour models (one per marble), groups pixels by
*(connected component, colour)* and projects each blob onto a calibrated
centerline whose waypoints carry track progress `s` (calibrated at
marble-centre height, with per-waypoint cross-track directions so that the
skew of an angled view does not shift progress). Only these detections
(a few hundred bytes per frame) leave the camera's edge node. A central fusion
engine assigns detections to marbles by colour and motion (Hungarian algorithm
with a colour ratio test), runs one Kalman filter per marble on `[s, v]`,
coasts hidden marbles only through known blind zones (tunnels, camera gaps,
failed cameras), times the finish with a least-squares fit around the line and
publishes a leaderboard in which no marble can gain a place while unseen.

## Live race simulator (`demo/simulator.html`)

A self-contained browser app that simulates races live and tracks them live:

* **Physics**: marbles roll under gravity (5/7 g·slope for a rolling sphere) with rolling
  resistance, drag, wall rubbing in corners, track-joint bumps, marble-to-marble collisions and
  per-marble grip that varies along the track (the *Chaos* setting).
* **Five maps**: Serpentine Table, Alpine Descent (hairpins, three tunnels), Spiral Tower (stacked
  levels the overhead camera cannot see), Figure-Eight Bridge and Grand Prix (lap races with a
  chain lift).
* **Camera rigs**: 14 cameras at ~45°, one overhead camera, or both, plus a 240 fps finish camera.
  A simulated detector produces what each camera's edge node would report (resolution-dependent
  noise, occlusion by tunnels, other track levels and other marbles, colour noise).
* **Tracker**: a JavaScript port of `marbletrack/fusion.py` (colour + motion assignment with the
  Hungarian algorithm, Kalman filter on track progress, blind zones, camera health, confirmed
  overtakes, least-squares finish timing, photo-finish flags), scored against the physics live.
  It adds one refinement found by fault-injection testing: a marble re-acquired after a long gap
  restarts from its average speed across the gap and must be seen three times before it can gain
  places.
* **Views**: free orbit with zoom (wheel, buttons, slider, double-click to focus), follow leader,
  follow selected, a TV director that cuts between rig cameras, overhead, finish camera; live
  camera feeds with detection boxes; a clickable minimap coloured by camera coverage.
* **Race control**: F1 start lights, marble count, laps, chaos, speed and slow motion, instant
  replay with scrubber, fault injection (kill camera, dim lights, network jitter), timing tower
  with true positions, telemetry, live tracker-vs-truth metrics, palette separation check.
* **Keys**: Space pause · R restart · N new race · 1–5 map · V cycle view · F follow selected ·
  +/− zoom · [ ] previous/next marble.

## Quick start

```bash
pip install -r requirements.txt
python -m pytest                                   # unit + property-based invariant tests

python scripts/live_demo.py --mode multi --serve 8080          # real-time run, overlay at http://localhost:8080
python scripts/render_demo.py --mode single --out results/demo_single.mp4
python -m marbletrack.edgecases                     # F1-grade edge cases, one run each

python scripts/monte_carlo.py --seeds 10           # full stress suite      -> results/monte_carlo.json
python scripts/benchmark.py                        # detector cost per frame -> results/benchmark.json
python scripts/angled_accuracy.py                  # angled-view geometry    -> results/angled_projection.json
python scripts/export_replays.py && python scripts/build_demo3d.py   # 3D replay page
python scripts/build_report.py                     # production-readiness report from the results
```

## Layout

| Path | Contents |
|---|---|
| `marbletrack/track.py` | Centerline polyline with per-vertex progress; multi-candidate projection |
| `marbletrack/color.py` | Robust Lab colour models, palette separation check |
| `marbletrack/detector.py` | Edge detector (background, colour classification, grouping, projection, adaptation) |
| `marbletrack/fusion.py` | Association, Kalman filtering, blind zones, camera health, finish timing, leaderboard |
| `marbletrack/runtime.py` | Multi-process real-time runtime and HTTP/JSON leaderboard publisher |
| `marbletrack/sim.py` | Race simulator, overhead and planar camera rigs |
| `marbletrack/sim3d.py` | 3D pinhole cameras at ~45° with depth buffer: walls, tunnels, perspective, occlusion |
| `marbletrack/race.py`, `marbletrack/edgecases.py` | Scenarios, scripted edge cases, ground-truth scoring |
| `scripts/` | Stress suite, benchmark, live demo, video/3D/report builders |
| `results/` | Measured outputs used by the report |
