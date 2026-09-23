#!/usr/bin/env bash
# Nightly run: scan the market, then build report.html from results.json.
set -euo pipefail
cd "$(dirname "$0")"
python3 -m pip install -q -r requirements.txt
python3 scanner.py | tee run.log
python3 build_page.py results.json report.html
