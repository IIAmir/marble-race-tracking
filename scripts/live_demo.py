"""Run the full system in real time on virtual cameras.

    python scripts/live_demo.py --mode multi --nodes 3 --serve 8080

Edge-node processes grab frames from simulated cameras on the wall clock, detect
marbles and stream detections to the fusion process, which publishes the live
leaderboard (terminal, and http://localhost:8080 when --serve is given).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from marbletrack.race import SCENARIOS  # noqa: E402
from marbletrack.runtime import run_live  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["single", "multi"], default="multi")
    ap.add_argument("--scenario", choices=list(SCENARIOS), default="nominal")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--nodes", type=int, default=3, help="edge-node processes")
    ap.add_argument("--no-finish-cam", action="store_true")
    ap.add_argument("--serve", type=int, default=None, help="HTTP port for the overlay / JSON feed")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--out", default=None, help="write the run summary as JSON")
    a = ap.parse_args()

    last = [-1.0]

    def show(now, board, engine):
        if a.quiet or now - last[0] < 0.5:
            return
        last[0] = now
        cams = engine.camera_status()
        down = [c for c, ok in cams.items() if not ok]
        line = " ".join(
            f"{e.rank}.{e.marble_id}{'*' if e.status == 'finished' else ('~' if e.status == 'coasting' else '')}"
            for e in board)
        print(f"t={engine.now:5.2f}s  {line}" + (f"   OFFLINE: {','.join(down)}" if down else ""), flush=True)

    res = run_live(a.mode, a.scenario, a.seed, nodes=a.nodes, finish_cam=not a.no_finish_cam,
                   serve_port=a.serve, on_publish=show)
    print(json.dumps(res, indent=1))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
