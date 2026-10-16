"""Venue equipment: NDI timestamps, processing scale, vMix title output, project settings."""
import os
import socket
import threading
import time

import numpy as np
import pytest

from marbletrack_app import vmix
from marbletrack_app.config import Project, VmixSetup
from marbletrack_app.engine import MIN_WORK_RADIUS_PX, _work_scale
from marbletrack_app.ndi import SenderClock


# ---------------------------------------------------------------------------- NDI clock
def test_sender_clock_removes_network_jitter():
    rng = np.random.default_rng(1)
    ts = 1000.0 + np.arange(600) / 60.0  # a 60 fps camera, its own clock
    offset = -812.345  # camera clock vs this PC: any value
    arrival = ts + offset + 0.040 + rng.exponential(0.006, len(ts))  # 40 ms transport + jitter
    clock = SenderClock()
    mapped = np.array([clock.map(a, b) for a, b in zip(ts, arrival)])
    settled = mapped[60:]  # after one second a near-minimal delay has been seen
    true_local = ts[60:] + offset + 0.040
    err = settled - true_local
    assert np.all(err >= -1e-9) and np.max(err) < 0.001  # stamped late by under a millisecond, never early
    assert np.max(np.abs(np.diff(settled) - 1 / 60)) < 0.0005  # the camera's own frame spacing
    assert np.std(arrival[60:] - true_local) > 0.004  # what arrival times alone would give


def test_sender_clock_relearns_after_clock_step_and_without_timestamps():
    clock = SenderClock()
    for k in range(120):
        clock.map(10 + k / 60, 50 + k / 60 + 0.03)
    # the camera's clock jumps back by 3 s (NTP correction)
    t = clock.map(12.0 - 3.0, 50 + 2.0 + 0.03)
    assert clock.resets == 1
    assert abs(t - (50 + 2.0 + 0.03)) < 1e-9
    assert clock.map(None, 77.5) == 77.5
    assert clock.map(9.3e10, 77.6) == 77.6  # NDIlib_recv_timestamp_undefined


# ---------------------------------------------------------------------------- processing scale
def test_work_scale_keeps_small_marbles_resolvable():
    # AIDA HD-NDI-IP67, 1080p, 4 mm lens ~0.6 m from the track: a 25 mm marble is ~60 px wide
    k = _work_scale(1920, 1080, 30.0)
    assert k == pytest.approx(np.sqrt(640 * 480 / (1920 * 1080)))
    assert 30.0 * k >= MIN_WORK_RADIUS_PX
    # OBSBOT Tail 2 overhead at 4K over ~5 m: the same marble is ~19 px wide
    k = _work_scale(3840, 2160, 9.6)
    assert 9.6 * k >= MIN_WORK_RADIUS_PX - 1e-9
    assert _work_scale(640, 480, 3.0) == 1.0
    assert _work_scale(3840, 2160) == 0.25


# ---------------------------------------------------------------------------- vMix
BOARD = [
    {"rank": 1, "name": "Ruby", "color": "#d72a2d", "status": "finished", "finish_time": 61.2345, "gap_m": 0.0, "photo_finish": True},
    {"rank": 2, "name": "Sun & Co", "color": "#ebd728", "status": "tracking", "finish_time": None, "gap_m": 0.4567},
    {"rank": 3, "name": "Leaf", "color": "#28aa3c", "status": "lost", "finish_time": None, "gap_m": 3.0},
]


def test_board_fields():
    cfg = VmixSetup(rows=4, color_field="P{n}Color.Fill.Color")
    f = vmix.board_fields(cfg, BOARD, 12.5, "running")
    assert f["P1Name.Text"] == ("SetText", "Ruby")
    assert f["P1Info.Text"] == ("SetText", "1:01.234 *")
    assert f["P2Info.Text"] == ("SetText", "+0.46 m")
    assert f["P3Info.Text"] == ("SetText", "OUT")
    assert f["P4Name.Text"] == ("SetText", "")  # rows beyond the field are blanked
    assert f["P2Color.Fill.Color"] == ("SetColor", "#ebd728")
    assert f["Clock.Text"] == ("SetText", "12.500") and f["Status.Text"] == ("SetText", "LIVE")
    assert vmix.command("SetText", "Leader board", "P2Name.Text", "Sun & Co") == \
        b"FUNCTION SetText Input=Leader%20board&SelectedName=P2Name.Text&Value=Sun%20%26%20Co\r\n"


class FakeVmix:
    """Minimal vMix TCP API: answers XML and FUNCTION, records SetText values."""

    XML = ('<vmix><version>28.0.0.39</version><inputs>'
           '<input key="abc" number="3" type="GT" title="Leaderboard" shortTitle="Leaderboard">'
           '{texts}</input></inputs></vmix>')

    def __init__(self, fields=("P1Name.Text", "P1Info.Text", "P2Name.Text", "P2Info.Text", "Clock.Text", "Status.Text"),
                 reject=()):
        self.fields, self.reject = fields, set(reject)
        self.values: dict = {}
        self.commands: list = []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()

    def _client(self, conn):
        from urllib.parse import parse_qs

        f = conn.makefile("rb")
        for raw in f:
            line = raw.decode().strip()
            if line == "XML":
                body = self.XML.format(texts="".join(f'<text index="{i}" name="{n}">x</text>' for i, n in enumerate(self.fields)))
                conn.sendall(f"XML {len(body.encode())}\r\n{body}".encode())
            elif line.startswith("FUNCTION "):
                _, fn, q = line.split(" ", 2)
                p = {k: v[0] for k, v in parse_qs(q, keep_blank_values=True).items()}
                self.commands.append((fn, p))
                if p["SelectedName"] in self.reject:
                    conn.sendall(b"FUNCTION ER Field not found\r\n")
                else:
                    self.values[p["SelectedName"]] = p["Value"]
                    conn.sendall(b"FUNCTION OK Completed\r\n")

    def close(self):
        self.sock.close()


def _wait(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_vmix_client_sends_only_changes_and_survives_rejections():
    fake = FakeVmix(reject={"Status.Text"})
    cfg = VmixSetup(enabled=True, port=fake.port, rows=2)
    client = vmix.VmixClient(cfg).start()
    try:
        client.publish_board(BOARD[:2], 3.0, "running")
        assert _wait(lambda: fake.values.get("P2Name.Text") == "Sun & Co" and "Clock.Text" in fake.values)
        assert fake.values["P1Info.Text"] == "1:01.234 *" and fake.values["Clock.Text"] == "3.000"
        assert _wait(lambda: client.errors == 1)
        n = len(fake.commands)
        client.publish_board(BOARD[:2], 3.1, "running")  # only the clock changed
        assert _wait(lambda: fake.values.get("Clock.Text") == "3.100")
        time.sleep(0.2)
        assert len(fake.commands) == n + 1
        assert client.status()["state"] == "connected" and "Status.Text" in client.status()["last_error"]
    finally:
        client.stop()
        fake.close()


def test_vmix_connection_test_checks_title_and_fields():
    fake = FakeVmix()
    try:
        assert vmix.test_connection(VmixSetup(port=fake.port, rows=2))["ok"]
        r = vmix.test_connection(VmixSetup(port=fake.port, rows=3))
        assert not r["ok"] and "P3Name.Text" in r["detail"]
        r = vmix.test_connection(VmixSetup(port=fake.port, input="Scores"))
        assert not r["ok"] and "no input" in r["detail"]
        assert vmix.test_connection(VmixSetup(port=fake.port, input="3", rows=2))["ok"]  # by number
    finally:
        fake.close()
    with socket.socket() as s:  # a port nobody listens on
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    r = vmix.test_connection(VmixSetup(port=free))
    assert not r["ok"] and "vMix" in r["detail"]


def test_project_keeps_vmix_settings_and_loads_old_projects():
    p = Project.new()
    p.vmix.enabled, p.vmix.input = True, "LB"
    q = Project.from_dict(p.to_dict())
    assert q.vmix.enabled and q.vmix.input == "LB"
    d = p.to_dict()
    del d["vmix"]
    assert Project.from_dict(d).vmix == VmixSetup()


# ---------------------------------------------------------------------------- NDI on a real network stack
@pytest.mark.slow
@pytest.mark.skipif(not os.environ.get("MARBLETRACK_NDI_LOOPBACK"), reason="set MARBLETRACK_NDI_LOOPBACK=1 (needs NDI discovery on this machine)")
def test_ndi_source_receives_a_local_sender():
    from fractions import Fraction

    from cyndilib.sender import Sender
    from cyndilib.video_frame import VideoSendFrame
    from cyndilib.wrapper.ndi_structs import FourCC

    from marbletrack_app.cameras import discover_ndi, make_source

    name = f"MT-LOOP-{os.getpid()}"
    stop = threading.Event()

    def send():
        vf = VideoSendFrame()
        vf.set_resolution(320, 180)
        vf.set_frame_rate(Fraction(30, 1))
        vf.set_fourcc(FourCC.BGRA)
        s = Sender(name, clock_video=True)
        s.set_video_frame(vf)
        with s:
            img = np.zeros((180, 320, 4), np.uint8)
            img[..., 1] = 200
            while not stop.is_set():
                s.write_video(img.ravel())

    threading.Thread(target=send, daemon=True).start()
    try:
        assert _wait(lambda: any(name in c.device for c in discover_ndi(1.0)), 15)
        key = next(c.device for c in discover_ndi(0.5) if name in c.device)
        src = make_source("ndi", key)
        src.open()
        assert src.size == (320, 180)
        frames = [src.read() for _ in range(10)]
        got = [(f, t) for f, t in frames if f is not None]
        assert got and got[-1][0].shape == (180, 320, 3) and abs(int(got[-1][0][90, 160, 1]) - 200) < 12
        assert all(b > a for (_, a), (_, b) in zip(got, got[1:]))
        src.close()
    finally:
        stop.set()


def test_colour_click_that_caught_only_part_of_a_marble_is_left_out():
    from marbletrack_app.calibration import consistent_clicks

    black = [[14.0, 0.0, 0.0], [12.0, 1.0, -1.0], [76.0, 0.0, 1.0], [15.0, 0.0, 0.0]]  # third: only the number disc
    assert consistent_clicks(black) == [True, True, False, True]
    assert consistent_clicks(black[:2] + black[2:3]) == [True, True, False]
    assert consistent_clicks(black[:2]) == [True, True]  # too few to tell
    assert consistent_clicks([[10, 0, 0], [50, 0, 0], [90, 0, 0]]) == [True, True, True]  # no majority: keep all
