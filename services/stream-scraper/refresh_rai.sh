#!/usr/bin/env bash
set -euo pipefail

sudo docker exec -i cheshire_cat_core python - <<'PY'
import importlib.util
from pathlib import Path

p = Path("/app/cat/plugins/stream_scraper/main.py")
spec = importlib.util.spec_from_file_location("stream_scraper_main", p)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

channels = [
    "Rai1", "Rai2", "Rai3", "Rai4", "Rai5",
    "RaiMovie", "RaiPremium", "RaiGulp", "RaiYoyo",
    "RaiStoria", "RaiNews24", "RaiSport", "RaiScuola"
]

for cid in channels:
    try:
        print(cid, "->", mod.update_single_channel(cid))
    except Exception as e:
        print(cid, "-> ERROR:", repr(e))
PY
