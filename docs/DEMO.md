# Showing MarbleTrack

Four things to show, each runs on its own; none needs cameras or internet.

| # | What | How to start | Time |
|---|---|---|---|
| 1 | **The app** (setup wizard, live tracking, race control) on 15 demo cameras | `MarbleTrack (demo cameras)` from the Start menu, or `python -m marbletrack_app --demo` | 5–10 min |
| 2 | **The venue simulator video**: 9 of the 14 venue cameras, the tracker's detections, the live board against the truth | open `results/venue_race.mp4` | 1 min |
| 3 | **The live race simulator** in the browser: the venue routes in a forest landscape, billiard balls, laps, tracker vs truth, failures on demand | open `demo/simulator.html` (double-click) | 3–5 min |
| 4 | **Replay in 3D** of recorded tracker output | open `demo/track3d.html` | 1–2 min |

The user guide for the people who will run it, in Persian with screenshots of every step:
`docs/MarbleTrack-Guide-FA.pdf` (also the *Guide* button in the app).

## 1. The app

**Install** (once):

* Installer: GitHub → *Actions* → *Windows app* → the latest green run → artifact
  *MarbleTrack-windows* → `MarbleTrack-Setup-1.0.0.exe`. The Start menu then has
  *MarbleTrack (demo cameras)*.
* Or from source (Python 3.12, 64-bit):
  ```
  git clone https://github.com/IIAmir/marble-race-tracking
  cd marble-race-tracking
  git checkout claude/clever-babbage-l3ovmj
  py -3.12 -m pip install -r windows\requirements-windows.txt
  py -3.12 -m marbletrack_app --demo
  ```
  (`--browser` opens it in the browser instead of its own window.)

**Script:**

1. **Maps** (top bar): one map per route; open, copy, export. A route used a year ago comes back
   with its cameras and colours; *Check camera views* finds cameras that were moved.
2. **Set up all demo cameras** (top bar, demo only): waypoints, the empty track and the marble
   colours on all 15 cameras in about 30 s. Then walk through the steps on the left to show what
   an installer does on each camera: click marks every 20 cm (step 3), capture the empty track
   (4), click each marble once per camera (5, with the look-alike check), vMix and recording
   (6), the checklist (7).
3. **Go live.** The board updates ~10 times a second; the clock starts at the start line.
   Click a camera tile to enlarge it; the circles are the detections, in each marble's colour.
4. **Race control** (right): when the race is complete it shows *Provisional*; *Confirm result*
   makes it *Final* (in vMix too). Click a marble to swap it with another or move it in the
   result; *Freeze board*, *Hide board*. Everything is logged at the bottom right.
5. **Training data** (under the board): roll one marble alone and every camera collects
   labelled pictures of it for the next stage of the detector.
6. **Back to setup**, step 6: *Record every camera while live* records whole sessions for
   replay and scoring on real races.

## 2. Venue simulator video

`results/venue_race.mp4`, route 6. Left: nine of the fourteen venue cameras (13 AIDA 1080p60
and the OBSBOT overhead in 4K), rendered from a physical simulation of 2.5 cm billiard balls
on a new 37 m route; circles are what the app's detector found. Right: the board the app
produced from all fourteen cameras, with the true rank beside it. Grey rows are marbles in a
stretch no camera sees; they keep their place. For the numbers over seven routes see
`docs/VENUE_SIMULATOR.md`.

## 3. Browser simulator

`demo/simulator.html` (double-click; works offline). It opens on **Venue route 6**, the route of
the venue video, in its landscape: forest, mountains with snow, flowers, rocks, houses, a pond.

* **Map**: *Venue route 1–7* are the venue simulator's circuits (spiral tower, tunnel, obstacle
  zones, finish down the hill, lift back to the start); 1–5 are the older test maps. Keys 1–9
  switch maps.
* **Balls**: *Billiard (venue set)* is the real set (the 1 and the stripe 9 both yellow);
  *Billiard, 9 → pink* is the recommended swap; *Glass marbles* the old look.
* **Laps**: a venue route takes about a minute a lap; choose 1 lap for a short show.
* **Views** (top left): *Follow leader* and *TV director* are the best to show; *Finish cam* for
  the line. *Scenery* (bottom) turns the landscape off.
* *Kill camera*, *Dim lights*, *Network jitter* inject failures while it runs; the *Tracker vs
  truth* panel counts errors live; *Instant replay* goes back in time. Each race has a seed;
  *Restart* replays it exactly.

The browser simulator's cameras report detections from geometry, not from pictures; the
pixel-level test of the tracker is the venue simulator (2. above).

## What to say about accuracy

* Simulated venue, seven routes (three never used for tuning): the final order was right in
  all seven, ranks of visible marbles right 98.7–100 % of the time, finish times within 2–9 ms.
* The independent stress suite (66 races): no wrong order, no identity errors.
* The simulator cannot certify the system: the next step is recording 20+ real races at the
  venue and scoring them (`docs/EQUIPMENT.md` §11). The result shown on air stays provisional
  until confirmed, and the finish-rack camera checks the order independently.
