"""Run every stress scenario over many random races and write aggregate statistics.

    python scripts/monte_carlo.py --seeds 10 --out results/monte_carlo.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from multiprocessing import Pool
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from marbletrack.edgecases import EDGE_CASES, run_edge_case  # noqa: E402
from marbletrack.race import SCENARIOS, run_race  # noqa: E402


def _job(args):
    scenario, mode, seed, finish_cam = args
    if scenario.startswith("edge:"):
        return {"edge": True, **run_edge_case(scenario[5:], mode, seed)}
    r = run_race(mode, seed=seed, scenario=scenario, finish_cam=finish_cam)
    d = asdict(r)
    d.pop("replay")
    d["finish_cam"] = finish_cam
    return d


def summarize(rows):
    out = []
    keys = sorted({(r["scenario"], r["mode"], r["finish_cam"]) for r in rows})
    for sc, mode, fc in keys:
        rs = [r for r in rows if (r["scenario"], r["mode"], r["finish_cam"]) == (sc, mode, fc)]
        fin = np.array([r["finish_err_ms_max"] for r in rs])
        out.append({
            "scenario": sc,
            "mode": mode,
            "finish_cam": fc,
            "races": len(rs),
            "official_order_correct": sum(r["final_order_correct"] for r in rs),
            "strict_order_correct": sum(r["final_order_strict"] for r in rs),
            "races_with_photo_finish_review": sum(r["photo_finishes"] > 0 for r in rs),
            "rank_accuracy_visible_mean": float(np.mean([r["rank_accuracy"] for r in rs])),
            "rank_accuracy_visible_min": float(np.min([r["rank_accuracy"] for r in rs])),
            "rank_accuracy_all_mean": float(np.mean([r["rank_accuracy_all"] for r in rs])),
            "finish_err_ms_mean": float(np.mean([r["finish_err_ms_mean"] for r in rs])),
            "finish_err_ms_p95_of_max": float(np.percentile(fin, 95)),
            "finish_err_ms_max": float(fin.max()),
            "pos_err_cm_rms_mean": float(np.mean([r["pos_err_cm_rms"] for r in rs])),
            "id_errors_total": int(sum(r["id_errors"] for r in rs)),
            "late_batches_mean": float(np.mean([r["late_batches"] for r in rs])),
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default="results/monte_carlo.json")
    a = ap.parse_args()
    jobs = [(name, mode, seed, True) for name, sc in SCENARIOS.items() for mode in sc.modes
            for seed in range(a.seeds)]
    jobs += [("nominal", mode, seed, False) for mode in ("single", "multi") for seed in range(a.seeds)]
    jobs += [(f"edge:{name}", mode, seed, True) for name, sc in EDGE_CASES.items() for mode in sc.modes
             for seed in range(a.seeds)]
    t0 = time.time()
    rows, edges = [], []
    with Pool(a.workers) as pool:
        for i, row in enumerate(pool.imap_unordered(_job, jobs), 1):
            if row.get("edge"):
                edges.append(row)
                print(f"[{i}/{len(jobs)} {time.time() - t0:.0f}s] edge {row['case']}/{row['mode']} seed {row['seed']}: "
                      f"rank {row['rank_accuracy']:.3f} id_err {row['id_errors']}", flush=True)
                continue
            rows.append(row)
            print(f"[{i}/{len(jobs)} {time.time() - t0:.0f}s] {row['scenario']}/{row['mode']}"
                  f"{'' if row['finish_cam'] else '/no-finish-cam'} seed {row['seed']}: "
                  f"order {'OK' if row['final_order_correct'] else 'WRONG'} "
                  f"rank {row['rank_accuracy']:.3f} fin {row['finish_err_ms_max']:.1f} ms "
                  f"id_err {row['id_errors']}", flush=True)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    rows.sort(key=lambda r: (r["scenario"], r["mode"], r["finish_cam"], r["seed"]))
    edges.sort(key=lambda r: (r["case"], r["mode"], r["seed"]))
    Path(a.out).write_text(json.dumps({"summary": summarize(rows), "runs": rows, "edge_cases": edges,
                                       "wall_time_s": time.time() - t0}, indent=1, default=float))
    for s in summarize(rows):
        print(s)


if __name__ == "__main__":
    main()
