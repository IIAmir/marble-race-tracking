"""Saved maps: one route (camera setups, waypoints, backgrounds, colours) per folder.

Routes change from draw to draw and old ones come back, sometimes a year later.
Every map is a self-contained folder under ``<home>/projects`` (``project.json``
plus one empty-track PNG per camera), so it can be reopened, copied as the
starting point of a new route, or exported as a single ``.zip`` for safekeeping.

Reopening a map does not assume the cameras are where they were. ``view_change``
compares each camera's live picture with the empty-track image saved in the map:

* ``same``     the view matches; the map can be used as it is;
* ``moved``    the camera was nudged or turned: the saved waypoints are carried
               over to the new view with the image transform found between the two
               pictures (then the empty track is captured again);
* ``changed``  the camera now looks at something else: redo its waypoints.
"""
from __future__ import annotations

import io
import json
import logging
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from .config import Project, slug

log = logging.getLogger("marbletrack.maps")

MAX_IMPORT_BYTES = 512 * 2**20


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def projects_dir(home: Path) -> Path:
    return home / "projects"


def unique_folder(home: Path, name: str) -> Path:
    base = projects_dir(home) / slug(name)
    folder, n = base, 2
    while folder.exists():
        folder = base.with_name(f"{base.name}-{n}")
        n += 1
    return folder


def summary(folder: Path) -> dict | None:
    try:
        with open(folder / "project.json", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return None
    cams = d.get("cameras", [])
    return {
        "folder": str(folder),
        "name": d.get("name") or folder.name,
        "notes": d.get("notes", ""),
        "saved_at": d.get("saved_at", ""),
        "track_length": d.get("track_length", 0.0),
        "marbles": len(d.get("marbles", [])),
        "cameras": sum(1 for c in cams if c.get("enabled", True)),
        "cameras_set_up": sum(1 for c in cams if c.get("enabled", True) and len(c.get("markers", [])) >= 2),
    }


def list_maps(home: Path) -> list[dict]:
    root = projects_dir(home)
    out = [s for f in sorted(root.iterdir()) if f.is_dir() and (s := summary(f))] if root.exists() else []
    return sorted(out, key=lambda s: s["saved_at"] or "", reverse=True)


def copy_map(src: Path, home: Path, new_name: str) -> Path:
    dst = unique_folder(home, new_name)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".project-*"))
    return dst


def export_zip(folder: Path, out_dir: Path) -> Path:
    """The map folder as one zip: ``<name>-<date>.zip`` in ``out_dir``."""
    s = summary(folder)
    if s is None:
        raise ValueError("this folder holds no saved map")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{slug(s['name'])}-{datetime.now().strftime('%Y%m%d-%H%M')}.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(folder.iterdir()):
            if f.is_file() and not f.name.startswith(".project-"):
                z.write(f, f.name)
    return path


_SAFE = re.compile(r"^[A-Za-z0-9._-]+$")


def import_zip(data: bytes, home: Path) -> Path:
    """Unpack an exported map into a new folder. Only flat, plainly named files are accepted."""
    if len(data) > MAX_IMPORT_BYTES:
        raise ValueError("file too large to be a MarbleTrack map")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("not a zip file")
    names = [i.filename for i in z.infolist() if not i.is_dir()]
    if "project.json" not in names:
        raise ValueError("the zip has no project.json: not a MarbleTrack map")
    for n in names:
        if not _SAFE.match(n):
            raise ValueError(f"unexpected file in the zip: {n}")
    project = Project.from_dict(json.loads(z.read("project.json")))  # validates before anything is written
    folder = unique_folder(home, project.name)
    folder.mkdir(parents=True)
    for n in names:
        (folder / n).write_bytes(z.read(n))
    return folder


# ---------------------------------------------------------------------------- view check
def _gray(img: np.ndarray, k: float) -> np.ndarray:
    """Grey, scaled by ``k``, contrast-equalised (tolerant to a change of light), lightly blurred."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    if k < 1:
        g = cv2.resize(g, (round(g.shape[1] * k), round(g.shape[0] * k)), interpolation=cv2.INTER_AREA)
    g = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(g)
    return cv2.GaussianBlur(g, (0, 0), 1.2)


def _scaled(H: np.ndarray, k: float) -> np.ndarray:
    """A homography found on images scaled by ``k``, expressed in full-size pixels."""
    S = np.diag([k, k, 1.0])
    return np.linalg.inv(S) @ H @ S


def _orb(a: np.ndarray, b: np.ndarray) -> np.ndarray | None:
    orb = cv2.ORB_create(4000)
    ka, da = orb.detectAndCompute(a, None)
    kb, db = orb.detectAndCompute(b, None)
    if da is None or db is None or len(ka) < 20 or len(kb) < 20:
        return None
    good = [m for m, *rest in cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(da, db, k=2)
            if rest and m.distance < 0.8 * rest[0].distance]
    if len(good) < 15:
        return None
    H, inl = cv2.findHomography(np.float32([ka[m.queryIdx].pt for m in good]),
                                np.float32([kb[m.trainIdx].pt for m in good]), cv2.RANSAC, 3.0)
    return H if H is not None and inl.sum() >= 12 else None


def _ecc(a: np.ndarray, b: np.ndarray, init: np.ndarray | None = None) -> np.ndarray | None:
    """Direct alignment of the two pictures, coarse to fine: works on smooth, low-texture views."""
    W = np.eye(3, dtype=np.float32) if init is None else init.astype(np.float32)
    crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 200, 1e-6)
    for f in (0.25, 0.5, 1.0):
        sa = cv2.resize(a, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1 else a
        sb = cv2.resize(b, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1 else b
        S = np.diag([f, f, 1.0]).astype(np.float32)
        Wf = S @ W @ np.linalg.inv(S)
        try:
            _, Wf = cv2.findTransformECC(sa.astype(np.float32), sb.astype(np.float32), Wf, cv2.MOTION_HOMOGRAPHY, crit, None, 5)
        except cv2.error:
            return None
        W = np.linalg.inv(S) @ Wf @ S
    return W.astype(np.float64)


def _score(a: np.ndarray, b: np.ndarray, H: np.ndarray) -> float:
    """Correlation between the saved picture moved by ``H`` and the live one, where both exist."""
    h, w = b.shape
    warped = cv2.warpPerspective(a.astype(np.float32), H, (w, h))
    valid = cv2.warpPerspective(np.ones_like(a, np.float32), H, (w, h)) > 0.99
    if valid.mean() < 0.3:
        return 0.0
    x, y = warped[valid], b.astype(np.float32)[valid]
    x, y = x - x.mean(), y - y.mean()
    return float((x @ y) / (np.sqrt((x @ x) * (y @ y)) + 1e-9))


def view_change(saved: np.ndarray, frame: np.ndarray, points: list | None = None,
                same_px: float = 2.0, max_shift_frac: float = 0.25, min_score: float = 0.75) -> dict:
    """Compare the map's empty-track image with what the camera sees now.

    Two estimates of the image transform are tried, feature matching (large
    moves, textured scenes) and direct alignment (small nudges, plain scenes);
    the one under which the two pictures correlate best wins. Returns ``status``
    (same / moved / changed / size), ``shift_px`` (how far the waypoints move, at
    most), ``score`` (correlation, 1 = identical) and ``H`` (saved -> now, 3x3).
    Marbles on the track do no harm: they are a small part of the picture.
    """
    if saved.shape[:2] != frame.shape[:2]:
        return {"status": "size", "shift_px": None, "H": None, "score": None,
                "detail": f"the camera now sends {frame.shape[1]}x{frame.shape[0]}, the map was made at {saved.shape[1]}x{saved.shape[0]}"}
    h, w = saved.shape[:2]
    k = min(1.0, 640.0 / max(w, h))
    a, b = _gray(saved, k), _gray(frame, k)
    cands = [np.eye(3)]
    H_orb = _orb(a, b)
    if H_orb is not None:
        cands.append(H_orb)
    for init in (None, H_orb):
        H_ecc = _ecc(a, b, init)
        if H_ecc is not None:
            cands.append(H_ecc)
    scored = [(_score(a, b, H), H) for H in cands if np.isfinite(H).all() and abs(np.linalg.det(H)) > 1e-6]
    score, H = max(scored, key=lambda x: x[0])
    H = _scaled(H, k)
    pts = np.float32(points if points is not None and len(points) else [[0, 0], [w, 0], [w, h], [0, h], [w / 2, h / 2]])
    moved = cv2.perspectiveTransform(pts.reshape(-1, 1, 2), H).reshape(-1, 2)
    shift = float(np.max(np.linalg.norm(moved - pts, axis=1)))
    inside = bool(np.all((moved[:, 0] >= 0) & (moved[:, 0] < w) & (moved[:, 1] >= 0) & (moved[:, 1] < h)))
    if score < min_score:
        status = "changed"
    elif shift <= same_px:
        status = "same"
    elif shift <= max_shift_frac * np.hypot(w, h) and inside and np.linalg.det(H[:2, :2]) > 0:
        status = "moved"
    else:
        status = "changed"
    detail = {"same": "matches the map", "moved": f"moved by up to {shift:.0f} px: waypoints can be carried over",
              "changed": "the view does not match the map: redo its waypoints"}[status]
    return {"status": status, "shift_px": round(shift, 1), "score": round(score, 3), "H": H if status != "changed" else None,
            "detail": detail}


def warp_points(H: np.ndarray, points: list) -> list:
    pts = np.float32(points).reshape(-1, 1, 2)
    return [[round(float(x), 2), round(float(y), 2)] for x, y in cv2.perspectiveTransform(pts, H).reshape(-1, 2)]
