"""Image and video files in any folder, also one whose name is not ASCII.

OpenCV's file functions take narrow (ANSI) paths on Windows: ``cv2.imread``,
``cv2.imwrite``, ``VideoWriter`` and ``VideoCapture`` fail on a path such as
``C:\\Users\\<a Persian name>\\AppData\\Roaming\\MarbleTrack``. Images are therefore
encoded in memory and written with Python's own file functions; videos get the
Windows short (8.3) form of the folder, which is ASCII.
"""
from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np


def imwrite(path, img, params=(), atomic: bool = False) -> bool:
    """``cv2.imwrite`` for any path: False if the image could not be encoded or written.
    ``atomic``: write a temp file and swap it in (a crash never leaves a truncated image)."""
    path = Path(path)
    ok, buf = cv2.imencode(path.suffix or ".png", img, list(params))
    if not ok:
        return False
    target = path.with_name(f".{path.name}.tmp") if atomic else path
    try:
        with open(target, "wb") as f:
            f.write(buf.tobytes())
            if atomic:
                f.flush()
                os.fsync(f.fileno())
        if atomic:
            from .config import replace_file

            replace_file(target, path)
    except OSError:
        if atomic:
            try:
                os.remove(target)
            except OSError:
                pass
        return False
    return True


def imread(path, flags=cv2.IMREAD_COLOR):
    """``cv2.imread`` for any path: None if the file is missing or not an image."""
    try:
        data = np.fromfile(str(path), np.uint8)
    except OSError:
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, flags)


def native_path(path) -> str:
    """A path OpenCV's video functions can open: on Windows, a non-ASCII folder becomes its short
    (8.3) form. The file name itself must be ASCII (the app names its files so)."""
    path = Path(path)
    s = str(path)
    if os.name != "nt" or s.isascii():
        return s
    try:
        import ctypes

        parent = str(path.parent)
        n = ctypes.windll.kernel32.GetShortPathNameW(parent, None, 0)
        if n:
            buf = ctypes.create_unicode_buffer(n)
            if ctypes.windll.kernel32.GetShortPathNameW(parent, buf, n) and buf.value.isascii():
                return os.path.join(buf.value, path.name)
    except Exception:  # pragma: no cover - Windows only, 8.3 names may be disabled
        pass
    return s
