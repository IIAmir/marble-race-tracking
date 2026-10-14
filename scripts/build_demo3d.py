"""Embed recorded replays into the 3D demo page.

    python scripts/export_replays.py --out results/replays.json
    python scripts/build_demo3d.py            # -> demo/track3d.html (self-contained apart from three.js CDN)
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def clean(x):
    """JSON for the browser: non-finite numbers become null."""
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    return x


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--replays", default=str(ROOT / "results/replays.json"))
    ap.add_argument("--out", default=str(ROOT / "demo/track3d.html"))
    a = ap.parse_args()
    data = clean(json.loads(Path(a.replays).read_text()))
    payload = json.dumps(data, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
    page = (ROOT / "demo/track3d.template.html").read_text().replace("/*REPLAYS*/", payload)
    Path(a.out).write_text(page)
    print(a.out, len(page) // 1024, "KiB")


if __name__ == "__main__":
    main()
