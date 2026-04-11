#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path

SRC = Path("${HOME}/jellyfin-novita-agent/views_90d.real.json")
DST = Path("${HOME}/jellyfin-novita-agent/views_90d.clean.json")

BAD = [
    "tgr",
    "tg ",
    "rainews",
    "rai 1", "rai 2", "rai 3", "rai 4", "rai 5",
    "canale 5", "italia 1", "rete 4",
    "la7", "tv8", "nove",
    "meteo",
]

def keep(item: dict) -> bool:
    title = str(item.get("title") or "").strip().lower()
    series = str(item.get("series_name") or "").strip().lower()
    genres = item.get("genres") or []
    duration = int(item.get("play_duration") or 0)

    hay = f"{title} {series}"

    if any(x in hay for x in BAD):
        return False
    if not genres and not series:
        return False
    if duration and duration < 600:
        return False
    return True

def main() -> int:
    rows = json.loads(SRC.read_text(encoding="utf-8"))
    out = [r for r in rows if keep(r)]
    DST.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "src": str(SRC),
        "dst": str(DST),
        "before": len(rows),
        "after": len(out),
    }, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
