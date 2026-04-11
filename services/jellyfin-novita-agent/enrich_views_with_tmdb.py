#!/usr/bin/env python3
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path("${HOME}/jellyfin-novita-agent")
CFG_PATH = ROOT / "config.json"
IN_PATH = ROOT / "views_90d.real.json"
OUT_PATH = ROOT / "views_90d.clean.json"
CACHE_PATH = ROOT / "tmdb_people_cache.json"
TMDB_BASE = "https://api.themoviedb.org/3"


def read_json(path: Path, default: Any):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def tmdb_headers(cfg: dict) -> dict | None:
    bearer = str((cfg or {}).get("tmdb_bearer") or "").strip()
    if not bearer:
        return None
    return {
        "Authorization": f"Bearer {bearer}",
        "accept": "application/json",
    }


def tmdb_get(path: str, params: dict, headers: dict | None):
    if not headers:
        return {}
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{TMDB_BASE}{path}?{qs}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {}


def enrich_movie_people_tmdb(title: str, year: int | None, headers: dict | None):
    if not headers or not title:
        return {"director": None, "writers": [], "cast": []}

    data = tmdb_get("/search/movie", {
        "query": title,
        "language": "it-IT",
        "include_adult": "false",
        "page": 1,
        "year": year,
    }, headers)

    results = data.get("results") or []
    if not results:
        data = tmdb_get("/search/movie", {
            "query": title,
            "language": "it-IT",
            "include_adult": "false",
            "page": 1,
        }, headers)
        results = data.get("results") or []

    if not results:
        return {"director": None, "writers": [], "cast": []}

    movie_id = results[0].get("id")
    if not movie_id:
        return {"director": None, "writers": [], "cast": []}

    details = tmdb_get(f"/movie/{movie_id}", {
        "language": "it-IT",
        "append_to_response": "credits",
    }, headers)

    crew = (details.get("credits") or {}).get("crew") or []
    cast = (details.get("credits") or {}).get("cast") or []

    director = None
    for x in crew:
        if str(x.get("job") or "").strip().lower() == "director":
            director = x.get("name")
            if director:
                break

    writers = []
    for x in crew:
        job = str(x.get("job") or "").strip().lower()
        if job in {"writer", "screenplay", "story"}:
            name = x.get("name")
            if name and name not in writers:
                writers.append(name)

    cast_names = []
    for x in cast[:5]:
        name = x.get("name")
        if name and name not in cast_names:
            cast_names.append(name)

    return {"director": director, "writers": writers, "cast": cast_names}


def is_live_tv_like(item: dict) -> bool:
    title = str(item.get("title") or "").lower()
    series = str(item.get("series_name") or "").lower()
    genres = item.get("genres") or []
    duration = int(item.get("play_duration") or 0)

    hay = f"{title} {series}"
    bad = [
        "tgr",
        "tg ",
        "rainews",
        "rai 1", "rai 2", "rai 3", "rai 4", "rai 5",
        "canale 5", "italia 1", "rete 4",
        "la7", "tv8", "nove",
        "meteo",
        "top crime",
        "mediaset extra",
        "la 5",
    ]

    if any(x in hay for x in bad):
        return True
    if not genres and not series and duration and duration < 600:
        return True
    return False


def movie_cache_key(item: dict) -> str:
    title = str(item.get("title") or "").strip()
    year = item.get("year")
    return f"{title}::{year if year not in (None, '') else ''}"


def main():
    cfg = read_json(CFG_PATH, {})
    headers = tmdb_headers(cfg)
    if not headers:
        raise SystemExit("tmdb_bearer mancante in config.json")

    rows = read_json(IN_PATH, [])
    if not isinstance(rows, list):
        raise SystemExit("views_90d.real.json non è una lista")

    cache = read_json(CACHE_PATH, {})
    if not isinstance(cache, dict):
        cache = {}

    movie_items = []
    for x in rows:
        if str(x.get("kind") or "").lower() != "movie":
            continue
        if is_live_tv_like(x):
            continue
        movie_items.append(x)

    unique_keys = []
    seen = set()
    for x in movie_items:
        k = movie_cache_key(x)
        if k not in seen:
            seen.add(k)
            unique_keys.append(k)

    for idx, key in enumerate(unique_keys, start=1):
        if key in cache:
            continue
        title, year_text = key.rsplit("::", 1)
        year = int(year_text) if year_text.isdigit() else None
        print(f"TMDB ENRICH {idx}/{len(unique_keys)}: {title}", flush=True)
        cache[key] = enrich_movie_people_tmdb(title, year, headers)
        write_json(CACHE_PATH, cache)

    out = []
    for x in rows:
        y = dict(x)
        if str(y.get("kind") or "").lower() == "movie" and not is_live_tv_like(y):
            key = movie_cache_key(y)
            tm = cache.get(key) or {}
            if not y.get("director"):
                y["director"] = tm.get("director")
            if not y.get("writers"):
                y["writers"] = tm.get("writers") or []
            if not y.get("cast"):
                y["cast"] = tm.get("cast") or []
        out.append(y)

    write_json(OUT_PATH, out)
    print(json.dumps({
        "input": str(IN_PATH),
        "output": str(OUT_PATH),
        "cache": str(CACHE_PATH),
        "events": len(out),
        "movie_items": len(movie_items),
        "unique_movie_titles": len(unique_keys),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
