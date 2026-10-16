"""Project configuration: everything the setup wizard produces, saved as JSON.

A project lives in its own folder (``project.json`` plus one empty-track
background PNG per camera). Paths default to ``%APPDATA%\\MarbleTrack`` on
Windows and ``~/.marbletrack`` elsewhere.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

SCHEMA_VERSION = 1

# Default marble set: the same ten colours the stress suite was run with.
DEFAULT_MARBLES = [
    ("Ruby", "#d72a2d"), ("Amber", "#f5821f"), ("Sun", "#ebd728"), ("Leaf", "#28aa3c"), ("Teal", "#1eb4be"),
    ("Sky", "#3c96eb"), ("Navy", "#1e2896"), ("Violet", "#8232aa"), ("Rose", "#f050aa"), ("Snow", "#f5f5f5"),
]


def app_dir() -> Path:
    base = os.environ.get("MARBLETRACK_HOME")
    if base:
        return Path(base)
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "MarbleTrack"
    return Path.home() / ".marbletrack"


def slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", text.strip()).strip("-.")
    return s[:60] or "project"


@dataclass
class MarbleDef:
    id: str
    name: str
    color: str  # display colour (#rrggbb); identification uses the calibrated models, not this


@dataclass
class ColorSample:
    """A marble's colour model as one camera sees it (CIELAB Gaussian)."""

    mean: list[float]
    cov_inv: list[list[float]]
    median: list[float] | None = None  # median Lab of the sampled blobs (what the detector compares)
    pixels: int = 0
    clicks: int = 0
    copied_from: str | None = None  # camera key when copied rather than measured


@dataclass
class CameraSetup:
    key: str  # backend-qualified id, e.g. "dshow:0", "pylon:40012345", "demo:cam03"
    backend: str
    device: str
    name: str
    enabled: bool = True
    role: str = "section"  # section | finish
    width: int = 0
    height: int = 0
    fps: float = 30.0
    auto_exposure: bool = False
    exposure: float | None = None  # backend units (DirectShow: log2 seconds, e.g. -6)
    auto_white_balance: bool = False
    latency_ms: float = 0.0  # capture-to-timestamp delay to subtract (USB cameras: ~30-60 ms), to the middle row
    readout_ms: float = 0.0  # rolling shutter: time from the first to the last row (0 = global shutter / unknown)
    # Track waypoints: image points (native pixels) of distance marks, in race direction.
    markers: list[list[float]] = field(default_factory=list)
    marker_s: list[float] = field(default_factory=list)  # track distance (m) of each marker
    # Marker indices that start a new stretch: a camera may see separate parts of the track
    # (both legs of a hairpin, a bridge over a lower pass). Click stretches in race order.
    breaks: list[int] = field(default_factory=list)
    width_scale: float = 1.0  # track band (mask) width relative to the width implied by the marks
    marble_radius_px: float = 0.0  # median measured during colour calibration (for display)
    radius_scale: float = 1.0  # measured marble size / size implied by the marks (perspective model)
    palette: dict[str, ColorSample] = field(default_factory=dict)
    background_file: str | None = None

    @property
    def s_range(self) -> tuple[float, float] | None:
        return (self.marker_s[0], self.marker_s[-1]) if len(self.marker_s) >= 2 else None

    @property
    def stretches(self) -> list[tuple[int, int]]:
        """(first, last) marker index of each stretch. A stretch also starts where the distance
        jumps (a hidden part of the track was skipped), even without an explicit break."""
        n = len(self.marker_s)
        if n == 0:
            return []
        steps = np.diff(self.marker_s) if n > 1 else np.zeros(0)
        typical = float(np.median(steps)) if len(steps) else 0.0
        cuts = {i for i in self.breaks if 0 < i < n}
        cuts |= {i + 1 for i, d in enumerate(steps) if d > max(0.6, 4 * typical)}
        starts = [0] + sorted(cuts)
        ends = [c - 1 for c in sorted(cuts)] + [n - 1]
        return list(zip(starts, ends))

    @property
    def s_ranges(self) -> list[tuple[float, float]]:
        """Track covered by this camera: one (lo, hi) per stretch of at least two waypoints."""
        return [(self.marker_s[a], self.marker_s[b]) for a, b in self.stretches if b > a]


@dataclass
class ServerSetup:
    url: str = ""
    token: str = ""
    station_id: str = "track-1"
    publish_hz: float = 10.0
    enabled: bool = True


@dataclass
class VmixSetup:
    """Leaderboard into a vMix title over the vMix TCP API (see ``marbletrack_app/vmix.py``)."""

    enabled: bool = False
    host: str = "127.0.0.1"  # vMix on the same PC
    port: int = 8099
    input: str = "Leaderboard"  # title input: its name, number or key
    rows: int = 0  # leaderboard rows in the title; 0 = one per marble
    name_field: str = "P{n}Name.Text"  # GT titles name text fields "<name>.Text"
    info_field: str = "P{n}Info.Text"  # finish time, or gap to the leader while racing
    color_field: str = ""  # e.g. "P{n}Color.Fill.Color" for a colour swatch shape; empty = not used
    clock_field: str = "Clock.Text"
    status_field: str = "Status.Text"  # READY / LIVE / FINAL
    lap_field: str = ""  # e.g. "Lap.Text": the leader's lap, "LAP 2/3" (lap races only); empty = not used


@dataclass
class RecordSetup:
    """Record every camera while live, for replay and for validating the tracker on real races."""

    enabled: bool = False
    folder: str = ""  # empty: "recordings" inside the map's folder
    jpeg_quality: int = 92  # frames are stored as Motion-JPEG (one file per camera)


@dataclass
class Project:
    name: str = "My marble race"  # the map's name: one project per route
    notes: str = ""  # free text, e.g. which draw / route this map is for
    saved_at: str = ""  # UTC, set on every save
    track_length: float = 0.0  # metres, start gate to end of run-out (a circuit: one lap, start line to start line)
    laps: int = 1  # more than one: the track is a circuit and the race runs this many laps
    finish_s: float = 0.0  # metres from the start gate to the finish line
    start_s: float = 0.1  # race clock starts when the first marble passes this distance
    blind_zones: list[list[float]] = field(default_factory=list)  # tunnels etc. [[a, b], ...]
    marker_spacing: float = 0.2  # default distance between waypoint marks (m)
    track_width_m: float = 0.05  # inside width of the running channel
    # Sections where the channel is wider (obstacle zones, splitters): [[from_m, to_m, width_m], ...]
    wide_zones: list[list[float]] = field(default_factory=list)
    marble_diameter_mm: float = 25.0
    marbles: list[MarbleDef] = field(default_factory=list)
    cameras: list[CameraSetup] = field(default_factory=list)
    server: ServerSetup = field(default_factory=ServerSetup)
    vmix: VmixSetup = field(default_factory=VmixSetup)
    record: RecordSetup = field(default_factory=RecordSetup)
    auto_new_race: bool = True  # start a new race when the track has been empty for a while
    schema: int = SCHEMA_VERSION

    @classmethod
    def new(cls) -> "Project":
        return cls(marbles=[MarbleDef(n.lower(), n, c) for n, c in DEFAULT_MARBLES])

    # -- helpers --------------------------------------------------------------
    def width_at(self, s) -> np.ndarray:
        """Inside channel width (m) at track distance ``s`` (within a lap)."""
        s = np.asarray(s, float)
        w = np.full(s.shape, float(self.track_width_m))
        for a, b, wz in self.wide_zones:
            w = np.where((s >= a) & (s <= b), np.maximum(w, wz), w)
        return w

    def camera(self, key: str) -> CameraSetup:
        for c in self.cameras:
            if c.key == key:
                return c
        raise KeyError(key)

    @property
    def active_cameras(self) -> list[CameraSetup]:
        return [c for c in self.cameras if c.enabled]

    def coverage(self, step: float = 0.01) -> dict:
        """Which stretches of the track no enabled camera covers (become blind zones)."""
        L = self.track_length
        if L <= 0:
            return {"gaps": [], "covered_frac": 0.0}
        s = np.arange(0.0, L + step / 2, step)
        seen = np.zeros(len(s), bool)
        for c in self.active_cameras:
            if c.role in ("section", "overhead"):
                for lo, hi in c.s_ranges:
                    seen |= (s >= lo) & (s <= hi)
        gaps, start = [], None
        for x, ok in zip(s, seen):
            if not ok and start is None:
                start = x
            if ok and start is not None:
                gaps.append([round(start, 3), round(x, 3)])
                start = None
        if start is not None:
            gaps.append([round(start, 3), round(L, 3)])
        return {"gaps": gaps, "covered_frac": float(seen.mean())}

    # -- persistence -------------------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        d = dict(d)
        if d.get("schema", SCHEMA_VERSION) > SCHEMA_VERSION:
            raise ValueError("project was saved by a newer version of MarbleTrack")
        def known(kind, x: dict) -> dict:  # maps are kept for years: ignore fields this version does not know
            return {k: v for k, v in x.items() if k in kind.__dataclass_fields__}

        cams = []
        for c in d.pop("cameras", []):
            c = known(CameraSetup, c)
            c["palette"] = {k: ColorSample(**known(ColorSample, v)) for k, v in c.get("palette", {}).items()}
            cams.append(CameraSetup(**c))
        return cls(
            marbles=[MarbleDef(**known(MarbleDef, m)) for m in d.pop("marbles", [])],
            server=ServerSetup(**known(ServerSetup, d.pop("server", {}))),
            record=RecordSetup(**known(RecordSetup, d.pop("record", {}))),
            vmix=VmixSetup(**{k: v for k, v in d.pop("vmix", {}).items() if k in VmixSetup.__dataclass_fields__}),
            cameras=cams,
            **{k: v for k, v in d.items() if k in cls.__dataclass_fields__},
        )


def save_project(project: Project, folder: Path) -> Path:
    """Atomic save: write to a temp file, then replace (a crash never leaves half a file)."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "project.json"
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".project-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(project.to_dict(), f, indent=2)
    os.replace(tmp, path)
    return path


def load_project(folder: Path) -> Project:
    with open(folder / "project.json", encoding="utf-8") as f:
        return Project.from_dict(json.load(f))
