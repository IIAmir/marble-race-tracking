"""Export the venue simulator's routes for the browser simulator (demo/venue_routes.js).

    python scripts/export_venue_routes.py [--seeds 1 2 3 4 5 6 7]

Each route is the centreline of ``marbletrack.venue.course.make_course(seed)`` (the same
circuits the venue races in docs/VENUE_SIMULATOR.md were run on) every 5 cm, in the browser's
axes (y up), with its tunnel, lift and finish line as fractions of the lap.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from marbletrack.venue.course import make_course  # noqa: E402


def export(seed: int, step: float = 0.05) -> dict:
    c = make_course(seed)
    L = float(c.length)
    k = np.unique(np.searchsorted(c.s, np.arange(0.0, L - step / 2, step)))
    P = c.P[k]
    ctr = (P[:, :2].min(0) + P[:, :2].max(0)) / 2
    pts = [[round(float(x - ctr[0]), 4), round(float(z), 4), round(float(-(y - ctr[1])), 4)] for x, y, z in P]
    tags = [c.tags[i] for i in k]
    return {
        "seed": seed, "length": round(L, 2), "pts": pts,
        "tunnels": [[round(a / L, 4), round(b / L, 4)] for a, b in c.tunnels],
        "lift": [round(c.lift[0] / L, 4), round(c.lift[1] / L, 4)],
        "finishAt": round(c.finish_s / L, 4),
        "helixTurns": len(c.helix),
        "zones": int(sum(1 for a, b in zip(tags, tags[1:]) if b == "zone" and a != "zone")),
        "drop": round(float(c.P[:, 2].max() - c.P[:, 2].min()), 2),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3, 4, 5, 6, 7])
    ap.add_argument("--out", default="demo/venue_routes.js")
    a = ap.parse_args()
    routes = [export(s) for s in a.seeds]
    body = ",\n".join(json.dumps(r, separators=(",", ":")) for r in routes)
    Path(a.out).write_text("// The venue simulator's routes (scripts/export_venue_routes.py); do not edit by hand.\n"
                           f"window.VENUE_ROUTES = [\n{body}\n];\n")
    for r in routes:
        print(f"route {r['seed']}: {r['length']} m, {len(r['pts'])} points, drop {r['drop']} m, {r['helixTurns']} tower turns")


if __name__ == "__main__":
    main()
