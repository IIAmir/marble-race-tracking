"""NDI camera sources (AIDA HD-NDI-IP67, OBSBOT Tail 2 and any other NDI or NDI|HX sender).

Uses ``cyndilib`` (Cython bindings that ship the NDI runtime). The import is
lazy, so the app runs without it; discovery then simply finds no NDI cameras.

Timestamps
----------
An NDI video frame carries the sender's ``timestamp``: when the camera handed
the frame to its NDI encoder, on the camera's own clock (UTC, 100 ns units).
Network transport and decoding add a variable delay on our side, so the
arrival time alone jitters by several milliseconds at 60 fps. ``SenderClock``
maps the sender's timestamps onto this PC's ``perf_counter`` clock using the
*minimum* observed delay over a sliding window: frames keep the camera's own
spacing (no jitter), the camera's clock may be off by any amount (no NTP
needed), and a clock step on the camera is detected and re-learnt. What it
cannot know is the constant delay between exposure and the encoder; that is
the camera's ``latency_ms`` in the project (equal for cameras of the same
model, so it does not affect who is ahead, only the absolute race clock).
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque

import numpy as np

log = logging.getLogger("marbletrack.ndi")

# NDIlib_recv_timestamp_undefined is INT64_MAX (100 ns units): ~2.9e10 s after 1970.
_TS_UNDEFINED_AFTER = 1e10


def available() -> bool:
    try:
        import cyndilib  # noqa: F401  # type: ignore
    except Exception:
        return False
    return True


class SenderClock:
    """Maps a sender's frame timestamps to the local clock (see the module docstring).

    ``offset = arrival - sender_ts`` is the sender-to-local clock offset plus the
    transport delay. Its minimum over ``window`` seconds is the least-delayed
    frame; every frame is stamped ``sender_ts + min_offset``. The window is short
    enough that the drift between two quartz clocks (~50 ppm, 0.5 ms per 10 s)
    stays below a millisecond.
    """

    def __init__(self, window: float = 10.0, step: float = 0.5):
        self.window, self.step = window, step
        self._q: deque = deque()  # (arrival, offset), offsets increasing (monotonic min-queue)
        self.last_ts: float | None = None
        self.resets = 0

    def reset(self):
        self._q.clear()
        self.last_ts = None
        self.resets += 1

    def map(self, sender_ts: float | None, arrival: float) -> float:
        if sender_ts is None or not np.isfinite(sender_ts) or sender_ts <= 0 or sender_ts > _TS_UNDEFINED_AFTER:
            return arrival  # the sender gives no timestamps: arrival time is all we have
        off = arrival - sender_ts
        # A step back of the camera's clock (NTP correction, reboot) or a long silence invalidates
        # the learnt minimum. A step forward needs nothing: its smaller offset is the new minimum.
        if self.last_ts is not None and (sender_ts <= self.last_ts - self.step or sender_ts - self.last_ts > 5.0):
            self.reset()
        self.last_ts = sender_ts
        while self._q and self._q[-1][1] >= off:
            self._q.pop()
        self._q.append((arrival, off))
        while self._q and self._q[0][0] < arrival - self.window:
            self._q.popleft()
        return sender_ts + self._q[0][1]

    @property
    def min_offset(self) -> float:
        """Current minimum offset (s), for diagnostics."""
        return self._q[0][1] if self._q else float("nan")


# ---------------------------------------------------------------------------- discovery
_finder = None
_finder_lock = threading.Lock()


def _get_finder():
    """One long-lived finder per process: NDI discovery keeps learning in the background."""
    global _finder
    with _finder_lock:
        if _finder is None:
            from cyndilib.finder import Finder  # type: ignore

            _finder = Finder()
            _finder.open()
        return _finder


def source_names(wait: float = 2.0) -> list[str]:
    if not available():
        return []
    f = _get_finder()
    end = time.monotonic() + wait
    names = f.get_source_names()
    while time.monotonic() < end:  # sources announce themselves over a second or two
        f.wait_for_sources(min(0.5, max(0.05, end - time.monotonic())))
        names = f.get_source_names()
    return sorted(names)


def guess_model(name: str) -> str:
    n = name.upper()
    if "OBSBOT" in n or "TAIL" in n:
        return "OBSBOT Tail 2"
    if "AIDA" in n:
        return "AIDA HD-NDI-IP67"
    return ""


# ---------------------------------------------------------------------------- source
class NDISource:
    """Receives one NDI sender. ``read()`` returns ``(frame_bgr, t)`` like every source.

    Only the newest frame is kept: if processing falls behind, older frames are
    dropped rather than queued (bounded latency, as with the other backends).
    """

    def __init__(self, name: str, width=0, height=0, fps=0.0, connect_timeout: float = 8.0):
        self.name = name
        self.connect_timeout = connect_timeout
        self.receiver = None
        self.vf = None
        self.clock = SenderClock()
        self._size = (0, 0)
        self._buf = None
        self._pending = None
        self.sender_timestamps = None  # True / False once the first frame arrived

    def open(self):
        if not available():
            raise RuntimeError("NDI support is not installed (pip install cyndilib)")
        from cyndilib.receiver import Receiver  # type: ignore
        from cyndilib.video_frame import VideoRecvFrame  # type: ignore
        from cyndilib.wrapper.ndi_recv import RecvBandwidth, RecvColorFormat  # type: ignore

        f = _get_finder()
        end = time.monotonic() + self.connect_timeout
        src = None
        while src is None:
            if self.name in f.get_source_names():
                src = f.get_source(self.name)
                break
            if time.monotonic() > end:
                raise RuntimeError(f"NDI source {self.name!r} not found on the network (camera off, other subnet or firewall?)")
            f.wait_for_sources(0.5)
        self.receiver = Receiver(color_format=RecvColorFormat.BGRX_BGRA, bandwidth=RecvBandwidth.highest,
                                 recv_name="MarbleTrack")
        self.vf = VideoRecvFrame(max_buffers=2)
        self.receiver.set_video_frame(self.vf)
        self.receiver.set_source(src)
        # Wait for the first frame so that the resolution is known when open() returns.
        while time.monotonic() < end + 5.0:
            frame, t = self._receive()
            if frame is not None:
                self._pending = (frame, t)
                return
        raise RuntimeError(f"NDI source {self.name!r} found but sends no video")

    def apply(self, auto_exposure: bool, exposure, auto_wb: bool) -> dict:
        # Exposure, shutter and white balance of NDI cameras are set in the camera's own web page
        # (or VISCA); MarbleTrack does not change them, so a camera used by vMix is never disturbed.
        return {}

    def read(self):
        if self._pending is not None:
            out, self._pending = self._pending, None
            return out
        return self._receive()

    def _receive(self):
        from cyndilib.receiver import ReceiveFrameType  # type: ignore

        ft = self.receiver.receive(ReceiveFrameType.recv_video, 200)
        if not ft & ReceiveFrameType.recv_video:
            return None, time.perf_counter()
        # Drain anything else already waiting: keep only the newest frame.
        while True:
            more = self.receiver.receive(ReceiveFrameType.recv_video, 0)
            if not more & ReceiveFrameType.recv_video:
                break
        arrival = time.perf_counter()
        ts_posix = self.vf.get_timestamp_posix()
        while self.vf.get_buffer_depth() > 1:
            self.vf.skip_frames(False)
        w, h = self.vf.get_resolution()
        stride = self.vf.get_line_stride() or w * 4
        n = self.vf.get_buffer_size()
        if self._buf is None or self._buf.size != n:
            self._buf = np.empty(n, np.uint8)
        if not self.vf.fill_p_data(self._buf):
            return None, arrival
        img = self._buf[: stride * h].reshape(h, stride // 4, 4)[:, :w, :3]
        self._size = (w, h)
        if self.sender_timestamps is None:
            self.sender_timestamps = 0 < ts_posix < _TS_UNDEFINED_AFTER
            if not self.sender_timestamps:
                log.warning("NDI %s sends no timestamps: using arrival times (more timing jitter)", self.name)
        # sender time (camera clock, UTC) -> local perf_counter, via the wall-clock offset
        sender_local = ts_posix - time.time() + arrival if self.sender_timestamps else None
        t = self.clock.map(sender_local, arrival)
        return np.ascontiguousarray(img), t

    @property
    def size(self):
        return self._size

    def close(self):
        if self.receiver is not None:
            try:
                self.receiver.disconnect()
            except Exception:
                pass
        self.receiver = None
