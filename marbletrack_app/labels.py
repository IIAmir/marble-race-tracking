"""Ground truth for real races, cheaply: crossing order at a few gates, and scoring against it.

Drawing boxes on every frame is not needed to judge a leaderboard. What the
board claims is an order, so the truth that matters is the order in which the
marbles pass a handful of places ("gates") on the route, plus the order in
the finish rack. With 8-15 gates per route that is ~100 labelled events per
race, minutes of work per race.

    # 1. proposals from the tracker, plus picture strips to check them
    python -m marbletrack_app.labels propose <session> --gates 1.5 4.0 7.2 ... [--boards <replay>/boards.jsonl]
    # 2. look at <session>/labels/gate-*.jpg, fix the order in <session>/labels/labels.json,
    #    add the rack order as "final_order", set "checked": true on each gate you verified
    # 3. score any run of the tracker (the live boards, or a replay) against the labels
    python -m marbletrack_app.labels score <session> [--boards <replay>/boards.jsonl]

Scores:

* ``gate_order_exact``: gates where the tracker's crossing order is exactly right;
* ``gate_pairs_wrong``: pairs of marbles in the wrong order at a gate (Kendall distance);
* ``inverted_seconds``: board time during which two marbles were shown in an order that
  both surrounding gates contradict (they did not change places between those gates);
* ``final_order_correct``: the board's final order against the rack.
"""
from __future__ import annotations

import argparse
import json
from itertools import combinations
from pathlib import Path

import numpy as np

from .imageio import imwrite, native_path
from .recording import read_frames_csv, read_jsonl

BOARD_DELAY = 0.08  # the live reorder buffer: a board published at `now` shows the track at about now - 0.08 s


# ---------------------------------------------------------------------------- from boards
def crossings(boards: list[dict], gate_s: float) -> dict[str, float]:
    """When each marble's shown position first reached ``gate_s`` (interpolated between boards)."""
    out: dict[str, float] = {}
    prev: dict[str, tuple[float, float]] = {}
    for b in boards:
        now = b["now"]
        for e in b["board"]:
            mid, s = e["marble_id"], e.get("progress_m")
            if s is None or e.get("status") == "unseen" or mid in out:
                continue
            if s >= gate_s:
                if mid in prev and prev[mid][1] < gate_s:
                    t0, s0 = prev[mid]
                    out[mid] = t0 + (now - t0) * (gate_s - s0) / max(s - s0, 1e-9)
                else:
                    out[mid] = now
            prev[mid] = (now, s)
    return out


def gate_orders(boards: list[dict], gates: list[float]) -> list[dict]:
    res = []
    for g in gates:
        c = crossings(boards, g)
        res.append({"s": g, "order": sorted(c, key=c.get), "times": {k: round(v, 4) for k, v in c.items()}})
    return res


def kendall_wrong_pairs(order: list[str], truth: list[str]) -> int:
    pos = {m: k for k, m in enumerate(order)}
    common = [m for m in truth if m in pos]
    return sum(1 for a, b in combinations(common, 2) if pos[a] > pos[b])


# ---------------------------------------------------------------------------- scoring
def score(boards: list[dict], labels: dict) -> dict:
    gates = sorted(labels["gates"], key=lambda g: g["s"])
    got = gate_orders(boards, [g["s"] for g in gates])
    per_gate = []
    for g, o in zip(gates, got):
        truth = g["order"]
        wrong = kendall_wrong_pairs(o["order"], truth)
        missing = [m for m in truth if m not in o["order"]]
        per_gate.append({"s": g["s"], "exact": o["order"][:len(truth)] == truth and not missing,
                         "pairs_wrong": wrong, "missing": missing, "tracker": o["order"], "truth": truth})
    # pairs that keep their order through two consecutive gates did not swap in between:
    # showing them the other way round anywhere between the two crossings is an error on air
    inverted = 0.0
    for (ga, oa), (gb, ob) in zip(zip(gates, got), zip(gates[1:], got[1:])):
        ta, tb = ga["order"], gb["order"]
        pa, pb = {m: k for k, m in enumerate(ta)}, {m: k for k, m in enumerate(tb)}
        stable = [(x, y) if pa[x] < pa[y] else (y, x) for x, y in combinations([m for m in ta if m in pb], 2)
                  if (pa[x] < pa[y]) == (pb[x] < pb[y])]
        for front, back in stable:
            t_start = oa["times"].get(back)  # both past gate a
            t_end = ob["times"].get(front)  # until the front one reaches gate b
            if t_start is None or t_end is None or t_end <= t_start:
                continue
            inverted += _time_shown_behind(boards, front, back, t_start, t_end)
    final = labels.get("final_order")
    shown_final = [e["marble_id"] for e in boards[-1]["board"]] if boards else []
    return {
        "gates": len(gates),
        "gate_order_exact": sum(p["exact"] for p in per_gate),
        "gate_pairs_wrong": sum(p["pairs_wrong"] for p in per_gate),
        "gate_pairs_total": sum(len(g["order"]) * (len(g["order"]) - 1) // 2 for g in gates),
        "inverted_seconds": round(inverted, 3),
        "final_order_correct": (shown_final[:len(final)] == final) if final else None,
        "final_pairs_wrong": kendall_wrong_pairs(shown_final, final) if final else None,
        "per_gate": per_gate,
    }


def _time_shown_behind(boards, front, back, t0, t1) -> float:
    total, last = 0.0, None
    for b in boards:
        now = b["now"]
        if now < t0:
            continue
        if now > t1:
            break
        ids = [e["marble_id"] for e in b["board"]]
        bad = front in ids and back in ids and ids.index(back) < ids.index(front)
        if last is not None and last[1]:
            total += now - last[0]
        last = (now, bad)
    return total


# ---------------------------------------------------------------------------- proposals
def propose(session: Path, gates: list[float], boards_path: Path | None = None, strip_px: int = 160) -> Path:
    """Tracker's crossing order at each gate + picture strips of each crossing, for a human to check."""
    import cv2

    from . import calibration as C
    from .replay import load_session

    manifest, project, _ = load_session(session)
    boards = read_jsonl(boards_path or session / "boards.jsonl")
    out_dir = session / "labels"
    out_dir.mkdir(exist_ok=True)
    names = {m.id: m.name for m in project.marbles}
    colours = {m.id: tuple(int(m.color[i:i + 2], 16) for i in (5, 3, 1)) for m in project.marbles}
    proposal = {"gates": [], "final_order": [e["marble_id"] for e in boards[-1]["board"]] if boards else [],
                "note": "Check every gate against its picture strip, fix 'order', set 'checked': true. "
                        "Replace final_order by the order in the finish rack."}
    for g in gates:
        o = gate_orders(boards, [g])[0]
        entry = {"s": g, "order": o["order"], "checked": False, "times": o["times"]}
        cam = _camera_for(project, g)
        if cam is not None:
            line = C.centerline(cam)
            xy = _point_at(line, g)
            tangent = _point_at(line, g + 0.01) - _point_at(line, g - 0.01)
            tangent = tangent / max(np.linalg.norm(tangent), 1e-9)
            r_px = float(np.interp(g, cam.marker_s, C.marble_radius_px(cam, project)))
            h = int(np.clip(6 * r_px, 40, strip_px))  # about six marble radii around the gate
            rows = read_frames_csv(session / "cameras" / f"{manifest['cameras'][cam.key]}.csv")
            cap = cv2.VideoCapture(native_path(session / "cameras" / f"{manifest['cameras'][cam.key]}.avi"))
            tiles = []
            for mid in o["order"]:
                # board times are on the arrival clock and lag the picture by the reorder delay
                t = o["times"][mid] - BOARD_DELAY
                k = int(np.argmin([abs(r[2] - t) for r in rows])) if rows else 0
                cap.set(cv2.CAP_PROP_POS_FRAMES, k)
                ok, frame = cap.read()
                if not ok:
                    continue
                x, y = int(xy[0]), int(xy[1])
                normal = np.array([-tangent[1], tangent[0]])
                a, b = xy + normal * 2.5 * r_px, xy - normal * 2.5 * r_px  # the gate, across the channel
                frame = frame.copy()
                cv2.line(frame, (int(a[0]), int(a[1])), (int(b[0]), int(b[1])), (255, 255, 255), 1, cv2.LINE_AA)
                crop = frame[max(0, y - h):y + h, max(0, x - h):x + h]
                crop = cv2.resize(crop, (2 * strip_px, 2 * strip_px), interpolation=cv2.INTER_LINEAR)
                cv2.rectangle(crop, (0, 0), (2 * strip_px - 1, 22), (20, 20, 20), -1)
                cv2.circle(crop, (11, 11), 7, colours.get(mid, (200, 200, 200)), -1)
                cv2.putText(crop, f"{len(tiles) + 1}. {names.get(mid, mid)}  t={rows[k][1]:.2f}s", (24, 16),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
                tiles.append(crop)
            cap.release()
            if tiles:
                imwrite(out_dir / f"gate-{g:.2f}.jpg", np.hstack(tiles))
            entry["camera"] = cam.name
        proposal["gates"].append(entry)
    path = out_dir / "labels.json"
    if path.exists():
        path = out_dir / "labels.proposed.json"  # never overwrite checked labels
    path.write_text(json.dumps(proposal, indent=1), encoding="utf-8")
    return path


def _camera_for(project, s: float):
    best = None
    for c in project.active_cameras:
        for lo, hi in c.s_ranges:
            if lo + 0.05 <= s <= hi - 0.05:
                margin = min(s - lo, hi - s)
                if best is None or margin > best[0]:
                    best = (margin, c)
    return best[1] if best else None


def _point_at(line, s: float) -> np.ndarray:
    k = int(np.clip(np.searchsorted(line.s, s), 1, len(line.s) - 1))
    f = (s - line.s[k - 1]) / max(line.s[k] - line.s[k - 1], 1e-9)
    return line.points[k - 1] + f * (line.points[k] - line.points[k - 1])


def main(argv=None):
    ap = argparse.ArgumentParser(description="Gate labels for real races: propose from the tracker, then score.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("propose")
    p.add_argument("session")
    p.add_argument("--gates", type=float, nargs="+", required=True)
    p.add_argument("--boards", default=None)
    s = sub.add_parser("score")
    s.add_argument("session")
    s.add_argument("--boards", default=None)
    s.add_argument("--labels", default=None)
    args = ap.parse_args(argv)
    session = Path(args.session)
    if args.cmd == "propose":
        print(propose(session, args.gates, Path(args.boards) if args.boards else None))
    else:
        labels = json.loads(Path(args.labels or session / "labels" / "labels.json").read_text(encoding="utf-8"))
        unchecked = [g["s"] for g in labels["gates"] if not g.get("checked")]
        res = score(read_jsonl(Path(args.boards) if args.boards else session / "boards.jsonl"), labels)
        res["unchecked_gates"] = unchecked
        print(json.dumps({k: v for k, v in res.items() if k != "per_gate"}, indent=1))
        for g in res["per_gate"]:
            if not g["exact"]:
                print(f"gate {g['s']}: tracker {g['tracker']} truth {g['truth']}")


if __name__ == "__main__":
    main()
