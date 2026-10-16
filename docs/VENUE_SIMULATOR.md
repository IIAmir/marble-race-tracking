# Venue simulator

A test bench that films whole races the way the venue will: a procedural
diorama circuit, 2.5 cm billiard balls rolling under real physics, the 13 AIDA
track cameras and the OBSBOT overhead with their optics, and the app's own
calibration, detector, fusion and leaderboard code in between. Every
rendered frame goes through exactly the code path of the Windows app; only the
pixels and the clock are simulated.

It exists to find what the earlier stress suite could not: problems that need
real 3-D geometry (cast shadows, one camera seeing two passes of the track,
balls bounced back into a stretch no camera sees), billiard-ball appearance
(white number discs, a black ball, shading, stripes) and the venue's actual
camera layout.

```
python scripts/venue_suite.py --seeds 1 2 3 --scenarios venue --cache cache/   # races, results/venue.json
python scripts/venue_suite.py --stills 1                                       # results/stills/venue_cameras.jpg
```

A race of ~38 s renders and tracks 32,000 frames (14 cameras × 60 fps); on a
4-core machine that takes about 18 minutes. The live system does the same work
in real time (see EQUIPMENT.md §7 for the per-frame cost on the venue PC).

## What is simulated

| Part | Model |
|---|---|
| Route (`venue/course.py`) | A new closed circuit per seed, 30–38 m a lap: hairpins, chicanes, sweepers, wiggles, a pretzel loop, drops, a helix tower, a tunnel, obstacle zones (pins, bumpers, splitters, spinners, speed bumps, funnels) and wider channels in those zones; the finish, a diverter into the finish rack, and a lift back to the start for lap races. No fixed lengths or speeds anywhere. |
| Balls (`venue/physics.py`) | 2.5 cm, rolling (5/7 g on slopes), wall scrub, banking, ball–ball collisions, pins, bumpers, spinners, hops; the leader can lap others; marshals nudge a ball that stays stuck. Speeds come from the slopes only. |
| Ball set | The real set (1 yellow, 9 yellow stripe) or the recommended set with 9 replaced by a pink solid. Numbers are real glyphs on white discs; the 9 is a stripe. |
| Scenery (`venue/scene.py`, `shading.py`) | Terrain with embankments, rock, moss, flowers, trees, a house, a parking lot; sun-like key light with a shadow map, ambient and sky light; procedural textures. |
| Cameras (`venue/camera.py`) | AIDA HD-NDI-IP67: 1080p60, 4 mm lens (67.5° HFOV), barrel distortion, 1/1000 s exposure, 14.5 ms rolling-shutter readout, vignetting, shot and read noise, 4:2:0 JPEG at the NDI|HX bit budget. OBSBOT Tail 2: 4K60 wide, looking straight down. White balance and frame phase differ per camera. |
| Rig (`venue/rig.py`) | Placed the way an installer would: a close finish camera (last 40 cm before the line), a start camera, then the remaining cameras greedily for coverage at ≥ 24 px per ball, terrain occlusion included. Gaps can remain, as on a real layout. |
| Timing (`venue/run.py`) | Each camera stamps frames with its own clock and encoder latency; the network adds delay and jitter; the app's `SenderClock` maps the stamps; the latency measured on site (1 ms error) is subtracted. Frames are dropped at 0.2 %. |
| Operator | Waypoints clicked every 20 cm on each visible stretch (1 px hand jitter, 2 mm tape error, a new stretch per separate pass), the route's wider sections entered once, the band-width slider set from what the camera shows, the empty track captured over a second, each ball clicked three times, all through `marbletrack_app.calibration`. |

Ground truth is the physics state: every leaderboard is scored against it at
30 Hz with the same `Scorer` the stress suite uses (rank accuracy of visible
pairs, phantom overtakes, identity errors, finish times, official order, and
whether the final board matches the order of the balls in the finish rack).

## What it found, and what changed in the tracker

Each problem below showed up in venue races, was traced to a frame and a cause,
and fixed by a rule that holds on any route, not by tuning to one race. Each fix
was then checked against the original stress suite (independent renderer), which
caught one over-eager first version (see 3).

1. **Cast shadows were the black ball.** A ball's shadow on the track is dark
   and neutral, so it matched the 8; the real 8 then lost to the bigger shadow
   blob. *Fix*: a pixel that is the empty-track background darkened by a common
   factor, its colour kept, is a shadow, not foreground (`suppress_shadows`).
   The black ball is much darker than any shadow relative to the surface below it.
2. **A camera saw a ball on another pass of the track.** Where the channel is
   wider (obstacle zones), one band width for the whole camera made the band
   swallow a neighbouring pass. *Fix*: the route's wider sections are entered
   once (`Project.wide_zones`, "6.5–7.0 @ 105" in step 1), the band and the
   lateral limit follow the width at each distance.
3. **One ball, two colour fragments.** Shading, motion blur or chroma blur at
   the overhead camera's 2 mm/px made part of the orange 5 match the red 3; the
   fragment then went to another ball. *Fix*: a fragment near the bigger piece
   whose colour is still a plausible shade of that marble is merged into it.
   (A first version merged by size and distance only; the stress suite's tight
   packs showed it merging touching balls, so colour is now required.)
4. **Lost balls took blobs cheaply.** Association compared Mahalanobis distances
   only, so a ball unseen for a while (wide uncertainty) matched almost anything.
   *Fix*: the cost is the negative log-likelihood, with the log of the innovation
   variance; a blob whose colour clearly belongs to a ball known to be elsewhere
   is that ball seen at the wrong place, not another ball.
5. **Measurements far behind a ball.** A detection 1.4 m behind a ball's last
   position (another pass of the track) was accepted once the ball's uncertainty
   had grown. *Fix*: balls do not jump backwards beyond a bounce (25 cm past the
   coasting floor).
6. **Uncertainty in places no camera sees.** A ball inside an unseen stretch was
   given unbounded uncertainty, though it can only be within that stretch.
   *Fix*: its position variance is bounded by the interval it must be in.

## Results

See `results/venue.json` (written by the suite) and the summary in
EQUIPMENT.md §12. The figures there are reported as they came out, including
the races that were not tuned on.

## Limits: what the simulator cannot prove

The simulator and the tracker were written by the same team, so a blind spot in
one may be shared by the other. Things it does not model: specular reflections
of the room on the balls, LED lighting flicker, hands and people in view,
camera vibration, the real NDI|HX encoder's artefacts, and the exact paint of
the real balls. The shadow test's threshold (shadows keep ≥ 42 % of the
surface's brightness) suits the soft light of an indoor venue, and is the one
number most worth checking on real footage. A few minutes of raw recording from
two or three AIDA cameras with the real balls is the test that settles it.
