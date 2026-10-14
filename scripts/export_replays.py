"""Export recorded races (pipeline output + ground truth) for the 3D demo and the report.

    python scripts/export_replays.py --out results/replays.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from marbletrack.edgecases import EDGE_CASES  # noqa: E402
from marbletrack.race import SCENARIOS, run_race  # noqa: E402

DEFAULT = [("multi", "nominal"), ("single", "nominal"), ("multi", "camera_failure"), ("multi", "corner_duel"),
           ("multi", "photo_finish"), ("multi", "backward_slip"), ("multi", "worst_case")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results/replays.json")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    out = {}
    for mode, sc in DEFAULT:
        scenario = EDGE_CASES.get(sc) or SCENARIOS[sc]
        r = run_race(mode, seed=a.seed, scenario=scenario, record=True)
        rp = r.replay
        rp.update(mode=mode, scenario=sc, description=scenario.description,
                  summary={k: getattr(r, k) for k in ("rank_accuracy", "rank_accuracy_all", "rank_accuracy_latency",
                                                      "phantom_overtakes", "final_order_correct",
                                                      "final_order_strict", "photo_finishes", "finish_err_ms_max",
                                                      "pos_err_cm_rms", "id_errors")},
                  events=r.events)
        out[f"{mode}_{sc}"] = rp
        print(mode, sc, "order", r.final_order_correct, "rank", round(r.rank_accuracy, 4), flush=True)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, separators=(",", ":"), default=float))
    print(a.out, Path(a.out).stat().st_size // 1024, "KiB")


if __name__ == "__main__":
    main()
