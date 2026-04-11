import json
import requests
from pathlib import Path

CFG_PATH = Path("${HOME}/jellyfin-novita-agent/config.json")
OUT_PATH = Path("${HOME}/jellyfin-novita-agent/views_90d.real.json")

cfg = json.loads(CFG_PATH.read_text(encoding="utf-8"))
jf_url = cfg["jellyfin_url"].rstrip("/")
jf_token = cfg["jellyfin_token"]

users = requests.get(f"{jf_url}/Users", params={"api_key": jf_token}, timeout=20).json()

events = []

for user in users:
    user_id = user["Id"]
    user_name = user["Name"]

    r = requests.get(
        f"{jf_url}/Users/{user_id}/Items",
        params={
            "IncludeItemTypes": "Episode,Movie",
            "Recursive": "true",
            "SortBy": "DatePlayed",
            "Filters": "IsPlayed",
            "Limit": "2000",
            "api_key": jf_token,
        },
        timeout=30,
    ).json()

    for item in r.get("Items", []):
        played_at = item.get("DatePlayed")
        if not played_at:
            continue

        item_type = item.get("Type")
        kind = "series" if item_type == "Episode" else "movie"

        title = item.get("SeriesName") if kind == "series" else item.get("Name")
        if not title:
            continue

        genres = item.get("Genres") or []

        events.append({
            "user_id": f"jf_{user_id}",
            "user_name": user_name,
            "item_id": item.get("Id"),
            "title": title,
            "kind": kind,
            "genres": genres,
            "series_name": item.get("SeriesName"),
            "season_number": item.get("ParentIndexNumber"),
            "played_at": played_at,
            "completed": True,
            "progress": 1.0
        })

OUT_PATH.write_text(json.dumps(events, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps({"output": str(OUT_PATH), "events": len(events)}, indent=2, ensure_ascii=False))
