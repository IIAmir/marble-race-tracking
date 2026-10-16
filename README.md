# Marble Race Tracking

Real-time identification, tracking and ranking of colour-coded marbles from
**14 fixed track cameras mounted at about 45°** or **one overhead master
camera**, plus a high-frame-rate finish camera, with multi-camera fusion,
occlusion handling and a live leaderboard.

* **Showing it (demo guide):** [`docs/DEMO.md`](docs/DEMO.md); venue race video: [`results/venue_race.mp4`](results/venue_race.mp4)
* **User guide (Persian, PDF):** [`docs/MarbleTrack-Guide-FA.pdf`](docs/MarbleTrack-Guide-FA.pdf): installation, every setup step with where to click, race control, troubleshooting
* **Architecture and implementation plan:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
* **Venue equipment** (13 AIDA HD-NDI-IP67 NDI cameras, OBSBOT Tail 2 overhead, vMix, one PC): what it
  changes, what to measure on site, and the marble colour set: [`docs/EQUIPMENT.md`](docs/EQUIPMENT.md)
* **Production-readiness report:** [`docs/readiness-report.html`](docs/readiness-report.html)
* **Windows app for the venue PC** (camera discovery, setup wizard, live tracking, WebSocket leaderboard
  streaming): [`docs/WINDOWS_APP.md`](docs/WINDOWS_APP.md). Run with `python -m marbletrack_app --demo`, or build
  `MarbleTrack.exe` with `windows\build.ps1`
* **Venue simulator** (procedural circuits, billiard balls, the 13 AIDA + OBSBOT cameras rendered and
  tracked through the app's own calibration and tracker; what it found and fixed):
  [`docs/VENUE_SIMULATOR.md`](docs/VENUE_SIMULATOR.md), run with `python scripts/venue_suite.py`
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

* **Every race is different**: each new race draws a fresh seed (shown in the top bar) for the
  starting order, each marble's size (±4 %), grip and wobble, and the whole obstacle course.
  *Restart* replays the same seed exactly; `#alpine/1234` in the URL opens a given race.
* **Physics**: glass marbles roll under gravity (5/7 g·slope for a rolling sphere) in a U-shaped
  channel, with rolling resistance, air drag, wall friction in corners and 3-D marble-to-marble
  collisions (restitution 0.88). They take off where the track falls away faster than gravity can
  follow (v²κ < −g), fly ballistically and bounce on landing. The grid rolls down onto a closed
  start gate that drops at lights out; lap races divert finishers into a collection chute; open
  tracks end in a sand trap and an end wall.
* **Obstacles** (toggle in the top bar): pin fields, bollard chicanes, rotating spinners (3–4 arms,
  random speed and direction), pinball bumpers with a solenoid kick, lane splitters, timed piston
  gates, speed bumps, jump ramps, single-file funnels and gravel strips (a speed-dependent loss).
  Obstacle sections are widened as on real runs. A marble that makes no progress for 5 s is freed
  by a marshal nudge, logged in race control.
* **Five maps**: Serpentine Table, Alpine Descent (hairpins, three tunnels), Spiral Tower (stacked
  levels the overhead camera cannot see), Figure-Eight Bridge and Grand Prix (lap races with a
  chain lift).
* **Camera rigs**: 14 cameras at ~45°, one overhead camera, or both, plus a 240 fps finish camera.
  A simulated detector produces what each camera's edge node would report (resolution-dependent
  noise, occlusion by tunnels, other track levels and other marbles, colour noise).
* **Tracker**: a JavaScript port of `marbletrack/fusion.py` (colour + motion assignment with the
  Hungarian algorithm, Kalman filter on track progress, blind zones, camera health, confirmed
  overtakes, least-squares finish timing, photo-finish flags), scored against the physics live.
  Two rules found by fault-injection testing are in the Python engine too: a marble re-acquired
  after a long gap restarts from its average speed across the gap and needs three fresh
  sightings before gaining places; and a hidden marble's prediction is bounded backwards as well
  as forwards (one knocked back into a tunnel cannot drift out of it unseen).
  The obstacle course adds three simulator-only refinements, each found by a failing race:
  the surveyed obstacle layout raises process noise where a marble can be reversed in one frame
  and widens measurement noise after jump ramps (an airborne marble is mapped with parallax);
  a pass between two marbles whose estimates were just touching (a head-on knock swaps their
  velocities) waits for the 100 ms confirmation; and a visible marble may not pass a hidden one
  before reaching where it was last seen and getting within 5 cm of its prediction.
  The camera simulation adds airborne parallax and occlusion by posts and bumpers.
* **Views**: free orbit with zoom (wheel, buttons, slider, double-click to focus), follow leader,
  follow selected, a TV director that cuts between rig cameras, overhead, finish camera; live
  camera feeds with detection boxes; a clickable minimap coloured by camera coverage.
* **Race control**: F1 start lights, marble count, laps, chaos, obstacles on/off, speed and slow
  motion, instant replay with scrubber, fault injection (kill camera, dim lights, network jitter),
  timing tower with true positions, telemetry (including air height), live tracker-vs-truth
  metrics, palette separation check, a log of course features and physics events (jumps, bumper
  kicks, knock-backs, marshal nudges), and a race history with win counts kept in your browser.
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
| `marbletrack_app/` | Windows desktop app: cameras (USB, Basler, NDI), setup wizard, live runtime, vMix title output, WebSocket client, UI ([guide](docs/WINDOWS_APP.md)) |
| `windows/` | PyInstaller spec, build script, installer, icon |
| `marbletrack/sim.py` | Race simulator, overhead and planar camera rigs |
| `marbletrack/sim3d.py` | 3D pinhole cameras at ~45° with depth buffer: walls, tunnels, perspective, occlusion |
| `marbletrack/race.py`, `marbletrack/edgecases.py` | Scenarios, scripted edge cases, ground-truth scoring |
| `scripts/` | Stress suite, benchmark, live demo, video/3D/report builders |
| `results/` | Measured outputs used by the report |
