# MarbleTrack on the venue equipment

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
- **The marbles are mini billiard balls** (about 25 mm, est. from the hand in the video),
  each with a white number disc. The detector ignores white pixels: a ball is identified
  by its body colour, and the white disc only removes some of its pixels.

## 3. Marble identity: the colour set

Identification is by colour, so **every ball needs a body colour no other ball has**. The
venue set has ten colours, all unique except balls **1 and 9, both yellow** (1 is solid
yellow with a white number disc; 9 is a yellow stripe with white caps).

**Recommendation: replace the 9 with a ball of a colour nobody else has** (for example
pink/magenta or cyan/turquoise, whichever is farthest from the rest of the set), and
choose it by measurement, not by eye (step 5 of setup shows any pair the detector could confuse).

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

## 4. Track cameras: AIDA HD-NDI-IP67

**Resolution (4 mm lens, est. 67° horizontal):**

| Camera-to-track distance | Width of view | mm per pixel | 25 mm ball across |
|---|---|---|---|
| 0.4 m | 0.53 m | 0.28 | ~90 px |
| 0.6 m | 0.80 m | 0.42 | ~60 px |
| 1.0 m | 1.34 m | 0.70 | ~36 px |

That is far more detail than identification needs (the stress suite passed with balls
7–29 px across). The constraint is **coverage**: 13 cameras at ~1 m each see roughly
10–17 m of track. Where the track is longer, put cameras where order matters
(start, finish, overtaking sections, tunnel and lift exits, the spiral), consider the
6 or 8 mm lens farther away for long straights, and let the overhead camera or a
declared blind zone cover the rest. Positions are held, never guessed, in uncovered stretches.

**Exposure, set in each camera's web page** (MarbleTrack does not change NDI camera settings,
so vMix's picture is never disturbed):

- **Shutter ≤ 1/1000 s, fixed.** Motion blur is speed × shutter: at 3 m/s, 1/250 s smears a
  ball by 12 mm (half its width), 1/1000 s by 3 mm. Auto exposure picks long shutters indoors.
- **Fixed gain, fixed (manual) white balance.** Automatic modes shift the colours learnt in
  step 5.
- **Light.** A 1/1000 s shutter needs a lot of light, and mains-powered LEDs flicker at
  100/120 Hz, which shows at short shutters as frames of varying brightness. Use flicker-free
  (DC or high-frequency driven) LED panels, diffuse, fixed.

**Rolling shutter (est.).** A 1/3" CMOS camera at this price is almost certainly
rolling shutter: the bottom row is read up to ~15 ms after the top row. A ball moving at
3 m/s is then placed up to ~4 cm differently depending on its height in the image. This
does not affect the order of balls close together in the same view, but it causes
disagreement where two cameras overlap and a constant offset at the finish line.
Planned improvement: time-stamp every detection by its image row (needs the sensor's
readout time, measured once with a flashing LED or a fast fan).

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
- **CPU.** Measured on a 4-core cloud VM, colour detection takes ~11 ms per 1440×1080
  frame (processed at 720×540): about 0.6 core per camera at 60 fps, ~8–9 cores for 13
  cameras, plus decoding, plus vMix. The 9900X3D has 12 fast cores, so this is feasible
  but tight. Measure it on the venue PC: the live screen shows each camera's frame rate and
  processing time. If it does not fit, the fallbacks in order are: process 30 of the 60
  frames per second, move decoding to the RTX 5080, or move MarbleTrack to a second PC on
  the same switch (vMix then has the main PC to itself).

## 8. vMix output

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

## 9. What changed in the software for this equipment

| Change | Where |
|---|---|
| NDI cameras found and received (AIDA, OBSBOT, any NDI sender), BGR frames, newest-frame-only, sender-clock timestamps | `marbletrack_app/ndi.py`, `cameras.py` |
| NDI runtime bundled in the Windows build | `windows/requirements-windows.txt`, `windows/MarbleTrack.spec` |
| Processing resolution keeps the smallest ball ≥ 5 px radius (overhead 4K view) | `marbletrack_app/engine.py` |
| vMix title output with change-only updates and a connection test | `marbletrack_app/vmix.py`, step 6 of the wizard |
| Tests: jitter removal, clock steps, processing scale, vMix against a fake vMix server, NDI loopback | `tests/test_equipment.py` |

## 10. Open questions

1. The ten colours of the set, and the replacement colour for the 9.
2. Ball diameter (25 mm assumed).
3. Total track length, and where start, finish, tunnels and the lift are.
4. Top speed of a ball on the fastest section.
5. Mounting height of the OBSBOT and how much of the layout it sees.
6. Whether the AIDA's NDI|HX timestamps and shutter settings behave as described above
   (check in its web page; first thing to verify on site).
