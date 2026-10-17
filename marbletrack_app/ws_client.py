"""Streams the live leaderboard to a backend server over a WebSocket.

Protocol (JSON text frames, schema ``marbletrack.v1``)
------------------------------------------------------
On connect the client sends ``hello``; afterwards ``leaderboard`` snapshots at
the publish rate (only the newest snapshot is sent: after a reconnect the
server gets the current board, not a backlog) and ``race_event`` messages,
which are queued and delivered in order (up to 1,000 while offline).

Every message carries ``schema``, ``type``, ``station_id``, ``seq`` (per
connection, increasing) and ``sent_at`` (UTC, ISO 8601). See
``docs/WINDOWS_APP.md`` for full examples. The ``Authorization: Bearer
<token>`` header is sent when a token is configured.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import threading
import time
from collections import deque
from datetime import datetime, timezone

log = logging.getLogger("marbletrack.ws")
SCHEMA = "marbletrack.v1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"} if token else {}


def _connect(url: str, token: str, timeout: float):
    from websockets.asyncio.client import connect

    return connect(url, additional_headers=_headers(token), open_timeout=timeout, ping_interval=10, ping_timeout=10,
                   close_timeout=2, max_size=2**20)


def check_url(url: str) -> str | None:
    if not url:
        return "Enter the server address, or turn off \"Stream to a server\" (step 6) if you only use vMix."
    if not (url.startswith("ws://") or url.startswith("wss://")):
        return "The address must start with ws:// or wss://"
    return None


def test_connection(url: str, token: str = "", station_id: str = "", timeout: float = 5.0) -> dict:
    """Open a connection, send a hello, close. Used by the wizard's "Test connection"."""
    err = check_url(url)
    if err:
        return {"ok": False, "detail": err}

    async def go():
        t = time.perf_counter()
        async with _connect(url, token, timeout) as ws:
            await ws.send(json.dumps({"schema": SCHEMA, "type": "hello", "station_id": station_id, "seq": 0,
                                      "sent_at": _now_iso(), "test": True}))
            pong = await ws.ping()
            await asyncio.wait_for(pong, timeout)
            return (time.perf_counter() - t) * 1000

    try:
        ms = asyncio.run(go())
        return {"ok": True, "detail": f"Connected in {ms:.0f} ms"}
    except Exception as e:
        return {"ok": False, "detail": _explain(e)}


def _explain(e: Exception) -> str:
    name = type(e).__name__
    text = str(e) or name
    if "InvalidStatus" in name or "InvalidHandshake" in name:
        if "401" in text or "403" in text:
            return "The server refused the token (HTTP 401/403). Check the access token."
        return f"The server answered but did not accept a WebSocket: {text}"
    if isinstance(e, (ConnectionRefusedError,)) or "Connect call failed" in text or "refused" in text.lower():
        return "Nothing is listening at that address (connection refused). Is the server running?"
    if isinstance(e, (asyncio.TimeoutError, TimeoutError)) or "timed out" in text.lower():
        return "No answer within 5 s. Check the address, the network and the firewall."
    if "getaddrinfo" in text or "Name or service not known" in text or "nodename" in text:
        return "The server name could not be found (DNS). Check the address."
    if "CERTIFICATE" in text.upper() or "SSL" in text.upper():
        return f"Secure connection failed: {text}"
    return text


class LeaderboardStreamer:
    """Background WebSocket client with automatic reconnection."""

    def __init__(self, url: str, token: str, station_id: str, app_version: str, extra_hello: dict | None = None):
        self.url, self.token, self.station_id, self.app_version = url, token, station_id, app_version
        self.extra_hello = extra_hello or {}
        self._board: dict | None = None
        self._board_dirty = False
        self._events: deque = deque(maxlen=1000)
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._stop = threading.Event()
        self.state, self.detail = "idle", ""
        self.sent = 0
        self.dropped_events = 0
        self.connected_since: float | None = None
        self.last_error = ""
        self.reconnects = 0
        self.thread = threading.Thread(target=self._thread, daemon=True, name="ws-client")

    # -- public, thread-safe -------------------------------------------------------
    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._poke()
        self.thread.join(timeout=5)

    def publish_board(self, race_id: str, state: str, clock, board: list, cameras: dict, late: bool = False,
                      result: str | None = None, hidden: bool = False):
        msg = {"type": "leaderboard", "race_id": race_id, "race_state": state,
               "race_time": None if clock is None else round(clock, 3), "clock_estimated": bool(late), "board": board,
               "cameras": {k: bool(v.get("online")) for k, v in cameras.items()},
               "result": result, "hidden": bool(hidden)}
        with self._lock:
            self._board, self._board_dirty = msg, True
        self._poke()

    def publish_event(self, kind: str, data: dict):
        with self._lock:
            if len(self._events) == self._events.maxlen:
                self.dropped_events += 1
            self._events.append({"type": "race_event", "event": kind, **data})
        self._poke()

    def status(self) -> dict:
        return {"state": self.state, "detail": self.detail, "sent": self.sent, "queued": len(self._events),
                "dropped_events": self.dropped_events, "reconnects": self.reconnects, "url": self.url,
                "connected_for": None if self.connected_since is None else round(time.time() - self.connected_since)}

    # -- internals ---------------------------------------------------------------------
    def _poke(self):
        if self._loop is not None and self._wake is not None:
            try:
                self._loop.call_soon_threadsafe(self._wake.set)
            except RuntimeError:
                pass

    def _thread(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._wake = asyncio.Event()
        try:
            self._loop.run_until_complete(self._run())
        finally:
            self._loop.close()

    async def _run(self):
        backoff, logged = 0.5, ""
        while not self._stop.is_set():
            err = check_url(self.url)
            if err:
                self.state, self.detail = "error", err
                return
            self.state, self.detail = "connecting", f"Connecting to {self.url}"
            try:
                async with _connect(self.url, self.token, 5.0) as ws:
                    self.state, self.detail = "connected", "Streaming"
                    self.connected_since = time.time()
                    backoff, logged = 0.5, ""
                    seq = 0

                    async def send(msg):
                        nonlocal seq
                        seq += 1
                        await ws.send(json.dumps({"schema": SCHEMA, "station_id": self.station_id, "seq": seq,
                                                  "sent_at": _now_iso(), **msg}, separators=(",", ":")))
                        self.sent += 1

                    await send({"type": "hello", "app_version": self.app_version, **self.extra_hello})
                    with self._lock:
                        self._board_dirty = self._board is not None
                    reader = asyncio.ensure_future(self._drain(ws))
                    while not self._stop.is_set():
                        if reader.done():
                            reader.result()  # raises the connection error
                        with self._lock:
                            ev = self._events[0] if self._events else None
                            board = self._board if self._board_dirty else None
                        if ev is not None:
                            await send(ev)
                            with self._lock:
                                if self._events and self._events[0] is ev:
                                    self._events.popleft()
                            continue
                        if board is not None:
                            with self._lock:
                                self._board_dirty = False
                            await send(board)
                            continue
                        self._wake.clear()
                        try:
                            await asyncio.wait_for(self._wake.wait(), 1.0)
                        except asyncio.TimeoutError:
                            pass
                    reader.cancel()
                    await ws.close()
            except Exception as e:
                if self._stop.is_set():
                    break
                err = _explain(e)
                if err != logged:  # a server that is down all day: one line in the log, not one per retry
                    log.warning("websocket: %s", err)
                    logged = err
                self.last_error = err
                self.connected_since = None
                self.reconnects += 1
                delay = backoff * (0.7 + 0.6 * random.random())
                self.state, self.detail = "reconnecting", f"{self.last_error} Retrying in {delay:.0f} s."
                end = time.monotonic() + delay
                while time.monotonic() < end and not self._stop.is_set():
                    await asyncio.sleep(0.1)
                backoff = min(10.0, backoff * 2)
        self.state, self.detail = "stopped", ""

    async def _drain(self, ws):
        """Read (and ignore) anything the server sends; ends when the connection drops."""
        async for _ in ws:
            pass
        raise ConnectionError("server closed the connection")
