#!/usr/bin/env bash
# Regenerate every measured artefact after the stress suite (results/monte_carlo.json) has run.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/live_demo.py --mode multi --nodes 3 --quiet --out results/live_multi.json > /dev/null
python3 scripts/live_demo.py --mode single --nodes 1 --quiet --out results/live_single.json > /dev/null
python3 scripts/benchmark.py --out results/benchmark.json
python3 scripts/angled_accuracy.py --out results/angled_projection.json
python3 -m pytest -q --junitxml results/pytest.xml | tail -1
python3 scripts/render_demo.py --mode multi --scenario camera_failure --out results/demo_multi_camera_failure.mp4 --stills results/stills
python3 scripts/render_demo.py --mode single --out results/demo_single.mp4
python3 -c "import cv2; im=cv2.imread('results/stills/multi_camera_failure_t4.0.png'); cv2.imwrite('results/stills/angled_mosaic.jpg', cv2.resize(im, None, fx=0.75, fy=0.75), [cv2.IMWRITE_JPEG_QUALITY, 82])"
python3 scripts/export_replays.py --out results/replays.json
python3 scripts/build_demo3d.py
python3 scripts/build_report.py --out docs/readiness-report.html
