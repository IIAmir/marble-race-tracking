# MarbleTrack on the venue equipment

Nothing here is tied to one route: routes change from draw to draw, so the software
learns speeds from the marbles themselves and takes lengths from each saved map (§9).

How the system fits the hardware the venue actually has, what that changes compared with
the reference design in [`ARCHITECTURE.md`](ARCHITECTURE.md) (which assumed GigE Vision
industrial cameras with PTP and a 240 fps finish camera), and what to measure on site.
Figures marked *est.* are calculations from datasheet values and must be confirmed with
the real cameras.

## 1. The equipment

| | Model | What matters for tracking |
|---|---|---|
| Track cameras (×13) | **AIDA Imaging HD-NDI-IP67** | 1/3" progressive CMOS (5.35 × 3.00 mm, 1920×1080), 1080p60, **NDI\|HX** (compressed) plus RTSP/SRT, CS-mount 4 mm lens (6 / 8 / 12 mm available), manual white balance, VISCA over IP, 12 V or PoE, IP67. |
| Overhead camera (×1) | **OBSBOT Tail 2** | 1/1.5" 50 MP sensor, 4K60 and 1080p120, 5× optical zoom (81° horizontal at wide end), **NDI\|HX3**, PoE+ (802.3at, ~30 W), AI auto-tracking PTZ. |
| PC | Ryzen 9 9900X3D (12 cores / 24 threads), 64 GB DDR5, RTX 5080, 2 TB NVMe, **5 GbE**, Windows 11 | Runs vMix and MarbleTrack. |
| Production | **vMix** | Receives every camera over NDI; shows the graphics. |

## 2. What the real track shows (`real_demos/`)

The photo and the video are not from these cameras, but they show what the cameras will see:

- **A room-sized diorama.** The track runs along mountain ridges at several heights, behind
  rock faces, through a parking-lot and golf-course scene and up a lift. A single overhead
  view cannot see all of it: rock overhangs and the stacked levels hide parts of the track.
- **A spiral tower module.** A helical ramp inside a ribbed cylinder, with a vertical lift.
  An angled camera sees several turns of the helix at once. The tracker already handles
  this: each turn is a different track distance, and fusion picks the candidate that fits
  the marble's motion (the simulator's *Spiral Tower* map tests exactly this).
- **Dark grey 3D-printed track.** Good: every coloured ball stands out against it, and the
  empty-track background is stable.
- **White plaster ("snow") right next to the track.** It is the same colour as the balls'
  number discs, the stripes of a striped ball and specular highlights. The track band
  (the dashed area in setup step 3) must stay on the grey channel and off the plaster.
- **The marbles are 25 mm billiard balls**, each with a white number disc. The set (photos of
  the finish rack): 1 yellow, 2 blue, 3 red, 4 purple, 5 orange, 6 teal, 7 maroon, 8 black,
  9 yellow stripe, and a white cue ball. The detector ignores white pixels: a ball is identified
  by its body colour, and the white disc only removes some of its pixels.

## 3. Marble identity: the colour set

Identification is by colour, so **every ball needs a body colour no other ball has**. The
venue set has ten colours, all unique except balls **1 and 9, both yellow** (1 is solid
yellow with a white number disc; 9 is a yellow stripe with white caps).

**Recommendation: replace the 9 with a ball of a colour nobody else has.** The set already
uses yellow, blue, red, orange, maroon, purple, teal, black and white, so the free hues are
**pink/magenta**, **light green/lime** and **light blue/cyan**. Pink/magenta is farthest from
everything in the set. Choose by measurement, not by eye: step 5 of setup shows any pair the
detector could confuse. A stripe ball causes a second problem: its white caps can be read as
the white cue ball.

Telling 1 from 9 by their white areas is possible in principle: a stripe ball shows far
more white next to its yellow than a solid ball, which only shows its number disc. It is
not reliable enough for the requirement of zero identity errors, though:

- a rolling 9 with its band facing the camera looks solid yellow for several frames;
- white also comes from the plaster, from highlights and from neighbouring balls' discs;
- in the overhead view a ball is only ~10–19 px across;
- after the two are hidden together (a pack, a tunnel, the spiral), the tracker could only
  tell them apart again after several clear frames, and could show them swapped meanwhile.

**Second risk, to check before race day: red 3, orange 5 and maroon 7.** The same
palette check that the setup wizard runs was applied to the video (a phone camera with
automatic exposure and white balance, so the numbers are pessimistic):

| Pair (video frame at 6 s, ball bodies only, number discs excluded) | Separation (needs ≥ 50) |
|---|---|
| upper red ball vs. the ball labelled 5 | 9.5 |
| upper red ball vs. lower red ball | 12.6 |
| ball 5 vs. lower red ball | 29 |
| yellow 1 vs. the second (rolling) yellow ball | 15 |
| any red vs. yellow 1 | 250–370 |
| purple 4 vs. any other | > 260 |

In this footage the 5 looks red, not orange. With the AIDA cameras at a locked
exposure and white balance under even light the pairs should separate further, but this
must be measured: if step 5 flags 3/5 or 3/7 on the real rig, replace one of those balls too.
In the well-lit finish-rack photos, 3, 5 and 7 are visibly different (red, orange-red,
maroon), which is encouraging.

Two more pairs to watch in step 5:

- **8 black vs. shadows.** A ball's shadow on the grey track is dark and colourless, like the 8.
  The detector only accepts a blob of a full ball's size and colour, but check on the real rig
  that no shadow is reported as the 8.
- **White cue ball vs. white.** Number discs, highlights and the plaster are also white. The
  plaster is part of the empty-track image, so it is not a problem while the light stays as it
  was when the empty track was captured.

**The finish rack.** The rack at the end of the route (1st to 10th) stacks the balls in order
of arrival. A camera on the rack can read the official order directly, slot by slot, and check
the tracker's result after every race. That also settles a photo finish without a person
reviewing frames.

## 4. Track cameras: AIDA HD-NDI-IP67

**Resolution (4 mm lens, est. 67° horizontal):**

| Camera-to-track distance | Width of view | mm per pixel | 25 mm ball across |
|---|---|---|---|
| 0.4 m | 0.53 m | 0.28 | ~90 px |
| 0.6 m | 0.80 m | 0.42 | ~60 px |
| 1.0 m | 1.34 m | 0.70 | ~36 px |

That is far more detail than identification needs (the stress suite passed with balls
7–29 px across). The constraint is **coverage**: each camera sees about 1.3 × its distance
of track width. Where a route is longer than the cameras can cover, put cameras where order matters
(start, finish, overtaking sections, tunnel and lift exits, the spiral), consider the
6 or 8 mm lens farther away for long straights, and let the overhead camera or a
declared blind zone cover the rest. Positions are held, never guessed, in uncovered stretches.

**Exposure, set in each camera's web page** (MarbleTrack does not change NDI camera settings,
so vMix's picture is never disturbed):

- **A short, fixed shutter.** Motion blur is speed × shutter time. Keep it under about a
  quarter of the ball (6 mm):

  | Ball speed | 1/250 s | 1/500 s | 1/1000 s | 1/2000 s |
  |---|---|---|---|---|
  | 1 m/s | 4 mm | 2 mm | 1 mm | 0.5 mm |
  | 2 m/s | 8 mm | 4 mm | 2 mm | 1 mm |
  | 4 m/s | 16 mm | 8 mm | 4 mm | 2 mm |

  The top speed is not known and changes with the route, so start at 1/1000 s. Then look at a
  paused frame on the fastest stretch: a ball should look round, not like a streak. The live
  screen shows each ball's measured speed. Auto exposure picks long shutters indoors.
- **Fixed gain, fixed (manual) white balance.** Automatic modes shift the colours learnt in
  step 5.
- **Light.** A 1/1000 s shutter needs a lot of light, and mains-powered LEDs flicker at
  100/120 Hz, which shows at short shutters as frames of varying brightness. Use flicker-free
  (DC or high-frequency driven) LED panels, diffuse, fixed.

**Rolling shutter (est.).** A 1/3" CMOS camera at this price is almost certainly
rolling shutter: the bottom row is read up to ~15 ms after the top row. A ball moving at
v m/s is then placed up to v × 15 mm differently depending on its height in the image
(3 cm at 2 m/s). This
does not affect the order of balls close together in the same view, but it causes
disagreement where two cameras overlap and a constant offset at the finish line.
Planned improvement: time-stamp every detection by its image row (needs the sensor's
readout time, measured once with a flashing LED or a fast fan). Speeds are never assumed:
the tracker estimates every ball's speed from its own positions.

## 5. Overhead camera: OBSBOT Tail 2

- **Turn off AI tracking, auto-framing and auto-zoom.** The camera must never move:
  every waypoint is a pixel position. Store the view as a preset, and if the camera is
  ever moved, redo its waypoints and background.
- **Lock focus, exposure (≤ 1/1000 s), gain and white balance**, as for the AIDA cameras.
- **Resolution.** Mounted ~3 m above the layout at the wide end, it sees ~5 m across:
  ~1.3 mm per pixel at 4K, a 25 mm ball ~19 px across (~10 px at 1080p). Send 4K over NDI.
  MarbleTrack now keeps every camera's processing resolution high enough for the smallest
  ball in its view (at least 5 px radius): the old rule would have shrunk the 4K view to
  960×540 and a ball to ~5 px across.
- **What it is for.** It is the only camera that sees the whole layout, so it fills the gaps
  between the AIDA cameras. It cannot see under rock overhangs or into the spiral; those
  stretches are covered by AIDA cameras or declared as blind zones.

## 6. Timing: NDI instead of PTP

The reference design used PTP-synchronised industrial cameras. NDI cameras have no PTP,
and their frames reach the PC after encoding, the network and decoding, with jitter.

- **Timestamps.** NDI frames carry the time the camera sent them, on the camera's own
  clock. MarbleTrack's NDI receiver (`marbletrack_app/ndi.py`, `SenderClock`) maps that clock
  onto the PC clock using the smallest delay seen over the last 10 s: frames keep the
  camera's own spacing (no network jitter), and the camera clocks need no synchronisation.
  In a test with 6 ms of random network jitter the stamps were within 1 ms of the truth
  (`tests/test_equipment.py`).
- **The constant delay** from exposure to sending (encoder latency) is the same for all 13
  AIDA cameras, so it does not change who is ahead. It shifts the race clock, and differs
  between the AIDA and the OBSBOT. Measure it once: point all cameras at one flashing LED,
  record, and enter each camera's offset as *Latency* in setup step 2.
- **Finish timing.** There is no 240 fps finish camera, so finish times come from 60 fps
  track cameras: about ±2–4 ms instead of under 1 ms, and two balls closer than ~6 ms are
  flagged *photo finish* for review instead of being called. If the OBSBOT can send
  1080p120 over NDI, it is the best finish camera available (check in its settings).
- **Latency.** NDI|HX encoding and decoding add a few frames. vMix shows the same frames
  with a similar delay, so the overlay stays in step with the picture. Measure on site
  before committing to the 150 ms target in `ARCHITECTURE.md`.

## 7. One PC for vMix and tracking

Every camera stream is received twice, once by vMix and once by MarbleTrack. Each
receiver decodes it (NDI|HX is H.264/H.265).

- **Network.** 14 NDI|HX streams at 1080p60/4K are well under 1 Gbit/s even counted twice,
  so the 5 GbE port is ample. Use a managed switch with **PoE+ (802.3at) on every port** and a
  budget for 13 AIDA cameras plus the OBSBOT's 30 W, and a 2.5/5/10 GbE uplink to the PC.
- **CPU, measured** (one core of a 2.8 GHz cloud Xeon, simulated frames with ten balls in view;
  `scripts/benchmark.py` style):

  | Camera | Processed at | Per frame | Cores at 60 fps | Cores at 30 fps |
  |---|---|---|---|---|
  | AIDA 1920×1080 | 739×416 | 12.9 ms | 0.77 | 0.39 |
  | OBSBOT 3840×2160 | 1527×859 | 28.2 ms | 1.69 | 0.85 |
  | All 13 AIDA + OBSBOT | | | ~11.7 | ~5.9 |

  Fusion (ranking all balls) adds about 1.5 ms per batch of camera frames: negligible.
  A Ryzen 9 9900X3D core is roughly 1.5–2× faster than this cloud core, so all cameras at 60 fps
  should need about 6–8 of its 12 cores (est.), leaving the rest for NDI decoding and vMix.
  This is feasible but tight; measure it on the venue PC (the live screen shows every camera's
  frame rate and processing time). If it does not fit, in order: process 30 frames per second
  instead of 60, move decoding to the RTX 5080, or run MarbleTrack on a second PC.
  Shrinking frames by a non-whole factor used to cost more than the detection itself
  (16 ms per 1080p frame); it is now done in two fast steps, which halved the per-frame cost
  without changing detections (positions within 0.3 mm).

## 8. Routes and saved maps

Every route is a **map**: cameras, waypoints and their distances, empty-track images, colours
and outputs, saved in its own folder (`%APPDATA%\MarbleTrack\projects\<name>`). The **Maps**
button in the top bar lists every saved map and:

- **Open** a map from an earlier draw, even one from a year ago;
- **Start new route**: the same marbles, cameras and outputs; waypoints, empty track and colours
  are done again;
- **Copy as new route**: start from an existing map, for a route that differs in only a few places;
- **Export / Import .zip**: one file per map, for safekeeping or for another PC.

Two maps never share a folder, even when they have the same name.

**Reopening a map checks the cameras.** Each camera's picture is compared with the empty track
saved in the map (Review step, *Camera views*; it runs by itself when a map is opened):

| Result | Meaning | What to do |
|---|---|---|
| Matches the map | The camera has not moved | Nothing |
| Moved | Nudged or turned since then | *Carry waypoints over*: they are moved with the picture; check them in step 3, then capture the empty track again |
| Different view | Points somewhere else now | Redo its waypoints |

On the real diorama photo, a camera turned by up to 5° and shifted by up to 120 px was
recognised, with its waypoints carried over to within 0.2 px, also with the light 15 % dimmer.
An unrelated picture was rejected. A camera that was *moved to another place* (not just turned)
sees the 3-D layout from a new angle, which no picture transform can follow exactly: check the
carried-over waypoints in step 3 and drag any that are off.

## 9. vMix output

MarbleTrack fills a vMix title directly over the vMix TCP API (port 8099): no browser
source, no data-source polling. Set it up in step 6, *Leaderboard output*.

1. In vMix, enable *Settings → Web Controller* (the TCP API is part of it).
2. Design a GT title with one text field per value, named `P1Name`, `P1Info`, `P2Name`,
   `P2Info`, … (one pair per row), plus `Clock` and `Status`. Optionally a shape per row
   (e.g. `P1Color`) for the ball's colour.
3. Enter the title's name in step 6 and press *Test vMix*. It checks that the title exists and
   has every field.

While live, only fields whose value changed are sent. `Info` is the finish time (with `*`
on a photo finish), the gap to the leader in metres while racing, or `OUT` for a ball lost
for more than 1.5 s. `Status` is `READY`, `LIVE` or `FINAL`. The field names are
patterns (`P{n}Name.Text`) and can be changed to match an existing title.

## 10. What changed in the software for this equipment

| Change | Where |
|---|---|
| NDI cameras found and received (AIDA, OBSBOT, any NDI sender), BGR frames, newest-frame-only, sender-clock timestamps | `marbletrack_app/ndi.py`, `cameras.py` |
| NDI runtime bundled in the Windows build | `windows/requirements-windows.txt`, `windows/MarbleTrack.spec` |
| Processing resolution keeps the smallest ball ≥ 5 px radius (overhead 4K view) | `marbletrack_app/engine.py` |
| vMix title output with change-only updates and a connection test | `marbletrack_app/vmix.py`, step 6 of the wizard |
| Tests: jitter removal, clock steps, processing scale, vMix against a fake vMix server, NDI loopback | `tests/test_equipment.py` |
| Maps: list, open, new route, copy, export/import; camera-view check and waypoint carry-over | `marbletrack_app/maps.py`, *Maps* button, Review step |
| Tests: view check (same / moved / different, light change), zip safety, reopening a map | `tests/test_maps.py` |
| Lap races and cameras that see several stretches (both legs of a hairpin) | `fusion.py`, `calibration.py`, step 3 |
| Shadows, wider channel sections, colour fragments, association by likelihood, matching cascade (found by the venue simulator, see VENUE_SIMULATOR.md) | `detector.py`, `fusion.py`, step 1 |
| Race recording, replay, gate labels and scoring on real races | `recording.py`, `replay.py`, `labels.py`, step 6 |
| Rolling-shutter row timing; board compares marbles at one instant | `detector.py`, `fusion.py`, step 2 |
| Race control: provisional result, identity swap, freeze/hide, manual order; finish-rack camera; estimated finishes | `engine.py`, `rack.py`, live screen |
| Training crops from single-marble runs | `training.py`, live screen |

## 11. Before race day: the venue checklist

The simulator cannot certify the system; real races can. Research on comparable systems (sports
tracking, electronic line calling, racing) points to the same order of work: fix the cameras,
record real races, label them cheaply, measure, then go on air with a human in control.

1. **Cameras to manual.** Shutter, gain, white balance and focus fixed on every camera, settings
   written down. Shutter 1/1000 s or faster (at 3 m/s a 1/60 s shutter smears a 25 mm ball over
   two diameters, which is what fragments its colour).
2. **Light flicker.** Check every camera at the chosen shutter speed for rolling bands from the
   LED fixtures; use flicker-free (constant-current) lights or a shutter that is a whole number of
   mains half-cycles.
3. **NDI bitrate at maximum.** NDI|HX sends colour at half resolution (4:2:0); a 25 px ball has
   ~12 px of colour. Check what the AIDA actually sends.
4. **Sensor readout** entered for every camera (step 2), so each marble is timed by its image row.
5. **Finish.** The finish camera's waypoints reach past the line (step 7 warns otherwise); a
   single-file run-out to the rack; a light board behind the rack slots; a rack camera (WINDOWS_APP.md,
   *Live screen*).
6. **Record 20+ real races on at least two routes** (step 6), plus single-marble runs for training
   data. Keep one route aside, untouched, as the final test.
7. **Label and score** with `marbletrack_app.labels` (gate orders + rack order). Suggested gates:
   no wrong winner in the held-out races, exact final order ≥ 97 %, phantom overtakes ≤ 0.2 per
   race, board order right ≥ 95 % of the time.
8. **Shadow mode, then supervised air.** Run live with the board on a non-air vMix output while
   someone notes every disagreement; then on air with the operator console (confirm result,
   swap, freeze, hide) and the rack check.

## 12. Open questions

1. The replacement colour for the 9.
2. Mounting height of the OBSBOT and how much of the layout it sees.
3. Whether the AIDA's NDI|HX timestamps and shutter settings behave as described above
   (check in its web page; first thing to verify on site).
