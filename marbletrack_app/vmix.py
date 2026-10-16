"""Live leaderboard into a vMix title, over the vMix TCP API (port 8099).

vMix shows the graphics; MarbleTrack only fills in the title's fields. Design
a title (GT Title Designer) with one text field per value and name them after
the patterns in ``VmixSetup`` (defaults: ``P1Name``, ``P1Info``, ``P1Color``,
…, ``Clock``, ``Status``). Only fields whose value changed are sent, so ten
updates a second cost a handful of commands, not hundreds.

Protocol: ``FUNCTION SetText Input=<input>&SelectedName=<field>&Value=<text>\\r\\n``,
answered by ``FUNCTION OK …`` or ``FUNCTION ER <reason>``. Parameter values
are URL-encoded, as in the HTTP API.
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from urllib.parse import quote

log = logging.getLogger("marbletrack.vmix")


def _fmt_time(t: float | None) -> str:
    if t is None:
        return ""
    m, s = divmod(max(0.0, t), 60.0)
    return f"{int(m)}:{s:06.3f}" if m >= 1 else f"{s:.3f}"


def board_fields(cfg, board: list, clock, state: str, laps: int = 1) -> dict[str, tuple[str, str]]:
    """Title fields for one leaderboard: ``{field: (function, value)}``.

    ``Info`` is the finish time for finished marbles, the gap to the leader in
    metres while racing, and ``OUT`` for a marble that has not been seen for a while.
    """
    out: dict[str, tuple[str, str]] = {}
    rows = cfg.rows or len(board)
    for n in range(1, rows + 1):
        e = board[n - 1] if n <= len(board) else None
        if e is None:
            name, info, color = "", "", "#00000000"
        else:
            name, color = e.get("name", ""), e.get("color", "#808080")
            if e.get("finish_time") is not None:
                info = _fmt_time(e["finish_time"]) + (" *" if e.get("photo_finish") else "")
            elif e.get("status") == "lost":
                info = "OUT"
            elif n == 1 or e.get("gap_m") is None:
                info = ""
            else:
                info = f"+{e['gap_m']:.2f} m"
        if cfg.name_field:
            out[cfg.name_field.format(n=n)] = ("SetText", name)
        if cfg.info_field:
            out[cfg.info_field.format(n=n)] = ("SetText", info)
        if cfg.color_field:
            out[cfg.color_field.format(n=n)] = ("SetColor", color)
    if cfg.clock_field:
        out[cfg.clock_field] = ("SetText", _fmt_time(clock) if clock is not None else "")
    if cfg.status_field:
        out[cfg.status_field] = ("SetText", {"waiting": "READY", "running": "LIVE", "complete": "FINAL"}.get(state, state.upper()))
    if getattr(cfg, "lap_field", ""):
        racing = [e for e in board if e.get("finish_time") is None and e.get("status") != "unseen"]
        lap = max([e.get("lap", 1) for e in racing], default=laps if state == "complete" else 1)
        out[cfg.lap_field] = ("SetText", "" if laps <= 1 else ("FINAL LAP" if lap >= laps else f"LAP {lap}/{laps}"))
    return out


def command(function: str, input_: str, field: str, value: str) -> bytes:
    q = f"Input={quote(str(input_), safe='')}&SelectedName={quote(field, safe='.')}&Value={quote(value, safe='')}"
    return f"FUNCTION {function} {q}\r\n".encode("utf-8")


class VmixClient:
    """Background TCP client: reconnects, sends only changed fields, newest board wins."""

    def __init__(self, cfg, laps: int = 1):
        self.cfg = cfg
        self.laps = laps
        self._want: dict[str, tuple[str, str]] = {}
        self._sent: dict[str, tuple[str, str]] = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self.state, self.detail = "idle", ""
        self.sent = 0
        self.errors = 0
        self.last_error = ""
        self.thread = threading.Thread(target=self._run, daemon=True, name="vmix")

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._wake.set()
        self.thread.join(timeout=3)

    def publish_board(self, board: list, clock, state: str):
        fields = board_fields(self.cfg, board, clock, state, self.laps)
        with self._lock:
            self._want.update(fields)
        self._wake.set()

    def status(self) -> dict:
        return {"state": self.state, "detail": self.detail, "sent": self.sent, "errors": self.errors,
                "last_error": self.last_error}

    def _pending(self) -> list[tuple[str, tuple[str, str]]]:
        with self._lock:
            return [(k, v) for k, v in self._want.items() if self._sent.get(k) != v]

    def _run(self):
        backoff = 0.5
        while not self._stop.is_set():
            self.state, self.detail = "connecting", f"Connecting to vMix at {self.cfg.host}:{self.cfg.port}"
            try:
                with socket.create_connection((self.cfg.host, int(self.cfg.port)), timeout=3.0) as sock:
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    sock.settimeout(2.0)
                    reader = sock.makefile("rb")
                    self.state, self.detail = "connected", f"Filling title {self.cfg.input!r}"
                    self._sent.clear()  # vMix may have restarted: send every field again
                    backoff = 0.5
                    while not self._stop.is_set():
                        todo = self._pending()
                        if not todo:
                            self._wake.wait(1.0)
                            self._wake.clear()
                            continue
                        # Pipeline the batch, then read one reply per command.
                        sock.sendall(b"".join(command(fn, self.cfg.input, k, v) for k, (fn, v) in todo))
                        for k, v in todo:
                            line = reader.readline()
                            if not line:
                                raise ConnectionError("vMix closed the connection")
                            text = line.decode("utf-8", "replace").strip()
                            if text.startswith("FUNCTION ER"):
                                self.errors += 1
                                self.last_error = f"{k}: {text[len('FUNCTION ER'):].strip()}"
                                log.warning("vMix rejected %s: %s", k, text)
                            self._sent[k] = v  # also on ER: retrying a misnamed field forever helps nobody
                            self.sent += 1
            except Exception as e:
                if self._stop.is_set():
                    break
                self.last_error = _explain(e, self.cfg)
                self.state, self.detail = "reconnecting", f"{self.last_error} Retrying in {backoff:.0f} s."
                log.warning("vMix: %s", self.last_error)
                self._stop.wait(backoff)
                backoff = min(5.0, backoff * 2)
        self.state, self.detail = "stopped", ""


def _explain(e: Exception, cfg) -> str:
    if isinstance(e, ConnectionRefusedError):
        return (f"Nothing is listening at {cfg.host}:{cfg.port}. Is vMix running? "
                "(Settings → Web Controller/TCP API must be enabled; the TCP API uses port 8099.)")
    if isinstance(e, (socket.timeout, TimeoutError)):
        return f"vMix at {cfg.host}:{cfg.port} did not answer within 3 s."
    if isinstance(e, socket.gaierror):
        return f"The address {cfg.host!r} could not be found."
    return str(e) or type(e).__name__


def test_connection(cfg, timeout: float = 3.0) -> dict:
    """Connect, ask vMix for its version and check that the title input exists."""
    try:
        t = time.perf_counter()
        with socket.create_connection((cfg.host, int(cfg.port)), timeout=timeout) as sock:
            sock.settimeout(timeout)
            f = sock.makefile("rb")
            sock.sendall(b"XML\r\n")
            head = f.readline().decode("utf-8", "replace").strip()  # "XML <length>"
            if not head.startswith("XML "):
                return {"ok": False, "detail": f"Unexpected answer from vMix: {head[:80]}"}
            n = int(head.split()[1])
            xml = f.read(n).decode("utf-8", "replace")
            ms = (time.perf_counter() - t) * 1000
    except Exception as e:
        return {"ok": False, "detail": _explain(e, cfg)}
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml.strip())
    except ET.ParseError:
        return {"ok": True, "detail": f"Connected in {ms:.0f} ms (could not read the input list)"}
    version = root.findtext("version") or "?"
    want = str(cfg.input).strip()
    for inp in root.iter("input"):
        if want in (inp.get("number"), inp.get("key"), inp.get("title"), inp.get("shortTitle")):
            names = [t.get("name") for t in inp.iter("text")]
            fields = board_fields(cfg, [], None, "waiting")
            missing = [k for k, (fn, _) in fields.items() if fn == "SetText" and names and k not in names]
            if missing:
                return {"ok": False, "detail": f"vMix {version}: title {want!r} has no field {missing[0]} "
                                               f"({len(missing)} missing). Its fields: {', '.join(names[:8])}"}
            return {"ok": True, "detail": f"vMix {version}, title {want!r} found, connected in {ms:.0f} ms"}
    return {"ok": False, "detail": f"vMix {version} is running but has no input called {want!r}."}
