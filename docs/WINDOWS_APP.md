# MarbleTrack for Windows

A desktop application for the race venue PC. It finds the connected cameras and walks an
operator through setup step by step. It then runs the real-time tracking engine
(`marbletrack`) on every camera, fills a vMix title with the live leaderboard and can stream it
to your backend over a WebSocket. How this fits the venue's cameras and PC:
[`EQUIPMENT.md`](EQUIPMENT.md).

```
cameras ──► one process per camera ─────► fusion thread ──► live screen
            (grab, colour detection)       (FusionEngine)      └► WebSocket client ──► your backend
```

## Install and run

| | |
|---|---|
| **Installer** | `MarbleTrack-Setup-1.0.0.exe`, built by CI (*Actions → Windows app → artifact*) or by `windows\build.ps1 -Installer`. It creates Start-menu entries for MarbleTrack, MarbleTrack (demo cameras) and the log folder. |
| **Portable** | Copy the `dist\MarbleTrack` folder and run `MarbleTrack.exe`. |
| **From source** | `pip install -r windows\requirements-windows.txt`, then `python -m marbletrack_app` |

Requirements:

- **Windows 10 or 11 (64-bit).**
- **Microsoft Edge WebView2 Runtime** for the app window. Windows 11 includes it, and the installer offers it when missing. Without it, MarbleTrack opens in the default browser instead.
- **CPU:** about 0.3 cores per 30 fps camera, so 14 cameras plus a finish camera fit an 8-core PC (16 threads).
- **NDI cameras** (AIDA HD-NDI-IP67, OBSBOT Tail 2, any NDI or NDI|HX sender): supported out of the box; the NDI
  runtime comes with `cyndilib`. Allow MarbleTrack through the Windows firewall (private network) so that
  NDI discovery works.
- **Basler cameras:** they also need pylon and `pypylon` (uncomment it in `requirements-windows.txt` before building).

Command-line options:

| Option | Effect |
|---|---|
| `--demo` | Also offer 15 virtual cameras from the race simulator: the whole app works without hardware. |
| `--browser` | Use the default browser instead of the app window. |
| `--home DIR` | Data folder; default `%APPDATA%\MarbleTrack`. |
| `--selftest` | Headless end-to-end check, exit code 0 on success (used by the build). |

Projects are saved automatically to `%APPDATA%\MarbleTrack\projects\<name>\`. Each project is a
`project.json` plus one empty-track PNG per camera. Logs are in `%APPDATA%\MarbleTrack\logs`.

## The setup wizard

The left rail shows the seven steps and what each one still needs. You can move between steps
freely; **Go live** unlocks when every step is complete.

1. **Race & marbles.** Enter the track length, where the finish line is, and the
   waypoint-mark spacing (default 20 cm). Then add the marbles, with a name and a display
   colour each. Under *More track settings* are the start line for the race clock, the channel
   width, the marble size, tunnels (blind zones) and automatic new races.
2. **Cameras.** Cameras are found automatically: USB/UVC via DirectShow, Basler via pylon, and NDI cameras
   on the network. NDI cameras keep the exposure, shutter and white balance set in their own web page
   (set a fixed shutter of 1/1000 s or faster there); MarbleTrack never changes them, so vMix's picture is
   not disturbed.
   For each camera you can:
   - switch it on or off;
   - set its role, *Track section* or *Finish line*;
   - put it in order along the track with the arrows;
   - under *Exposure & colour*, lock exposure and white balance. Automatic exposure shifts the
     colours the tracker learned.
3. **Track waypoints.** Put small marks on the track every 20 cm. In each camera, click the
   marks in race direction, at marble-centre height: rest a marble on the mark and click its
   centre. A magnifier follows the cursor.
   - Each mark's distance fills itself in, continuing from the previous camera. Click a label
     to change it, drag a waypoint to move it, or press Ctrl+Z to undo.
   - The dashed blue band shows where the tracker will look; a slider sets its width.
   - *Detail* shows how many millimetres of track one pixel covers, and warns where a camera is
     too far away.
   - The coverage bar shows every camera's stretch and the gaps between them (hatched).
   - The app joins the marks with a smooth curve and interpolates distances between them with
     a projective (perspective-correct) fit. A camera looking along the track therefore needs
     no denser marks.
4. **Empty track.** Remove the marbles and press *Capture all cameras*. Each camera stores a
   median background image, and the app warns if anything moved during the capture.
5. **Marble colours.** Put the marbles where the camera sees them, not touching. Pick a marble
   on the right and click it in the picture. The app outlines the blob it learned and moves on
   to the next marble.
   - Each camera learns its own colours, because lighting differs.
   - A pair the detector could confuse is flagged with advice. The check uses the same rule as
     the detector: would marble A's blob pass marble B's colour gate?
   - *Copy missing colours from…* is a fallback for cameras that cannot see every marble.
6. **Leaderboard output.**
   - *Fill a vMix title*: the vMix computer (127.0.0.1 on the same PC), the title's name and its field
     names (`P{n}Name.Text`, `P{n}Info.Text`, `Clock.Text`, `Status.Text` by default; `{n}` is the row).
     *Test vMix* checks that vMix answers, that the title exists and that it has every field. See
     [EQUIPMENT.md §8](EQUIPMENT.md#8-vmix-output) for the title design.
   - *Stream to a server*: the WebSocket address (`ws://` or `wss://`), an optional
     access token and a station name. *Test connection* explains failures in plain words
     (refused, timeout, DNS, rejected token).
7. **Review & go live.** Shows a checklist with *Fix* links and the coverage bar.

## Live screen

- **Leaderboard and clock.**
  - Rows slide when positions change.
  - Hidden marbles are dimmed.
  - Finish times appear in amber.
  - Photo finishes are marked for review.
- **Cameras.**
  - Click a thumbnail to make it the large view.
  - Detections are drawn in each marble's colour.
  - A camera that stops is marked red, and the tracker treats its stretch as a blind zone.
- **Status bar.**
  - Camera health shows how many are online, their frame rates and the slowest processing time.
  - Server connection shows connected or reconnecting, and messages sent.
  - vMix shows connected or reconnecting, field updates sent, and any field vMix rejected.
  - The race-control log lists starts, finishes and camera problems.
- **Race control.**
  - The clock starts when the first marble passes the start line.
  - A race is complete when every marble has finished, or 15 s after the first finish with
    nothing moving.
  - With *automatic new races*, marbles returning to the track after it was clear start a new
    race. *New race* does this at once.

## WebSocket protocol (`marbletrack.v1`)

The app is a client: it connects to your server and sends JSON text frames. It reconnects with
backoff from 0.5 s up to 10 s.

- **Leaderboard snapshots** are sent at the publish rate (10/s by default). After a reconnect
  you get the current board, not a backlog.
- **Race events** are queued, up to 1,000 while offline, and delivered in order.
- **Common fields** on every message: `schema`, `type`, `station_id`, `seq` (increasing per
  connection) and `sent_at` (UTC). The token is sent as `Authorization: Bearer <token>`.

`hello` (first message of every connection):
```json
{"schema":"marbletrack.v1","type":"hello","station_id":"track-1","seq":1,"sent_at":"2026-10-14T18:03:10.002Z",
 "app_version":"1.0.0","project":"Saturday cup","track_length":13.51,"finish_s":13.31,
 "marbles":[{"id":"ruby","name":"Ruby","color":"#d72a2d"}],
 "cameras":[{"key":"opencv:0","name":"Gate","role":"section"}]}
```

`leaderboard`:
```json
{"schema":"marbletrack.v1","type":"leaderboard","station_id":"track-1","seq":412,"sent_at":"…",
 "race_id":"3f2a9c1b77de","race_state":"running","race_time":12.418,"clock_estimated":false,
 "board":[{"rank":1,"marble_id":"ruby","name":"Ruby","color":"#d72a2d","status":"tracking",
           "progress_m":9.214,"speed_mps":1.42,"gap_m":0.0,"finish_time":null,"photo_finish":false}],
 "cameras":{"opencv:0":true}}
```

The fields mean:

- `race_state`: `waiting`, `running` or `complete`.
- `status`: one of
  - `tracking`: seen now;
  - `acquiring`: just appeared, its speed is not yet trusted;
  - `coasting`: hidden, and holding its place;
  - `lost`: not seen for 1.5 s;
  - `unseen`;
  - `finished`.
- `finish_time`: seconds from the race start.
- `clock_estimated`: true when tracking began after the race had started.

`race_event` (`event` is one of `race_started`, `finish`, `race_complete`, `new_race`,
`camera_offline`, `camera_online`, `camera_error`):
```json
{"schema":"marbletrack.v1","type":"race_event","event":"finish","station_id":"track-1","seq":413,"sent_at":"…",
 "race_id":"3f2a9c1b77de","marble_id":"ruby","name":"Ruby","rank":1,"time":12.4183}
```

A minimal reference server that prints what arrives is a good start for your backend:
`python -m marbletrack_app.ws_receiver --port 8765 --token SECRET`.

## Building

`windows\build.ps1` does everything on a Windows machine:

1. creates a virtual environment and installs the dependencies;
2. runs the unit tests;
3. builds `dist\MarbleTrack\MarbleTrack.exe` with PyInstaller (`windows\MarbleTrack.spec`, a
   one-folder windowed build);
4. runs the built exe's `--selftest`;
5. with `-Installer`, builds `dist\MarbleTrack-Setup-1.0.0.exe` with Inno Setup 6.

CI does the same on every push (`.github/workflows/windows-app.yml`) and publishes the exe,
the installer and the selftest report as artifacts.

The self-test runs the whole chain on the demo cameras:

1. discovery;
2. waypoints;
3. background;
4. colour sampling;
5. live tracking in 15 camera processes;
6. WebSocket streaming to an in-process server.

It checks that every stage works; accuracy is the stress suite's job.

## Under the hood

| Module | Role |
|---|---|
| `marbletrack_app/cameras.py` | Discovery (OpenCV/DirectShow, pypylon, demo), frame sources, setup-time grab threads (idle cameras drop to 2 frames/s) |
| `marbletrack_app/calibration.py` | Clicks → `CameraCalibration`: spline centerline with projective distance interpolation, perspective-scaled track band and marble size, click-to-blob segmentation, robust colour fit (specular glints excluded), palette check |
| `marbletrack_app/engine.py` | Camera processes (spawned; the same on every OS) and the fusion thread: `ReorderBuffer` → `FusionEngine`, race sessions, previews with detections drawn |
| `marbletrack_app/ndi.py` | NDI discovery and receiver; `SenderClock` maps camera timestamps to the PC clock without network jitter |
| `marbletrack_app/vmix.py` | vMix TCP API client: fills a title, sends only changed fields, reconnects; connection test |
| `marbletrack_app/ws_client.py` | WebSocket client: reconnects, coalesces boards, queues events, explains errors |
| `marbletrack_app/app.py`, `server.py` | Controller and local server (127.0.0.1 only; every call needs the per-launch token) |
| `marbletrack_app/web/` | The UI (no build step, no external downloads: works offline at the venue) |

## Measured accuracy of setup by clicking

The test is `tests/test_app.py::test_calibration_from_clicks_tracks_accurately`:

- it uses demo cameras at 640×480;
- it places waypoints with 1 px of simulated hand jitter;
- it takes one click per marble colour.

Every detection was identified correctly. The median position error was under 4 mm, and the
95th percentile under 20 mm.

The tail comes from where a camera sees the track far away, at about 5 mm per pixel. The
*Detail* readout in step 3 shows this for every camera, and higher-resolution cameras shrink it
proportionally.

The stress suite's angled-camera accuracy (p95 ≤ 3 mm) used exact calibration with cross-track
normals, which the click wizard does not collect yet.
