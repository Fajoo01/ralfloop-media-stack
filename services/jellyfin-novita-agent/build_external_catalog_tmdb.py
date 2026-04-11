#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
import subprocess
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

TMDB_BASE = "https://api.themoviedb.org/3"

VALID_BUCKETS = {
    "continuation",
    "new_release",
    "recent_catalog",
    "back_catalog",
    "library_gap",
}

BUCKET_WEIGHT = {
    "continuation": 50,
    "library_gap": 40,
    "new_release": 30,
    "recent_catalog": 20,
    "back_catalog": 10,
}


def norm_text(s):
    if s is None:
        return None
    s = str(s).strip()
    s = re.sub(r"\s+", " ", s)
    return s or None


def norm_kind(s):
    s = norm_text(s)
    if not s:
        return None
    s = s.lower()
    return s if s in {"movie", "series"} else None


def norm_genres(genres):
    if not genres:
        return []
    out, seen = [], set()
    for g in genres:
        g = norm_text(g)
        if not g:
            continue
        k = g.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(g)
    return out


def norm_bucket(bucket):
    bucket = norm_text(bucket)
    if not bucket:
        return "recent_catalog"
    bucket = bucket.lower()
    return bucket if bucket in VALID_BUCKETS else "recent_catalog"


def norm_int(value, default=None):
    if value is None:
        return default
    try:
        return int(value)
    except Exception:
        return default


def dedupe_key(item):
    return (
        item["kind"],
        (item["title"] or "").lower(),
        item.get("season_number"),
    )


def score_seed_item(item):
    bucket = item.get("bucket", "recent_catalog")
    priority_boost = norm_int(item.get("priority_boost"), 0) or 0
    year = norm_int(item.get("year"), 0) or 0
    has_franchise = 5 if item.get("franchise") else 0
    has_season = 3 if item.get("season_number") else 0
    return BUCKET_WEIGHT.get(bucket, 0) + priority_boost * 10 + has_franchise + has_season + year / 10000.0


def normalize_item(raw):
    title = norm_text(raw.get("title"))
    kind = norm_kind(raw.get("kind"))
    if not title or not kind:
        return None
    return {
        "title": title,
        "kind": kind,
        "genres": norm_genres(raw.get("genres")),
        "franchise": norm_text(raw.get("franchise")),
        "bucket": norm_bucket(raw.get("bucket")),
        "source": norm_text(raw.get("source")) or "manual_seed",
        "release_date": norm_text(raw.get("release_date")),
        "year": norm_int(raw.get("year")),
        "season_number": norm_int(raw.get("season_number")),
        "priority_boost": norm_int(raw.get("priority_boost"), 0) or 0,
        "notes": norm_text(raw.get("notes")) or "",
    }


def tmdb_headers():
    bearer = os.getenv("TMDB_BEARER")
    if bearer:
        return {"Authorization": f"Bearer {bearer}", "accept": "application/json"}
    return {"accept": "application/json"}


def tmdb_get(path, params):
    api_key = os.getenv("TMDB_API_KEY")
    params = dict(params)
    headers = ["accept: application/json"]

    bearer = os.getenv("TMDB_BEARER")
    if bearer:
        headers.append(f"Authorization: Bearer {bearer}")
    elif api_key:
        params["api_key"] = api_key

    url = f"{TMDB_BASE}{path}?{urlencode(params)}"
    print(f"[TMDB] GET {url}", flush=True)

    cmd = ["curl", "-fsS", "--max-time", "30"]
    for h in headers:
        cmd += ["-H", h]
    cmd.append(url)

    out = subprocess.check_output(cmd, text=True)
    return json.loads(out)


def get_tmdb_genre_maps(language):
    movie = tmdb_get("/genre/movie/list", {"language": language})
    tv = tmdb_get("/genre/tv/list", {"language": language})
    movie_map = {g["id"]: g["name"] for g in movie.get("genres", [])}
    tv_map = {g["id"]: g["name"] for g in tv.get("genres", [])}
    return movie_map, tv_map


def map_genres(ids, genre_map):
    return [genre_map[g] for g in ids if g in genre_map]


def tmdb_movie_item(row, genre_map):
    title = norm_text(row.get("title"))
    release_date = norm_text(row.get("release_date"))
    year = int(release_date[:4]) if release_date and len(release_date) >= 4 else None
    return normalize_item({
        "title": title,
        "kind": "movie",
        "genres": map_genres(row.get("genre_ids", []), genre_map),
        "franchise": None,
        "bucket": "new_release",
        "source": "tmdb_upcoming",
        "release_date": release_date,
        "year": year,
        "season_number": None,
        "priority_boost": 0,
        "notes": "Import automatico TMDb upcoming"
    })


def tmdb_tv_item(row, genre_map, bucket="new_release", source="tmdb_on_the_air"):
    title = norm_text(row.get("name"))
    first_air_date = norm_text(row.get("first_air_date"))
    year = int(first_air_date[:4]) if first_air_date and len(first_air_date) >= 4 else None
    return normalize_item({
        "title": title,
        "kind": "series",
        "genres": map_genres(row.get("genre_ids", []), genre_map),
        "franchise": title,
        "bucket": bucket,
        "source": source,
        "release_date": first_air_date,
        "year": year,
        "season_number": None,
        "priority_boost": 0,
        "notes": f"Import automatico {source}"
    })


def load_seed(seed_path):
    p = Path(seed_path)
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    out = []
    for raw in data:
        item = normalize_item(raw)
        if item:
            out.append(item)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", default="catalog_seed.json")
    ap.add_argument("--out", default="external_catalog.json")
    ap.add_argument("--language", default=os.getenv("TMDB_LANGUAGE", "it-IT"))
    ap.add_argument("--region", default=os.getenv("TMDB_REGION", "IT"))
    ap.add_argument("--movie-pages", type=int, default=1)
    ap.add_argument("--tv-pages", type=int, default=1)
    args = ap.parse_args()

    if not (os.getenv("TMDB_API_KEY") or os.getenv("TMDB_BEARER")):
        raise SystemExit("Manca TMDB_API_KEY o TMDB_BEARER nelle variabili ambiente")

    seed_items = load_seed(args.seed)
    movie_genres, tv_genres = get_tmdb_genre_maps(args.language)

    ext_items = []

    for page in range(1, args.movie_pages + 1):
        data = tmdb_get("/movie/upcoming", {
            "language": args.language,
            "region": args.region,
            "page": page,
        })
        for row in data.get("results", []):
            item = tmdb_movie_item(row, movie_genres)
            if item:
                ext_items.append(item)

    for page in range(1, args.tv_pages + 1):
        data = tmdb_get("/tv/on_the_air", {
            "language": args.language,
            "page": page,
        })
        for row in data.get("results", []):
            item = tmdb_tv_item(row, tv_genres, bucket="new_release", source="tmdb_on_the_air")
            if item:
                ext_items.append(item)

        data2 = tmdb_get("/tv/airing_today", {
            "language": args.language,
            "page": page,
        })
        for row in data2.get("results", []):
            item = tmdb_tv_item(row, tv_genres, bucket="new_release", source="tmdb_airing_today")
            if item:
                ext_items.append(item)

    merged = seed_items + ext_items

    deduped = {}
    for item in merged:
        key = dedupe_key(item)
        old = deduped.get(key)
        if old is None or score_seed_item(item) > score_seed_item(old):
            deduped[key] = item

    final_items = list(deduped.values())
    final_items.sort(key=lambda x: score_seed_item(x), reverse=True)

    Path(args.out).write_text(
        json.dumps(final_items, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(json.dumps({
        "seed_items": len(seed_items),
        "external_items": len(ext_items),
        "final_items": len(final_items),
        "out": args.out,
        "language": args.language,
        "region": args.region,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
