

def _filter_live_tv(rows):
    out = []
    for item in rows:
        title = str(item.get("title") or "").lower()
        series = str(item.get("series_name") or "").lower()
        genres = item.get("genres") or []
        duration = int(item.get("play_duration") or 0)

        hay = f"{title} {series}"

        bad = [
            "tgr",
            "tg ",
            "rainews",
            "rai 1","rai 2","rai 3","rai 4","rai 5",
            "canale 5","italia 1","rete 4",
            "la7","tv8","nove",
            "meteo"
        ]

        if any(x in hay for x in bad):
            continue

        if not genres and not series:
            continue

        if duration and duration < 600:
            continue

        out.append(item)
    return out

import json
import re
import sqlite3
from pathlib import Path

PR_DB_PATH = "/srv/jellyfin-sibilla/config/data/playback_reporting.db"
JF_DB_PATH = "/srv/jellyfin-sibilla/config/data/jellyfin.db"
OUT_PATH = Path("${HOME}/jellyfin-novita-agent/views_90d.real.json")

CFG_PATH = Path("${HOME}/jellyfin-novita-agent/config.json")
TMDB_BASE = "https://api.themoviedb.org/3"

def read_cfg():
    try:
        return json.loads(CFG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

def tmdb_headers(cfg):
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
    import urllib.parse
    import urllib.request
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{TMDB_BASE}{path}?{qs}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=2) as resp:
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


MIN_SECONDS_MOVIE = 900
MIN_SECONDS_EPISODE = 600
COMPLETE_SECONDS_MOVIE = 1800
COMPLETE_SECONDS_EPISODE = 1200
MAX_ROWS = 5000

RE_SXE = re.compile(r'(?i)\bs(?P<s>\d{1,2})e(?P<e>\d{1,2})\b')
RE_X = re.compile(r'(?i)\b(?P<s>\d{1,2})x(?P<e>\d{1,2})\b')


def parse_series_info(raw_name: str):
    raw_name = (raw_name or "").strip()
    season_number = None
    series_name = None

    m = RE_SXE.search(raw_name) or RE_X.search(raw_name)
    if m:
        season_number = int(m.group("s"))

    if " - " in raw_name:
        series_name = raw_name.split(" - ", 1)[0].strip()
    else:
        series_name = raw_name.strip()

    return series_name, season_number


def norm_id(s: str | None) -> str:
    return str(s or "").replace("-", "").lower()


def load_jellyfin_metadata(jf_db_path: str):
    conn = sqlite3.connect(jf_db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    item_genres = {}
    series_name_to_genres = {}
    item_director = {}
    item_writers = {}
    item_cast = {}

    rows = cur.execute("""
        SELECT
            replace(lower(ivm.ItemId), '-', '') AS ItemIdNorm,
            iv.Type AS Type,
            iv.Value AS Value
        FROM ItemValuesMap ivm
        JOIN ItemValues iv ON iv.ItemValueId = ivm.ItemValueId
        WHERE iv.Type = 2
    """).fetchall()

    tmp = {}
    for r in rows:
        item_id = str(r["ItemIdNorm"] or "")
        value = (r["Value"] or "").strip()
        if not item_id or not value:
            continue
        tmp.setdefault(item_id, set()).add(value)

    item_genres = {k: sorted(v) for k, v in tmp.items()}

    series_rows = cur.execute("""
        SELECT
            replace(lower(b.Id), '-', '') AS SeriesIdNorm,
            b.Name AS SeriesName
        FROM BaseItems b
        WHERE b.Type = 'MediaBrowser.Controller.Entities.TV.Series'
    """).fetchall()

    for r in series_rows:
        sid = str(r["SeriesIdNorm"] or "")
        sname = (r["SeriesName"] or "").strip()
        if not sid or not sname:
            continue
        genres = item_genres.get(sid, [])
        if genres:
            series_name_to_genres[sname.lower()] = genres

    people_rows = cur.execute("""
        SELECT
            replace(lower(pb.ItemId), '-', '') AS ItemIdNorm,
            p.Name AS PersonName,
            p.PersonType AS PersonType,
            pb.Role AS Role,
            pb.SortOrder AS SortOrder,
            pb.ListOrder AS ListOrder
        FROM PeopleBaseItemMap pb
        JOIN Peoples p ON p.Id = pb.PeopleId
        WHERE p.Name IS NOT NULL
    """).fetchall()

    tmp_director = {}
    tmp_writers = {}
    tmp_cast = {}

    for r in people_rows:
        item_id = str(r["ItemIdNorm"] or "")
        name = (r["PersonName"] or "").strip()
        ptype = str(r["PersonType"] or "").strip().lower()
        sort_order = r["SortOrder"]
        list_order = r["ListOrder"]

        if not item_id or not name:
            continue

        if ptype == "director":
            tmp_director.setdefault(item_id, [])
            if name not in tmp_director[item_id]:
                tmp_director[item_id].append(name)

        elif ptype in {"writer", "screenwriter"}:
            tmp_writers.setdefault(item_id, [])
            if name not in tmp_writers[item_id]:
                tmp_writers[item_id].append(name)

        elif ptype in {"actor", "gueststar"}:
            tmp_cast.setdefault(item_id, [])
            ord_value = sort_order if sort_order is not None else list_order
            tmp_cast[item_id].append((999999 if ord_value is None else int(ord_value), name))

    item_director = {k: (v[0] if v else None) for k, v in tmp_director.items()}
    item_writers = {k: v for k, v in tmp_writers.items()}
    item_cast = {
        k: [name for _, name in sorted(v, key=lambda t: (t[0], t[1].lower()))[:5]]
        for k, v in tmp_cast.items()
    }

    conn.close()
    return item_genres, series_name_to_genres, item_director, item_writers, item_cast


def main():
    pr_conn = sqlite3.connect(PR_DB_PATH)
    pr_conn.row_factory = sqlite3.Row
    pr_cur = pr_conn.cursor()

    rows = pr_cur.execute("""
    SELECT
        UserId,
        ItemId,
        ItemType,
        ItemName,
        DateCreated,
        PlayDuration
    FROM PlaybackActivity
    WHERE DateCreated IS NOT NULL
    ORDER BY DateCreated DESC
    LIMIT ?
    """, (MAX_ROWS,)).fetchall()

    item_genres, series_name_to_genres, item_director, item_writers, item_cast = load_jellyfin_metadata(JF_DB_PATH)

    cfg = read_cfg()
    _tmdb_headers = tmdb_headers(cfg)
    _tmdb_cache = {}

    # TMDb prefetch disattivato: troppo lento/bloccante su questa macchina
    best = {}

    for r in rows:
        item_type = (r["ItemType"] or "").lower().strip()
        raw_name = (r["ItemName"] or "").strip()
        user_id = str(r["UserId"] or "")
        item_id = norm_id(r["ItemId"])
        played_at = r["DateCreated"]
        play_duration = int(r["PlayDuration"] or 0)

        if not raw_name or not user_id or not item_id:
            continue

        if item_type == "episode":
            kind = "series"
            min_seconds = MIN_SECONDS_EPISODE
            complete_seconds = COMPLETE_SECONDS_EPISODE
        else:
            kind = "movie"
            min_seconds = MIN_SECONDS_MOVIE
            complete_seconds = COMPLETE_SECONDS_MOVIE

        if play_duration < min_seconds:
            continue

        title = raw_name
        season_number = None
        series_name = None

        if kind == "series":
            series_name, season_number = parse_series_info(raw_name)
            title = series_name or raw_name

        genres = item_genres.get(item_id, [])
        if not genres and kind == "series" and series_name:
            genres = series_name_to_genres.get(series_name.lower(), [])

        key = (user_id, item_id)

        director = item_director.get(item_id)
        writers = item_writers.get(item_id, [])
        cast = item_cast.get(item_id, [])

        # TMDb enrichment disattivato nel flusso normale: si usa solo metadata locale

        event = {
            "user_id": user_id,
            "item_id": item_id,
            "title": title,
            "kind": kind,
            "genres": genres,
            "director": director,
            "writers": writers,
            "cast": cast,
            "series_name": series_name,
            "season_number": season_number,
            "played_at": played_at,
            "completed": play_duration >= complete_seconds,
            "progress": 1.0 if play_duration >= complete_seconds else 0.6,
            "play_duration": play_duration,
        }

        prev = best.get(key)
        if prev is None:
            best[key] = event
        else:
            if (event["play_duration"], event["played_at"]) > (prev["play_duration"], prev["played_at"]):
                best[key] = event

    events = sorted(_filter_live_tv(best.values()), key=lambda x: x["played_at"], reverse=True)

    OUT_PATH.write_text(json.dumps(events, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "output": str(OUT_PATH),
        "events": len(events)
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
