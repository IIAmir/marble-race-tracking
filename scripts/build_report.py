"""Build the production-readiness report from the measured results.

    python scripts/build_report.py --out docs/readiness-report.html

Every number on the page is read from results/*.json produced by
scripts/monte_carlo.py, scripts/benchmark.py, scripts/live_demo.py,
scripts/angled_accuracy.py and the pytest run (results/pytest.xml).
Acceptance gates are evaluated here, from those files, against thresholds
fixed in GATES below.
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import math
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "results"
DEMO_URL = "https://claude.ai/artifact/5UG8AbkwzGADygux82K1d5"
REPO_URL = "https://github.com/IIAmir/marble-race-tracking/tree/claude/tender-allen-hczmqo"

SCEN_LABEL = {
    "nominal": "Nominal race", "pack": "Tight pack", "occlusion": "Three tunnels",
    "camera_failure": "Two cameras fail", "network": "Frame loss + jitter", "lighting": "Lighting −20 %",
    "worst_case": "Worst case (all at once)",
}
MODE_LABEL = {"single": "1 overhead cam", "multi": "14 angled cams"}


def esc(s) -> str:
    return html.escape(str(s))


def load(name, default=None):
    p = RES / name
    return json.loads(p.read_text()) if p.exists() else default


def data_uri(path: Path, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


# ------------------------------------------------------------------ charts (inline SVG)

def dot_plot(rows, lo=80.0, hi=100.0):
    """Rows: (label, visible %, incl-hidden %). Two series, one shared x axis."""
    W, rh, left, right, top = 640, 30, 210, 24, 34
    H = top + rh * len(rows) + 34
    x = lambda v: left + (W - left - right) * (max(lo, min(hi, v)) - lo) / (hi - lo)
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Share of instants ranked correctly per scenario" class="chart">']
    for t in range(int(lo), int(hi) + 1, 5):
        out.append(f'<line x1="{x(t):.1f}" x2="{x(t):.1f}" y1="{top - 6}" y2="{H - 30}" class="grid"/>'
                   f'<text x="{x(t):.1f}" y="{H - 12}" class="tick" text-anchor="middle">{t}%</text>')
    for i, (label, vis, allv) in enumerate(rows):
        y = top + rh * i + rh / 2
        out.append(f'<text x="{left - 12}" y="{y + 4:.1f}" class="rowlabel" text-anchor="end">{esc(label)}</text>')
        out.append(f'<line x1="{x(min(vis, allv)):.1f}" x2="{x(max(vis, allv)):.1f}" y1="{y:.1f}" y2="{y:.1f}" class="link"/>')
        out.append(f'<circle cx="{x(allv):.1f}" cy="{y:.1f}" r="5.5" class="s2 ring" data-tip="{esc(label)}: {allv:.2f}% incl. hidden marbles"/>')
        out.append(f'<circle cx="{x(vis):.1f}" cy="{y:.1f}" r="5.5" class="s1 ring" data-tip="{esc(label)}: {vis:.2f}% of visible marbles"/>')
    out.append("</svg>")
    return "".join(out)


def ladder_chart(bins):
    """bins: list of (gap_ms, correct, flagged, wrong). Stacked horizontal bars, status colours."""
    W, rh, left, right, top = 640, 30, 90, 70, 10
    H = top + rh * len(bins) + 34
    n_max = max(c + f + w for _, c, f, w in bins) or 1
    sx = (W - left - right) / n_max
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Photo-finish outcomes by true gap" class="chart">']
    for t in range(0, n_max + 1, max(1, n_max // 5)):
        xx = left + t * sx
        out.append(f'<line x1="{xx:.1f}" x2="{xx:.1f}" y1="{top}" y2="{H - 30}" class="grid"/>'
                   f'<text x="{xx:.1f}" y="{H - 12}" class="tick" text-anchor="middle">{t}</text>')
    for i, (gap, c, f, w) in enumerate(bins):
        y = top + rh * i + 5
        out.append(f'<text x="{left - 12}" y="{y + 14}" class="rowlabel" text-anchor="end">{gap:g} ms</text>')
        xx = left
        for n, cls, name in ((c, "good", "called correctly"), (f, "warn", "flagged for review"), (w, "bad", "wrong, not flagged")):
            if n:
                wd = n * sx - 2
                out.append(f'<rect x="{xx:.1f}" y="{y}" width="{max(wd, 1):.1f}" height="{rh - 10}" rx="3" class="{cls}" '
                           f'data-tip="{gap:g} ms gap: {n} pair(s) {name}"/>')
                xx += n * sx
        total = c + f + w
        out.append(f'<text x="{xx + 8:.1f}" y="{y + 14}" class="tick">{c}/{total} auto</text>')
    out.append("</svg>")
    return "".join(out)


def z_hist(z):
    """Histogram of normalised pair-gap errors against the standard normal density."""
    W, H, left, right, top, bottom = 640, 240, 44, 16, 14, 34
    edges = np.arange(-4, 4.01, 0.5)
    counts, _ = np.histogram(np.clip(z, -3.999, 3.999), edges)
    dens = counts / max(1, len(z)) / 0.5
    ymax = max(0.5, float(dens.max()) * 1.1)
    sx = lambda v: left + (W - left - right) * (v + 4) / 8
    sy = lambda v: H - bottom - (H - top - bottom) * v / ymax
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Normalised finish-gap errors" class="chart">']
    for t in np.arange(0, ymax, 0.1):
        out.append(f'<line x1="{left}" x2="{W - right}" y1="{sy(t):.1f}" y2="{sy(t):.1f}" class="grid"/>'
                   f'<text x="{left - 6}" y="{sy(t) + 4:.1f}" class="tick" text-anchor="end">{t:.1f}</text>')
    for a, b, d, n in zip(edges[:-1], edges[1:], dens, counts):
        if n:
            out.append(f'<rect x="{sx(a) + 1:.1f}" y="{sy(d):.1f}" width="{sx(b) - sx(a) - 2:.1f}" height="{sy(0) - sy(d):.1f}" '
                       f'rx="2" class="s1" data-tip="{a:+.1f} to {b:+.1f} σ: {n} pairs"/>')
    xs = np.linspace(-4, 4, 161)
    pts = " ".join(f"{sx(v):.1f},{sy(math.exp(-v * v / 2) / math.sqrt(2 * math.pi)):.1f}" for v in xs)
    out.append(f'<polyline points="{pts}" class="curve"/>')
    for t in range(-4, 5):
        out.append(f'<text x="{sx(t):.1f}" y="{H - 12}" class="tick" text-anchor="middle">{t:+d}σ</text>')
    for t in (-3, 3):
        out.append(f'<line x1="{sx(t):.1f}" x2="{sx(t):.1f}" y1="{top}" y2="{H - bottom}" class="limit"/>')
    out.append("</svg>")
    return "".join(out)


def bench_chart(cases):
    W, rh, left, right, top = 640, 34, 290, 60, 10
    H = top + rh * len(cases) + 34
    vmax = max(max(c["ms_median"], c["budget_ms"]) for c in cases) * 1.15
    sx = lambda v: left + (W - left - right) * v / vmax
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Detector time per frame vs frame budget" class="chart">']
    step = 5 if vmax < 40 else 10
    for t in range(0, int(vmax) + 1, step):
        out.append(f'<line x1="{sx(t):.1f}" x2="{sx(t):.1f}" y1="{top}" y2="{H - 30}" class="grid"/>'
                   f'<text x="{sx(t):.1f}" y="{H - 12}" class="tick" text-anchor="middle">{t} ms</text>')
    for i, c in enumerate(cases):
        y = top + rh * i + 6
        out.append(f'<text x="{left - 12}" y="{y + 15}" class="rowlabel" text-anchor="end">{esc(c["name"])}</text>')
        out.append(f'<rect x="{left}" y="{y}" width="{sx(c["ms_median"]) - left:.1f}" height="{rh - 12}" rx="3" class="s1" '
                   f'data-tip="{esc(c["name"])}: {c["ms_median"]:.1f} ms median, {c["ms_p95"]:.1f} ms p95"/>')
        bx = sx(c["budget_ms"])
        out.append(f'<line x1="{bx:.1f}" x2="{bx:.1f}" y1="{y - 3}" y2="{y + rh - 9}" class="budget" '
                   f'data-tip="Frame budget at {c["fps"]} fps: {c["budget_ms"]:.1f} ms"/>')
        out.append(f'<text x="{max(sx(c["ms_median"]), bx) + 8:.1f}" y="{y + 15}" class="tick">{c["ms_median"]:.1f}</text>')
    out.append("</svg>")
    return "".join(out)


# ------------------------------------------------------------------ markdown (ARCHITECTURE.md §10)


def tests_summary():
    p = RES / "pytest.xml"
    if not p.exists():
        return None
    root = ET.parse(p).getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    total = int(suite.get("tests", 0))
    bad = int(suite.get("failures", 0)) + int(suite.get("errors", 0))
    return {"total": total, "passed": total - bad - int(suite.get("skipped", 0)), "failed": bad}


def collect():
    mc = load("monte_carlo.json")
    runs, summ, edges = mc["runs"], mc["summary"], mc["edge_cases"]
    d = {"mc": mc, "runs": runs, "summ": summ, "edges": edges, "bench": load("benchmark.json"),
         "live": {m: load(f"live_{m}.json") for m in ("single", "multi")}, "angled": load("angled_projection.json"),
         "tests": tests_summary()}
    allr = [r for r in runs if r["finish_cam"]] + edges
    d["n_races"] = len(runs) + len(edges)
    d["n_frames"] = sum(r["frames"] for r in runs)
    d["id_err"] = sum(r["id_errors"] for r in runs) + sum(e["id_errors"] for e in edges)
    d["official"] = sum(r["final_order_correct"] for r in allr)
    d["official_total"] = len(allr)
    d["phantom"] = sum(r["phantom_overtakes"] for r in runs) + sum(e["phantom_overtakes"] for e in edges)
    d["shown"] = sum(r["overtakes_shown"] for r in runs) + sum(e["overtakes_shown"] for e in edges)
    lat_all = [r["rank_accuracy_latency"] for r in allr]
    d["lat_min"], d["lat_mean"] = min(lat_all), float(np.mean(lat_all))
    d["strict_mean"] = float(np.mean([r["rank_accuracy"] for r in allr]))
    pf = [p for e in edges if e["case"] == "photo_finish" for p in e["pairs"]]
    d["pf"] = pf
    d["wrong_unflagged"] = sum(p["outcome"] == "WRONG_UNFLAGGED" for p in pf)
    zs = []
    for e in edges:
        if e["case"] != "photo_finish":
            continue
        z, sg = np.array(e["z"]), np.array(e["sigma_ms"])
        err = z * sg
        for k in range(len(z) // 2):
            a_, b_ = 2 * k, 2 * k + 1
            zs.append((err[a_] - err[b_]) / math.hypot(sg[a_], sg[b_]))
    d["zs"] = np.array(zs)
    d["fin_err"] = [v for e in edges if e["case"] == "photo_finish" for v in e["finish_err_ms"]]
    d["sig"] = [v for e in edges if e["case"] == "photo_finish" for v in e["sigma_ms"]]
    for case in ("corner_duel", "backward_slip", "fast_handoff"):
        d[case] = [e for e in edges if e["case"] == case]
    cf = [r for r in runs if r["scenario"] == "camera_failure"]
    d["cf_ok"] = all(r["final_order_correct"] for r in cf) and sum(r["id_errors"] for r in cf) == 0
    return d


def gates(d):
    """(area, what is checked, threshold, measured text, passed)."""
    out = []
    add = lambda *g: out.append(g)
    add("Identity", "No marble ever takes another marble's name", "0 errors",
        f"{d['id_err']} in {d['n_races']} races", d["id_err"] == 0)
    add("Official result", "Final order correct; pairs too close to call are flagged for review", "100 % of races",
        f"{d['official']}/{d['official_total']}", d["official"] == d["official_total"])
    add("No phantom overtakes", "Every pass shown on the board happened on the track", "0",
        f"{d['phantom']} of {d['shown']:,} displayed passes", d["phantom"] == 0)
    add("Live ranking", "Visible marbles in true order, 100 ms display budget", "≥ 99.5 % in every race",
        f"worst race {100 * d['lat_min']:.2f} %, mean {100 * d['lat_mean']:.3f} %", d["lat_min"] >= 0.995)
    nf = sum(p["outcome"] == "flagged" for p in d["pf"])
    add("Photo finishes", "Wrong finish calls that were not flagged", "0",
        f"{d['wrong_unflagged']} of {len(d['pf'])} ({nf} flagged, {len(d['pf']) - nf - d['wrong_unflagged']} called)",
        d["wrong_unflagged"] == 0)
    nb = int(np.sum(np.abs(d["zs"]) > 3))
    add("Honest uncertainty", "Finish-gap errors beyond 3σ of the stated uncertainty", "≤ 1 %",
        f"{nb} of {len(d['zs'])}", nb <= 0.01 * max(1, len(d["zs"])))
    cd = d["corner_duel"]
    cd_ok = sum(e["overtake_flips"] == 1 for e in cd)
    dith = sum(e["dither_flips"] == 0 for e in cd)
    add("Corner micro-overtakes", "Real pass shown exactly once; ±8 mm dead heat never flickers", "every race",
        f"{cd_ok}/{len(cd)} and {dith}/{len(cd)}", cd_ok == len(cd) and dith == len(cd))
    bs = d["backward_slip"]
    lost = sum(e["lost_marble_instants"] for e in bs)
    add("Backward slips", "Marbles lost through bounces and reversals", "0", f"{lost} in {len(bs)} races", lost == 0)
    ap = d["angled"]
    if ap:
        add("Angled-view geometry", "Progress error over 14 cameras at ~45°, all lanes", "p95 ≤ 3 mm",
            f"median {ap['oblique']['median_mm']:.2f} mm, p95 {ap['oblique']['p95_mm']:.2f} mm", ap["oblique"]["p95_mm"] <= 3.0)
    add("Camera failure", "Two cameras die mid-race", "results correct, 0 ID errors",
        "all races correct" if d["cf_ok"] else "failures", d["cf_ok"])
    lm = d["live"].get("multi")
    if lm:
        p95 = lm["capture_to_fusion_ms"]["p95"]
        add("Latency", "Capture → fused leaderboard, 15 cameras live", "p95 ≤ 150 ms", f"p95 {p95:.0f} ms", p95 <= 150)
    b = d["bench"]
    if b:
        cores = b["sizing"]["mode_b_cores_14_cams_at_60fps"]
        add("Compute", "Detection for 14 cameras at 60 fps, at the processing resolution the suite validated", "≤ 16 cores (4 edge PCs)",
            f"{cores:.1f} cores", cores <= 16)
    t = d["tests"]
    if t:
        add("Invariants and unit tests", "Property-based fuzzing + unit tests", "all pass", f"{t['passed']}/{t['total']}",
            t["failed"] == 0 and t["passed"] == t["total"])
    return out


def write_markdown_summary(d, g):
    md = [f"Measured by `scripts/monte_carlo.py` ({d['n_races']} races, {d['n_frames']:,} camera frames), "
          "`scripts/angled_accuracy.py`, `scripts/benchmark.py`, `scripts/live_demo.py` and the test suite; "
          "regenerated by `scripts/build_report.py`.", "",
          "| Gate | Threshold | Measured | Result |", "|---|---|---|---|"]
    md += [f"| {area}: {what} | {thr} | {meas} | {'PASS' if ok else 'FAIL'} |" for area, what, thr, meas, ok in g]
    md += ["", "| Scenario · cameras | Races | Official correct | Rank strict (mean) | Rank, 100 ms budget (worst race) | Phantom overtakes | Incl. hidden | ID errors |",
           "|---|---|---|---|---|---|---|---|"]
    for name, s in scenario_rows(d):
        md.append(f"| {name} | {s['races']} | {s['official_order_correct']}/{s['races']} | {100 * s['rank_accuracy_visible_mean']:.2f}% | "
                  f"{100 * s['rank_accuracy_latency_min']:.2f}% | {s['phantom_overtakes_total']} | {100 * s['rank_accuracy_all_mean']:.1f}% | "
                  f"{s['id_errors_total']} |")
    p = ROOT / "docs/ARCHITECTURE.md"
    text = p.read_text()
    a, b = text.index("<!-- RESULTS:START -->"), text.index("<!-- RESULTS:END -->")
    p.write_text(text[: a + len("<!-- RESULTS:START -->")] + "\n" + "\n".join(md) + "\n" + text[b:])


def scenario_rows(d):
    order = list(SCEN_LABEL)
    rows = []
    for s in sorted(d["summ"], key=lambda s: (order.index(s["scenario"]), s["mode"], not s["finish_cam"])):
        name = f'{SCEN_LABEL[s["scenario"]]} · {MODE_LABEL[s["mode"]]}' + ("" if s["finish_cam"] else " · no finish cam")
        rows.append((name, s))
    return rows


# ------------------------------------------------------------------ page

def main():
    ap_ = argparse.ArgumentParser()
    ap_.add_argument("--out", default=str(ROOT / "docs/readiness-report.html"))
    ap_.add_argument("--no-media", action="store_true")
    a = ap_.parse_args()

    d = collect()
    G = gates(d)
    all_pass = all(g[4] for g in G)
    write_markdown_summary(d, G)
    rows = scenario_rows(d)
    bench, live, angled = d["bench"], d["live"], d["angled"]

    media = {}
    if not a.no_media:
        for key, fn, mime in (("v_single", "demo_single.mp4", "video/mp4"), ("v_multi", "demo_multi_camera_failure.mp4", "video/mp4"),
                              ("angled", "stills/angled_mosaic.jpg", "image/jpeg")):
            if (RES / fn).exists():
                media[key] = data_uri(RES / fn, mime)

    lm = live.get("multi") or {}
    tiles = [
        (f"{sum(g[4] for g in G)}/{len(G)}", "acceptance gates passed", "thresholds fixed before the final run"),
        (f"{d['n_races']}", "simulated races", f"{d['n_frames']:,} camera frames through the full pipeline"),
        (f"{d['id_err']}", "identity errors", "a marble never took another marble's name"),
        (f"{d['phantom']}", "phantom overtakes", f"out of {d['shown']:,} passes shown on the board"),
        (f"{d['official']}/{d['official_total']}", "official results correct", "too-close finishes go to review"),
    ]
    if lm:
        tiles.append((f"{lm['capture_to_fusion_ms']['p95']:.0f} ms", "capture → leaderboard (p95)", "15 cameras live, incl. 50 ms reorder window"))

    verdict = ("All simulated acceptance gates pass. The software is ready for hardware integration and on-site acceptance."
               if all_pass else "Not all gates pass. See the failing rows below before proceeding.")
    S = []
    S.append(f"""
<header class="hero">
  <p class="eyebrow">Production readiness report · computer vision · October 2026</p>
  <h1>Marble race tracking: {'every simulated production gate passes' if all_pass else 'readiness gates not yet met'}</h1>
  <p class="lede">The complete system (colour detection, multi-camera fusion, occlusion handling, finish timing, live leaderboard) was run against {d['n_races']} randomized and adversarial races, rendered by a 3D camera simulator with fourteen cameras mounted at about 45°, track walls, tunnels, perspective and marbles hiding each other. {esc(verdict)}</p>
  <div class="links"><a class="btn primary" href="{DEMO_URL}" target="_blank" rel="noopener">Open the interactive 3D replay</a><a class="btn" href="{REPO_URL}" target="_blank" rel="noopener">Source code and raw results</a></div>
  <div class="tiles">{''.join(f'<div class="tile"><strong>{v}</strong><span>{k}</span><small>{x}</small></div>' for v, k, x in tiles)}</div>
</header>""")

    S.append(f"""
<section id="gates">
  <h2>Acceptance gates</h2>
  <p class="sub">Each gate has a threshold set before the final run; the result is computed from the result files by the script that builds this page.</p>
  <div class="tablewrap"><table class="data gates">
    <thead><tr><th>Area</th><th>What is checked</th><th>Threshold</th><th>Measured</th><th>Result</th></tr></thead>
    <tbody>{''.join(f'<tr><td><b>{esc(ar)}</b></td><td>{esc(w)}</td><td>{esc(t)}</td><td class=n>{esc(m)}</td><td>{"<span class=\"chip good\">✓ PASS</span>" if ok else "<span class=\"chip bad\">✕ FAIL</span>"}</td></tr>' for ar, w, t, m, ok in G)}</tbody>
  </table></div>
</section>""")

    S.append(f"""
<section id="approach">
  <h2>How it works</h2>
  <div class="two">
    <div>
      <p>Every marble has a unique colour. Each camera finds marble-coloured pixels against the empty-track background, groups them by colour and maps each blob onto a calibrated centerline. From then on a marble is a single number: <b>progress along the track in metres</b>.</p>
      <p>Because everything is in track metres, one or fourteen cameras is only configuration: a hand-off between cameras needs no special logic, tunnels and camera gaps are intervals on the track, and the leaderboard is a sort.</p>
      <ul class="facts">
        <li><b>Identity</b>: per-camera colour models in CIELAB, illumination-compensated every frame; a detection goes to a marble only if its colour is within reach of its best match.</li>
        <li><b>Geometry</b>: the centerline is calibrated at marble-centre height, and each blob is decomposed along and across the track using the camera's own perspective skew.</li>
        <li><b>Tracking</b>: one Kalman filter per marble on progress and speed, fed by every camera, with out-of-order frames and sudden reversals handled.</li>
        <li><b>Ranking</b>: an overtake is shown only when it is seen, larger than half a marble plus 2σ, and (near that threshold) held for 100 ms. Nobody passes a marble whose position is uncertain.</li>
        <li><b>Finish</b>: a least-squares fit of the frames around the line gives each time and an honest uncertainty; close calls are flagged.</li>
      </ul>
    </div>
    <div class="diagram">
<pre class="mermaid">
flowchart TB
  A[Camera frame · PTP time] --> B[Illumination gain +<br/>background subtraction]
  B --> C[Per-pixel colour match<br/>CIELAB]
  C --> D[Whole blob → oblique projection<br/>→ progress s, lateral u]
  D -- "~300 bytes / frame" --> E[Fusion: colour + motion assignment<br/>Kalman filter per marble]
  E --> F[Leaderboard: confirmed, seen overtakes only]
  F --> G[Broadcast overlay · results]
</pre>
    </div>
  </div>
</section>""")

    if angled:
        S.append(f"""
<section id="angled">
  <h2>Fourteen cameras at 45°</h2>
  <p class="sub">The 14 segment cameras are simulated as real pinhole cameras roughly 0.4–0.9 m from the track and about 45° above it, looking from behind, ahead, both sides and the diagonals. A depth buffer handles every occlusion.</p>
  {'<figure class="still"><img src="' + media['angled'] + '" alt="Fourteen simulated angled camera views of the marble track"><figcaption>The fourteen simulated camera views at one instant of a race.</figcaption></figure>' if 'angled' in media else ''}
  <div class="two">
    <ul class="facts">
      <li><b>Perspective scale.</b> The same marble spans 7–29 px depending on where it is in the view. Expected blob size is tabulated along each camera's section at calibration.</li>
      <li><b>Height parallax.</b> A marble's centre is 12.5 mm above the track. The centerline is recorded at marble-centre height (roll a marble along it), so parallax cancels.</li>
      <li><b>Skew.</b> Seen at an angle, the cross-track direction is not perpendicular to the track's image. Plain nearest-pixel projection errs by up to {angled['nearest_pixel']['max_mm']:.0f} mm for marbles in the outer lanes; the oblique projection brings that to a median of {angled['oblique']['median_mm']:.1f} mm (p95 {angled['oblique']['p95_mm']:.1f} mm) over {angled['placements']} placements.</li>
      <li><b>Walls and line-of-sight occlusion.</b> Track walls hide the lower part of marbles in the near lane, and marbles lined up along the line of sight hide each other. Partly hidden blobs carry a larger, correlated uncertainty; fully hidden marbles cannot gain places and cannot be passed on prediction.</li>
      <li><b>Section edges.</b> In an angled view a marble just past the end of a camera's section can poke into it. Only blobs at least 85 % inside the section are accepted, so nothing is reported from a clipped fragment.</li>
      <li><b>Lighting.</b> Under a 20 % dimming, the light-grey walls in these views drifted from the stored background and produced phantom detections. Per-frame illumination gain on background and colours removed them.</li>
    </ul>
    <div>
      <div class="tiles small">
        <div class="tile"><strong>{angled['oblique']['median_mm']:.1f} mm</strong><span>median progress error</span><small>p95 {angled['oblique']['p95_mm']:.1f} mm · max {angled['oblique']['max_mm']:.1f} mm</small></div>
        <div class="tile"><strong>{angled['nearest_pixel']['max_mm']:.1f} mm</strong><span>without oblique projection</span><small>max error, the skew this removes</small></div>
        <div class="tile"><strong>{angled['missed']}/{angled['placements']}</strong><span>placements not detected</span><small>marble behind the near wall from a low view; tracked through by coasting</small></div>
      </div>
    </div>
  </div>
</section>""")

    if "v_single" in media or "v_multi" in media:
        S.append(f"""
<section id="demo">
  <h2>What the system sees</h2>
  <p class="sub">Recorded from the pipeline. Camera views with each detection labelled by identity; on the right, the live leaderboard with “ok” where it matches ground truth.</p>
  <div class="two media">
    {'<figure><video src="' + media['v_multi'] + '" autoplay muted loop playsinline controls></video><figcaption>Fourteen angled cameras. Cameras 5 and 10 fail mid-race; their sections turn red on the track map and the marbles there are held as hidden until they reappear.</figcaption></figure>' if 'v_multi' in media else ''}
    {'<figure><video src="' + media['v_single'] + '" autoplay muted loop playsinline controls></video><figcaption>Alternative rig: one 2.3 MP overhead camera above a 3 × 2 m table, plus a finish camera.</figcaption></figure>' if 'v_single' in media else ''}
  </div>
</section>""")

    dots = [(n, 100 * s["rank_accuracy_visible_mean"], 100 * s["rank_accuracy_all_mean"]) for n, s in rows if s["finish_cam"]]
    lo = math.floor(min(min(v, w) for _, v, w in dots) / 5) * 5

    def row_html(name, s):
        ok = s["official_order_correct"] == s["races"]
        chip = '<span class="chip good">✓ all</span>' if ok else f'<span class="chip bad">✕ {s["races"] - s["official_order_correct"]} wrong</span>'
        return (f"<tr><td>{esc(name)}</td><td class=n>{s['races']}</td><td>{chip}</td>"
                f"<td class=n>{100 * s['rank_accuracy_visible_mean']:.2f}%</td><td class=n>{100 * s['rank_accuracy_latency_min']:.2f}%</td>"
                f"<td class=n>{s['phantom_overtakes_total']}</td><td class=n>{100 * s['rank_accuracy_all_mean']:.1f}%</td>"
                f"<td class=n>{s['races_with_photo_finish_review']}</td><td class=n>{s['finish_err_ms_p95_of_max']:.2f}</td>"
                f"<td class=n>{s['id_errors_total']}</td></tr>")

    S.append(f"""
<section id="stress">
  <h2>Stress suite: {len(d['runs'])} randomized races</h2>
  <p class="sub">Ten random races per scenario and rig. Each race has ten marbles, overtakes throughout, unsynchronised cameras, sensor noise and per-camera colour casts. The board is scored against ground truth 30 times per second.</p>
  <figure class="chartbox">
    <figcaption><b>Share of instants with every marble ranked correctly</b>
      <span class="legend"><i class="s1"></i>visible marbles <i class="s2"></i>including marbles hidden in tunnels or dead camera sections</span></figcaption>
    {dot_plot(dots, lo=lo)}
  </figure>
  <p class="note">Hidden marbles are the only real source of disagreement. Inside a tunnel nobody can see who leads; the board shows those marbles as hidden and holds them until they reappear. Among visible marbles the strict score counts a correct overtake as wrong for the few frames before it is confirmed; within the 100 ms display budget used for broadcast graphics every race scores at least {100 * d['lat_min']:.2f} %.</p>
  <div class="tablewrap"><table class="data">
    <thead><tr><th>Scenario · rig</th><th>Races</th><th>Official result</th><th>Rank, strict (mean)</th><th>Rank, 100 ms budget (worst race)</th><th>Phantom overtakes</th><th>Incl. hidden</th><th>Photo reviews</th><th>Finish error p95 (ms)</th><th>ID errors</th></tr></thead>
    <tbody>{''.join(row_html(n, s) for n, s in rows)}</tbody>
  </table></div>
</section>""")

    by_gap = defaultdict(lambda: [0, 0, 0])
    for p in d["pf"]:
        by_gap[round(p["gap_ms"], 2)][{"correct": 0, "flagged": 1}.get(p["outcome"], 2)] += 1
    bins = [(g, *by_gap[g]) for g in sorted(by_gap)]
    resolved_from = min((g for g, c, f, w in bins if all(ff == 0 and ww == 0 for gg, _, ff, ww in bins if gg >= g)), default=None)
    cd, bs, fh = d["corner_duel"], d["backward_slip"], d["fast_handoff"]
    cd_lat = [e["overtake_display_latency_ms"] for e in cd if math.isfinite(e["overtake_display_latency_ms"])]
    S.append(f"""
<section id="edge">
  <h2>Formula 1-grade edge cases</h2>
  <p class="sub">Scripted races that each target one failure mode, ten random variations per case and rig, on the angled cameras.</p>
  <div class="cards">
    <article class="card"><h3>Millisecond photo finishes</h3>
      <p>Pairs crossing side by side, 0.1–8 ms apart, filmed by a 240 fps finish camera beside the line at 45°, where the near marble partly hides the far one. Typical finish-time error {np.median(d['fin_err']):.2f} ms.</p>
      <p class="big">{d['wrong_unflagged']} <span>wrong unflagged calls out of {len(d['pf'])}</span></p></article>
    <article class="card"><h3>Micro-overtakes in a corner</h3>
      <p>Inside line passes outside line by 3 cm through a 180° turn while another pair dithers ±8 mm around a dead heat.</p>
      <p class="big">{sum(e['overtake_flips'] == 1 for e in cd)}/{len(cd)} <span>overtakes shown exactly once</span></p>
      <p class="big">{sum(e['dither_flips'] == 0 for e in cd)}/{len(cd)} <span>dead heats with zero flicker</span></p>
      <p class="small">Shown {np.median(cd_lat):.0f} ms (median) after the lead exceeds half a marble.</p></article>
    <article class="card"><h3>Backward slips and bounces</h3>
      <p>Marbles reverse at up to 1 m/s on a straight, in a corner and just past the finish line while rivals pass.</p>
      <p class="big">{sum(e['lost_marble_instants'] for e in bs)} <span>lost marbles in {len(bs)} races</span></p>
      <p class="small">First crossing timed within {max(e['finish_first_crossing_err_ms'] for e in bs):.1f} ms. Peak error {max(e['max_pos_err_cm'] for e in bs):.1f} cm for one frame at the reversal.</p></article>
    <article class="card"><h3>Rapid hand-offs</h3>
      <p>3.0–3.5 m/s through 14 angled cameras with 3 cm overlap, 30 fps and ±1 cm calibration disagreement between neighbours.</p>
      <p class="big">{sum(e['handoffs'] for e in fh):,} <span>hand-offs, {sum(e['id_errors'] for e in fh)} identity errors</span></p>
      <p class="small">Worst race within the display budget: {100 * min(e['rank_accuracy_latency'] for e in fh):.1f} %.</p></article>
  </div>
  <div class="two">
    <figure class="chartbox">
      <figcaption><b>Photo-finish outcomes by true gap</b>
        <span class="legend"><i class="good"></i>✓ called correctly <i class="warn"></i>⚑ flagged for review <i class="bad"></i>✕ wrong, not flagged</span></figcaption>
      {ladder_chart(bins)}
      <p class="note">{("Gaps of " + format(resolved_from, "g") + " ms and more are always called automatically. ") if resolved_from else ""}Closer finishes are reported as too close to call and go to the race steward with the 240 fps frames.</p>
    </figure>
    <figure class="chartbox">
      <figcaption><b>Is the stated uncertainty honest?</b> <span class="legend"><i class="s1"></i>measured gap error ÷ stated σ <i class="curve-key"></i>ideal (standard normal)</span></figcaption>
      {z_hist(d['zs'])}
      <p class="note">{len(d['zs'])} finish pairs; {int(np.sum(np.abs(d['zs']) > 3))} beyond ±3σ, the flagging threshold. The stated σ includes occlusion bias that is shared between frames and so cannot average away.</p>
    </figure>
  </div>
</section>""")

    guarantees = [
        ("One identity per detection, one detection per marble per frame", "Hungarian assignment with colour ratio test; fuzz-tested every frame"),
        ("No marble gains a place while it is hidden", "Rank rule; fuzz-tested every frame"),
        ("Nobody passes a marble whose position is uncertain", "Pass threshold includes the hidden marble's growing variance; chain rule keeps visible passes"),
        ("No rank flicker below half a marble's width", "Margin + 2σ, and 100 ms confirmation near the threshold"),
        ("Finish order is correct or explicitly flagged", "Flag below 3σ of the two fitted times; provisional times claim no better than half a frame"),
        ("Corrupt input never reaches the filter", "NaN/inf, wrong palette size, empty candidates dropped; fuzz-tested"),
        ("Filter covariance stays symmetric positive semi-definite", "Checked after every update in fuzzing"),
        ("Bounded latency", "Fixed 50 ms reorder window; edge nodes drop late frames instead of queueing"),
    ]
    S.append(f"""
<section id="guarantees">
  <h2>What is guaranteed, and how we know</h2>
  <p class="sub">These properties hold by construction. Property-based fuzzing checks them after every frame of hundreds of random input streams with clutter, ambiguous colours, duplicate and out-of-order frames, NaNs and silent cameras.</p>
  <div class="tablewrap"><table class="data"><thead><tr><th>Invariant</th><th>Mechanism and check</th></tr></thead>
  <tbody>{''.join(f'<tr><td>{esc(x)}</td><td>{esc(y)}</td></tr>' for x, y in guarantees)}</tbody></table></div>
</section>""")

    if bench:
        ls = live.get("single") or {}
        S.append(f"""
<section id="realtime">
  <h2>Real time, on ordinary hardware</h2>
  <div class="two">
    <figure class="chartbox">
      <figcaption><b>Edge detector time per frame</b> (one CPU core, Python + OpenCV) <span class="legend"><i class="s1"></i>median time <i class="budget-key"></i>frame budget at the camera's rate</span></figcaption>
      {bench_chart(bench['cases'])}
      <p class="note">Segment cameras are processed at 720×540, half of their 1440×1080 sensor. The stress suite ran its cameras at 640×480, so that is the resolution it validated. Fourteen of them at 60 fps need about {bench['sizing']['mode_b_cores_14_cams_at_60fps']:.1f} cores in total. The 240 fps finish camera needs about {bench['sizing'].get('finish_cores_at_240fps', 0):.1f} cores and gets an edge PC of its own. Fusion of one camera frame plus a leaderboard update takes {bench['fusion_ms_per_frame_median']:.2f} ms on one core.</p>
    </figure>
    <div>
      <h3>Live run: the full system in wall-clock time</h3>
      <p>Edge-node processes grab frames from the virtual cameras on the clock, detect, and stream detections to the fusion process, which publishes the leaderboard ten times a second.</p>
      <div class="tiles small">
        {f'<div class="tile"><strong>{lm["capture_to_fusion_ms"]["p50"]:.0f} / {lm["capture_to_fusion_ms"]["p95"]:.0f} ms</strong><span>capture → fused, p50 / p95</span><small>{lm["cameras"]} angled cameras, {lm["edge_nodes"]} edge processes; includes the 50 ms reorder window</small></div>' if lm else ''}
        {f'<div class="tile"><strong>{100 * lm["live_rank_accuracy_visible"]:.1f}%</strong><span>live board correct</span><small>{lm["frames_processed"]:,} frames, {lm["frames_dropped_realtime"]} dropped as late</small></div>' if lm else ''}
        {f'<div class="tile"><strong>{ls["capture_to_fusion_ms"]["p95"]:.0f} ms</strong><span>overhead-camera rig, p95</span><small>{ls["frames_processed"]:,} frames, {ls["frames_dropped_realtime"]} dropped</small></div>' if ls else ''}
      </div>
      <p class="note">Measured on a {bench['machine']['cores']}-core cloud container that also renders all 15 virtual 3D camera images in Python. That rendering, not detection, saturates the container, so edge nodes drop late frames ({lm.get("frames_dropped_realtime", 0):,} here) to keep latency bounded. Real cameras deliver frames without that cost.</p>
    </div>
  </div>
</section>""")

    S.append("""
<section id="hardware">
  <h2>Hardware</h2>
  <div class="two">
    <div>
      <h3>Recommended rig</h3>
      <div class="tablewrap"><table class="data">
        <thead><tr><th>Role</th><th>Camera class</th><th>Example models</th><th>Approx. price</th></tr></thead>
        <tbody>
          <tr><td>Segment camera ×14, mounted ~45°</td><td>1.6 MP colour global shutter, ~75 fps, GigE PoE, PTP</td><td>FLIR Blackfly S BFS-PGE-16S2C · LUCID Triton TRI016S-CC · Allied Vision Alvium G1-158c</td><td>$370–520</td></tr>
          <tr><td>Finish camera, beside the line</td><td>Same class in ROI mode, ≥ 200 fps</td><td>IMX273 models in ROI mode · Basler a2A1920-160uc</td><td>$350–500</td></tr>
          <tr><td>Overhead camera (alternative rig)</td><td>2.3 MP colour global shutter, 51 fps</td><td>Basler ace 2 a2A1920-51gcBAS</td><td>$380–490</td></tr>
          <tr><td>Lens (each)</td><td>Fixed C-mount, 3.5–7 mm</td><td>Computar, Basler</td><td>$130–250</td></tr>
        </tbody></table></div>
      <p class="note">Distributor list prices found during research; re-quote before purchase. A 4K camera does not help at this price: 12 MP sensors are rolling-shutter and run 10–31 fps.</p>
    </div>
    <div>
      <h3>Budget</h3>
      <div class="tablewrap"><table class="data">
        <thead><tr><th></th><th>14 angled + finish camera</th><th>Overhead + finish camera</th></tr></thead>
        <tbody>
          <tr><td>Cameras and lenses</td><td class=n>$8–11k</td><td class=n>$1.0–1.5k</td></tr>
          <tr><td>Edge compute, PoE NICs, PTP switch, cabling, mounts</td><td class=n>$5–7.5k</td><td class=n>$1–2k</td></tr>
          <tr><td>Flicker-free LED lighting</td><td class=n>$1–2k</td><td class=n>$1–1.5k</td></tr>
          <tr class="total"><td>Total (indicative)</td><td class=n>$15–20k</td><td class=n>$3–5k</td></tr>
        </tbody></table></div>
      <p class="note">Both rigs run the same software. The angled rig suits large, multi-level or overhanging tracks and gives close-up broadcast views.</p>
    </div>
  </div>
</section>""")

    S.append("""
<section id="limits">
  <h2>Ready for what, exactly</h2>
  <div class="two">
    <div>
      <h3>Verified in simulation</h3>
      <ul class="facts">
        <li>Identity, association, fusion, occlusion logic, ranking rules and finish timing, end to end against exact ground truth.</li>
        <li>Angled-camera geometry: perspective, skew, parallax, walls and line-of-sight occlusion.</li>
        <li>Failure handling: dead cameras, frame loss, network jitter, lighting drift.</li>
        <li>Real-time operation with separate edge and fusion processes.</li>
      </ul>
      <h3>Needs on-site acceptance</h3>
      <ul class="facts">
        <li><b>Real marbles and optics.</b> Glass swirls, transparency and reflections of neighbours are not simulated. Calibration measures every colour pair's separation before race day.</li>
        <li><b>Real lighting.</b> Slow drift is handled; flicker is not. Flicker-free DC LED lighting is a requirement.</li>
        <li><b>Collisions.</b> Simulated marbles never knock each other off the track. A marble that leaves is reported lost and re-acquired by colour.</li>
        <li><b>Absolute timing offset.</b> One-sided lighting shifts every marble's visible centre equally (about 0.2 ms at the line). It does not change the order; a single light-gate run calibrates it out.</li>
      </ul>
    </div>
    <div>
      <h3>Path to go-live, about 11–12 weeks</h3>
      <ol class="phases">
        <li><b>Hardware bring-up (2 weeks).</b> Two segment cameras, finish camera, lighting, camera SDK adapter, PTP.</li>
        <li><b>Field trial on one segment (2 weeks).</b> Real marbles at 45°: identity ≥ 99.5 % on 20 annotated runs; every colour pair separable.</li>
        <li><b>Full rig (3 weeks).</b> All cameras calibrated; live overlay with p95 latency ≤ 150 ms; camera-failure drill.</li>
        <li><b>Acceptance (2 weeks).</b> 50 recorded races against hand-annotated truth, the same gates as above.</li>
        <li><b>Hardening (2–3 weeks).</b> C++ hot loop, monitoring, frame archive for audit, 4-hour soak test.</li>
      </ol>
    </div>
  </div>
</section>""")

    S.append("""
<section id="reproduce">
  <h2>Reproduce every number</h2>
  <div class="tablewrap"><pre class="cmd">pip install -r requirements.txt
python -m pytest --junitxml results/pytest.xml       # unit + property-based invariant tests
python scripts/monte_carlo.py --seeds 10             # stress suite + edge cases -> results/monte_carlo.json
python scripts/angled_accuracy.py                    # angled-view geometry     -> results/angled_projection.json
python scripts/benchmark.py                          # compute per frame        -> results/benchmark.json
python scripts/live_demo.py --mode multi --out results/live_multi.json   # real-time run
python scripts/build_report.py                       # this page</pre></div>
</section>""")

    page = (ROOT / "docs/report.template.html").read_text().replace("<!--SECTIONS-->", "\n".join(S))
    Path(a.out).write_text(page)
    print(a.out, len(page) // 1024, "KiB", "ALL GATES PASS" if all_pass else "GATES FAILING: " + ", ".join(g[0] for g in G if not g[4]))


if __name__ == "__main__":
    main()
