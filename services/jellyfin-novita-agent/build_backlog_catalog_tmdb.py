#!/usr/bin/env python3
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import socket
from urllib.error import URLError, HTTPError

_ORIG_GETADDRINFO = socket.getaddrinfo

def _force_ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    infos = _ORIG_GETADDRINFO(host, port, family, type, proto, flags)
    ipv4 = [x for x in infos if x[0] == socket.AF_INET]
    return ipv4 or infos

socket.getaddrinfo = _force_ipv4_getaddrinfo

ROOT = Path("${HOME}/jellyfin-novita-agent")
CONFIG_PATH = ROOT / "config.json"
SEED_PATH = ROOT / "catalog_backlog_seed.json"
OUT_PATH = ROOT / "catalog_backlog.json"

TMDB_BASE = "https://api.themoviedb.org/3"

GENRE_MAP_IT = {
    "azione": 28,
    "avventura": 12,
    "animazione": 16,
    "commedia": 35,
    "crime": 80,
    "documentario": 99,
    "dramma": 18,
    "famiglia": 10751,
    "fantasy": 14,
    "storia": 36,
    "horror": 27,
    "musica": 10402,
    "mistero": 9648,
    "romance": 10749,
    "sci-fi": 878,
    "fantascienza": 878,
    "thriller": 53,
    "guerra": 10752,
    "western": 37,
}

def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

def tmdb_headers(cfg: Dict[str, Any]) -> Dict[str, str]:
    bearer = (cfg.get("tmdb_bearer") or "").strip()
    if not bearer:
        raise SystemExit("tmdb_bearer mancante in config.json")
    return {
        "Authorization": f"Bearer {bearer}",
        "accept": "application/json",
    }

def tmdb_get(path: str, params: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
    qs = urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{TMDB_BASE}{path}?{qs}"
    print("TMDB GET:", url, flush=True)
    req = Request(url, headers=headers, method="GET")
    try:
        with urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (URLError, HTTPError, TimeoutError, OSError) as e:
        print(f"TMDB WARN: {path} failed: {e}", flush=True)
        return {"results": []}

def norm_title(s: str) -> str:
    import re
    s = (s or "").lower().strip()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def load_existing_titles() -> set[str]:
    out = set()

    # external catalog
    for row in read_json(ROOT / "external_catalog.json", []):
        if isinstance(row, dict) and str(row.get("kind") or "").lower() == "movie":
            out.add(norm_title(str(row.get("title") or "")))

    # backlog current
    for row in read_json(OUT_PATH, []):
        if isinstance(row, dict) and str(row.get("kind") or "").lower() == "movie":
            out.add(norm_title(str(row.get("title") or "")))

    return out

def map_genres(seed: Dict[str, Any]) -> List[int]:
    ids = []
    for g in seed.get("genres") or []:
        gid = GENRE_MAP_IT.get(str(g).strip().lower())
        if gid and gid not in ids:
            ids.append(gid)
    return ids

def enrich_movie(row: Dict[str, Any], source: str, priority: int = 1, headers: Dict[str, str] | None = None) -> Dict[str, Any]:
    release_date = row.get("release_date") or ""
    year = None
    if release_date and len(release_date) >= 4 and release_date[:4].isdigit():
        year = int(release_date[:4])

    genres = row.get("genre_ids") or []
    tmdb_id = row.get("id")

    details = {}
    if tmdb_id and headers:
        details = tmdb_get(
            f"/movie/{tmdb_id}",
            {
                "language": "it-IT",
                "append_to_response": "credits"
            },
            headers
        )

    credits = details.get("credits") or {}
    crew = credits.get("crew") or []
    cast = credits.get("cast") or []

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

    if details.get("genres"):
        genres = [g.get("id") for g in (details.get("genres") or []) if g.get("id")]

    return {
        "kind": "movie",
        "title": row.get("title") or row.get("original_title") or "",
        "original_title": row.get("original_title") or row.get("title") or "",
        "year": year,
        "tmdb_id": tmdb_id,
        "overview": row.get("overview") or details.get("overview") or "",
        "popularity": row.get("popularity") or details.get("popularity") or 0,
        "vote_average": row.get("vote_average") or details.get("vote_average") or 0,
        "vote_count": row.get("vote_count") or details.get("vote_count") or 0,
        "genre_ids": genres,
        "director": director,
        "writers": writers,
        "cast": cast_names,
        "priority": priority,
        "source": source,
        "status": "pending",
        "attempts": 0,
        "last_attempt_at": None,
        "last_result": None
    }

def add_seed_titles(seed: Dict[str, Any], out: List[Dict[str, Any]], seen: set[str], headers: Dict[str, str]) -> None:
    for item in seed.get("include_titles") or []:
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        year = item.get("year")
        key = norm_title(title)
        if key in seen:
            continue

        params = {
            "query": title,
            "language": "it-IT",
            "include_adult": "false",
            "page": 1,
            "year": year,
        }
        data = tmdb_get("/search/movie", params, headers)
        results = data.get("results") or []
        if not results:
            continue

        row = results[0]
        movie = enrich_movie(row, "seed_search", int(item.get("priority") or 5), headers)
        if not movie["title"]:
            continue

        out.append(movie)
        seen.add(norm_title(movie["title"]))
        time.sleep(0.15)

def fetch_bucket(path: str, pages: int, params: Dict[str, Any], headers: Dict[str, str], out: List[Dict[str, Any]], seen: set[str], source: str, priority: int) -> None:
    for page in range(1, pages + 1):
        payload = dict(params)
        payload["page"] = page
        data = tmdb_get(path, payload, headers)
        for row in data.get("results") or []:
            title = str(row.get("title") or row.get("original_title") or "").strip()
            if not title:
                continue
            key = norm_title(title)
            if key in seen:
                continue
            out.append(enrich_movie(row, source, priority, headers))
            seen.add(key)
        time.sleep(0.15)

def main() -> None:
    cfg = read_json(CONFIG_PATH, {})
    seed = read_json(SEED_PATH, {})
    headers = tmdb_headers(cfg)
    genre_ids = map_genres(seed)
    genre_csv = ",".join(str(x) for x in genre_ids) if genre_ids else None

    min_year = int(seed["min_year"]) if seed.get("min_year") not in (None, "", False) else None
    max_year = int(seed.get("max_year") or 2023)
    min_vote_count = int(seed.get("min_vote_count") or 300)

    seen = load_existing_titles()
    out: List[Dict[str, Any]] = []

    add_seed_titles(seed, out, seen, headers)

    common = {
        "language": "it-IT",
        "region": "IT",
        "include_adult": "false",
        "with_original_language": None,
        "vote_count.gte": min_vote_count,
        "primary_release_date.gte": f"{min_year}-01-01" if min_year is not None else None,
        "primary_release_date.lte": f"{max_year}-12-31",
        "with_genres": genre_csv,
    }

    fetch_bucket(
        "/discover/movie",
        int(seed.get("pages_discover") or 6),
        {**common, "sort_by": "vote_average.desc"},
        headers,
        out,
        seen,
        "discover",
        2,
    )

    fetch_bucket(
        "/movie/top_rated",
        int(seed.get("pages_top_rated") or 3),
        {"language": "it-IT", "page": 1, "region": "IT"},
        headers,
        out,
        seen,
        "top_rated",
        2,
    )

    fetch_bucket(
        "/movie/popular",
        int(seed.get("pages_popular") or 3),
        {"language": "it-IT", "page": 1, "region": "IT"},
        headers,
        out,
        seen,
        "popular",
        1,
    )

    # filtro finale anno
    final = []
    seen2 = set()
    for row in out:
        year = row.get("year")
        title = row.get("title") or ""
        if not title:
            continue
        if year is None or (min_year is not None and year < min_year) or year > max_year:
            continue
        key = f"{norm_title(title)}::{year}"
        if key in seen2:
            continue
        seen2.add(key)
        final.append(row)

    final.sort(key=lambda x: (-int(x.get("priority") or 0), -float(x.get("vote_average") or 0), -int(x.get("vote_count") or 0), x.get("title") or ""))
    write_json(OUT_PATH, final)
    print(json.dumps({
        "output": str(OUT_PATH),
        "items": len(final),
        "seed_titles": len(seed.get("include_titles") or []),
        "genres": seed.get("genres") or [],
        "min_year": min_year,
        "max_year": max_year
    }, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
