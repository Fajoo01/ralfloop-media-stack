#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

import requests
import re


# -----------------------------
# Config
# -----------------------------

DEFAULT_DB = "/var/lib/jellyfin-novita-agent/novita.db"
DEFAULT_OUTPUT_DIR = "/var/lib/jellyfin-novita-agent/out"


@dataclasses.dataclass
class Config:
    jellyfin_url: str
    jellyfin_token: str
    amule_api_url: str
    amule_api_key: str
    db_path: str = DEFAULT_DB
    output_dir: str = DEFAULT_OUTPUT_DIR
    observation_days: int = 90
    auto_download_threshold: float = 15.0
    suggest_threshold: float = 8.0
    movie_auto_download_threshold: float = 15.0
    movie_suggest_threshold: float = 8.0
    max_auto_download_per_day: int = 3
    preferred_genres: list[str] = dataclasses.field(default_factory=list)
    manual_favorite_directors: list[str] = dataclasses.field(default_factory=list)
    manual_favorite_writers: list[str] = dataclasses.field(default_factory=list)
    manual_favorite_actors: list[str] = dataclasses.field(default_factory=list)
    director_weight: float = 1.0
    writer_weight: float = 0.6
    cast_weight: float = 0.35
    director_match_bonus: float = 3.0
    writer_match_bonus: float = 2.0
    cast_match_bonus: float = 1.0
    cast_top_n: int = 5
    movie_backlog_file: str = ""

    @classmethod
    def from_env_or_file(cls, path: str | None) -> "Config":
        data: dict[str, Any] = {}
        if path and Path(path).exists():
            data = json.loads(Path(path).read_text(encoding="utf-8"))

        def pick(key: str, default: Any = "") -> Any:
            return os.getenv(key.upper(), data.get(key, default))

        return cls(
            jellyfin_url=str(pick("jellyfin_url", "")).rstrip("/"),
            jellyfin_token=str(pick("jellyfin_token", "")),
            amule_api_url=str(pick("amule_api_url", "")).rstrip("/"),
            amule_api_key=str(pick("amule_api_key", "")),
            db_path=str(pick("db_path", DEFAULT_DB)),
            output_dir=str(pick("output_dir", DEFAULT_OUTPUT_DIR)),
            observation_days=int(pick("observation_days", 90)),
            auto_download_threshold=float(pick("auto_download_threshold", 15)),
            suggest_threshold=float(pick("suggest_threshold", 8)),
            movie_auto_download_threshold=float(pick("movie_auto_download_threshold", 15)),
            movie_suggest_threshold=float(pick("movie_suggest_threshold", 8)),
            max_auto_download_per_day=int(pick("max_auto_download_per_day", 3)),
            preferred_genres=list(data.get("preferred_genres", [])),
            manual_favorite_directors=list(data.get("manual_favorite_directors", [])),
            manual_favorite_writers=list(data.get("manual_favorite_writers", [])),
            manual_favorite_actors=list(data.get("manual_favorite_actors", [])),
            director_weight=float(pick("director_weight", 1.0)),
            writer_weight=float(pick("writer_weight", 0.6)),
            cast_weight=float(pick("cast_weight", 0.35)),
            director_match_bonus=float(pick("director_match_bonus", 3.0)),
            writer_match_bonus=float(pick("writer_match_bonus", 2.0)),
            cast_match_bonus=float(pick("cast_match_bonus", 1.0)),
            cast_top_n=int(pick("cast_top_n", 5)),
            movie_backlog_file=str(pick("movie_backlog_file", "")),
        )


# -----------------------------
# Storage
# -----------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS view_events (
    user_id TEXT,
    item_id TEXT,
    item_type TEXT,
    title TEXT,
    series_title TEXT,
    season_number INTEGER,
    episode_number INTEGER,
    completed INTEGER,
    played_at TEXT,
    year INTEGER,
    genres_json TEXT,
    franchise TEXT,
    director TEXT,
    writers_json TEXT,
    cast_json TEXT
);

CREATE TABLE IF NOT EXISTS candidates (
    candidate_id TEXT PRIMARY KEY,
    kind TEXT,
    title TEXT,
    season_number INTEGER,
    score REAL,
    reason_json TEXT,
    decision TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS executions (
    execution_id TEXT PRIMARY KEY,
    candidate_id TEXT,
    action TEXT,
    status TEXT,
    detail TEXT,
    created_at TEXT
);
"""



import re
from collections import Counter, defaultdict


def _norm(s: str | None) -> str:
    if not s:
        return ""
    s = s.lower().strip()
    s = re.sub(r"\s*[-:]\s*s\d+\s*$", "", s)
    s = re.sub(r"\s+s\d+\s*$", "", s)
    s = re.sub(r"\s+part\s+\w+\s*$", "", s)
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s



def _title_key(s: str | None) -> str:
    if not s:
        return ""
    s = s.lower().strip()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _series_base(title: str | None, franchise: str | None = None) -> str:
    if franchise:
        return _norm(franchise)
    return _norm(title)


def normalize_person_name(x: str | None) -> str:
    s = str(x or "").strip().lower()
    s = re.sub(r"\([^)]*\)", "", s)
    s = re.sub(r"[^\w\s' -]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _as_list(v):
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def extract_movie_people(item: dict) -> tuple[list[str], list[str], list[str]]:
    directors_raw = (
        item.get("directors")
        or item.get("director")
        or ((item.get("credits") or {}).get("directors"))
        or []
    )
    writers_raw = (
        item.get("writers")
        or item.get("writer")
        or item.get("screenplay")
        or ((item.get("credits") or {}).get("writers"))
        or []
    )
    cast_raw = (
        item.get("cast")
        or ((item.get("credits") or {}).get("cast"))
        or []
    )

    directors = [normalize_person_name(x) for x in _as_list(directors_raw) if normalize_person_name(x)]
    writers = [normalize_person_name(x) for x in _as_list(writers_raw) if normalize_person_name(x)]

    cast_names = []
    for x in _as_list(cast_raw):
        if isinstance(x, dict):
            name = x.get("name") or x.get("original_name") or x.get("person") or ""
        else:
            name = x
        n = normalize_person_name(name)
        if n:
            cast_names.append(n)

    return directors, writers, cast_names


def build_user_profile(events: list[dict]) -> dict:
    genre_counter = Counter()
    director_counter = Counter()
    writer_counter = Counter()
    cast_counter = Counter()
    title_counter = Counter()
    franchise_counter = Counter()
    series_counter = Counter()
    user_set_by_title = defaultdict(set)
    user_set_by_series = defaultdict(set)

    for ev in events:
        user_id = ev.get("user_id")
        title = ev.get("title")
        kind = ev.get("kind")
        genres = ev.get("genres") or []
        completed = bool(ev.get("completed"))
        progress = float(ev.get("progress") or 0)

        weight = 1.0
        if completed:
            weight += 1.0
        if progress >= 0.9:
            weight += 0.5
        elif progress < 0.5:
            weight -= 0.5

        for g in genres:
            genre_counter[g] += weight

        if kind == "movie":
            directors, writers, cast_names = extract_movie_people(ev)
            for d in directors:
                director_counter[d] += weight
            for w in writers:
                writer_counter[w] += weight
            for a in cast_names[:5]:
                cast_counter[a] += weight

        base_title = _title_key(title)
        if base_title:
            title_counter[base_title] += weight
            if user_id:
                user_set_by_title[base_title].add(user_id)

        if kind == "series":
            series_name = ev.get("series_name") or title
            base_series = _series_base(series_name)
            if base_series:
                series_counter[base_series] += weight * 2
                if user_id:
                    user_set_by_series[base_series].add(user_id)

        franchise_key = _series_base(ev.get("series_name") or title)
        if franchise_key:
            franchise_counter[franchise_key] += weight

    return {
        "genre_counter": genre_counter,
        "director_counter": director_counter,
        "writer_counter": writer_counter,
        "cast_counter": cast_counter,
        "title_counter": title_counter,
        "franchise_counter": franchise_counter,
        "series_counter": series_counter,
        "user_set_by_title": user_set_by_title,
        "user_set_by_series": user_set_by_series,
    }



_GENRE_ALIASES = {
    "drama": "drama",
    "dramma": "drama",
    "crime": "crime",
    "thriller": "thriller",
    "mystery": "mystery",
    "mistero": "mystery",
    "sci fi": "sci-fi",
    "sci-fi": "sci-fi",
    "science fiction": "sci-fi",
    "fantascienza": "sci-fi",
    "action": "action",
    "azione": "action",
    "adventure": "adventure",
    "avventura": "adventure",
    "fantasy": "fantasy",
    "comedy": "comedy",
    "commedia": "comedy",
    "romance": "romance",
    "horror": "horror",
    "orrore": "horror",
    "animation": "animation",
    "animazione": "animation",
    "documentary": "documentary",
    "documentario": "documentary",
    "family": "family",
    "famiglia": "family",
    "history": "history",
    "storia": "history",
    "war": "war",
    "guerra": "war",
    "music": "music",
    "musica": "music",
    "western": "western",
}

IGNORE_SERIES_TASTE_GENRES = {"animation", "family"}

def normalize_genre_name(g: str) -> str:
    s = (g or "").strip().lower()
    s = re.sub(r"[/_]", " ", s)
    s = s.replace("-", " ")
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return _GENRE_ALIASES.get(s, s)


ANIME_TITLE_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]")

def is_probable_anime_series(candidate: dict, genres: list[str]) -> bool:
    if str(candidate.get("kind") or "").lower() != "series":
        return False

    norm_genres = [normalize_genre_name(str(g)) for g in (genres or []) if str(g).strip()]
    return "animation" in norm_genres

def score_candidate(candidate: dict, profile: dict, cfg: dict | None = None) -> tuple[float, dict]:
    title = candidate.get("title")
    kind = candidate.get("kind")
    genres = candidate.get("genres") or []
    franchise = candidate.get("franchise")
    bucket = (candidate.get("bucket") or "recent_catalog").strip().lower()
    priority_boost = int(candidate.get("priority_boost") or 0)

    cfg = cfg or {}
    preferred_genres = set((cfg.get("preferred_genres") or []))
    manual_favorites = [str(x).strip() for x in (cfg.get("manual_favorites") or []) if str(x).strip()]
    manual_favorite_directors = {
        normalize_person_name(x)
        for x in (cfg.get("manual_favorite_directors") or [])
        if normalize_person_name(x)
    }
    manual_favorite_writers = {
        normalize_person_name(x)
        for x in (cfg.get("manual_favorite_writers") or [])
        if normalize_person_name(x)
    }
    manual_favorite_actors = {
        normalize_person_name(x)
        for x in (cfg.get("manual_favorite_actors") or [])
        if normalize_person_name(x)
    }

    director_score = 0.0
    writer_score = 0.0
    cast_score = 0.0
    manual_director_bonus = 0.0
    manual_writer_bonus = 0.0
    manual_actor_bonus = 0.0

    norm_counter = {}
    for k, v in (profile.get("genre_counter") or {}).items():
        nk = normalize_genre_name(str(k))
        if not nk:
            continue
        norm_counter[nk] = norm_counter.get(nk, 0) + v

    norm_genres = []
    for g in genres:
        ng = normalize_genre_name(str(g))
        if ng and ng not in norm_genres:
            norm_genres.append(ng)

    if str(candidate.get("kind") or "").lower() == "series":
        norm_counter = {k: v for k, v in norm_counter.items() if k not in IGNORE_SERIES_TASTE_GENRES}
        norm_genres = [g for g in norm_genres if g not in IGNORE_SERIES_TASTE_GENRES]

    top_genres = [k for k, _ in sorted(norm_counter.items(), key=lambda kv: -kv[1])[:6]]
    matched = [g for g in norm_genres if g in top_genres]

    # almeno 2 match veri, altrimenti niente gusto
    if len(matched) < 2:
        raw_genre_score = 0.0
    else:
        # overlap pesato per posizione nei top generi
        weighted = 0.0
        for g in matched:
            idx = top_genres.index(g)
            weighted += max(1.0, 6.0 - idx)

        # bonus piccolo se uno dei match è nei top 3
        if any(g in top_genres[:3] for g in matched):
            weighted += 2.0

        # penalizza candidati con troppi generi dispersivi
        weighted = weighted / max(1.0, len(norm_genres))
        raw_genre_score = weighted * 3.0
    norm_candidate_genres = [
        normalize_genre_name(str(g))
        for g in (genres or [])
        if str(g).strip()
    ]

    anime_penalty = 0.0
    if str(kind or "").lower() == "series" and "animation" in norm_candidate_genres:
        anime_penalty = float(
            (cfg or {}).get(
                "series_animation_penalty",
                (cfg or {}).get("series_anime_penalty", 12.0)
            ) or 12.0
        )

    genre_score = min(raw_genre_score, 24.0)
    genre_score *= 1.5
    genre_score -= anime_penalty

    manual_genre_bonus = 0.0
    for g in genres:
        if g in preferred_genres:
            manual_genre_bonus += 2.0

    base_title = _title_key(title)
    base_franchise = _series_base(title, franchise)

    franchise_score = profile["franchise_counter"].get(base_franchise, 0)
    exact_title_penalty = 0
    if profile["title_counter"].get(base_title, 0) > 0:
        exact_title_penalty = -100

    manual_favorite_bonus = 0.0
    for fav in manual_favorites:
        fav_key = _title_key(fav)
        fav_base = _series_base(fav)
        if not fav_key:
            continue
        if fav_key == base_title:
            manual_favorite_bonus = max(manual_favorite_bonus, 6.0)
        elif fav_base == base_franchise:
            manual_favorite_bonus = max(manual_favorite_bonus, 5.0)
        elif fav_key in base_title or base_title in fav_key:
            manual_favorite_bonus = max(manual_favorite_bonus, 3.0)

    matched_directors = []
    matched_writers = []
    matched_cast = []

    if kind == "movie":
        directors, writers, cast_names = extract_movie_people(candidate)

        raw_directors = [str(x).strip() for x in (candidate.get("director"),) if str(x or "").strip()]
        raw_writers = [str(x).strip() for x in (candidate.get("writers") or []) if str(x).strip()]
        raw_cast = [str(x).strip() for x in (candidate.get("cast") or []) if str(x).strip()]

        d_counter = profile.get("director_counter") or {}
        w_counter = profile.get("writer_counter") or {}
        c_counter = profile.get("cast_counter") or {}

        matched_directors = [
            raw_directors[idx]
            for idx, d in enumerate(directors[:len(raw_directors)])
            if float(d_counter.get(d, 0) or 0) > 0
        ]

        matched_writers = [
            raw_writers[idx]
            for idx, w in enumerate(writers[:len(raw_writers)])
            if float(w_counter.get(w, 0) or 0) > 0
        ]

        cast_top_n = int(cfg.get("cast_top_n", 5) or 5)
        cast_slice = cast_names[:cast_top_n]
        raw_cast_slice = raw_cast[:cast_top_n]

        matched_cast = [
            raw_cast_slice[idx]
            for idx, a in enumerate(cast_slice[:len(raw_cast_slice)])
            if float(c_counter.get(a, 0) or 0) > 0
        ]

        director_score = sum(float(d_counter.get(d, 0)) for d in directors)
        writer_score = sum(float(w_counter.get(w, 0)) for w in writers)
        cast_score = sum(float(c_counter.get(a, 0)) for a in cast_slice)

        if any(d in manual_favorite_directors for d in directors):
            manual_director_bonus = float(cfg.get("director_match_bonus", 3.0) or 3.0)
        if any(w in manual_favorite_writers for w in writers):
            manual_writer_bonus = float(cfg.get("writer_match_bonus", 2.0) or 2.0)
        if any(a in manual_favorite_actors for a in cast_slice):
            manual_actor_bonus = float(cfg.get("cast_match_bonus", 1.0) or 1.0)

    sequel_bonus = 0
    multiuser_bonus = 0
    if kind == "series":
        sequel_bonus += profile["series_counter"].get(base_franchise, 0) * 1.5
        multiuser_bonus += len(profile["user_set_by_series"].get(base_franchise, set())) * 2.0
    else:
        sequel_bonus += franchise_score * 1.2
        multiuser_bonus += len(profile["user_set_by_title"].get(base_franchise, set())) * 1.5

    bucket_weights = {
        "continuation": 12.0,
        "library_gap": 9.0,
        "new_release": 6.0,
        "recent_catalog": 3.0,
        "back_catalog": 1.0,
    }
    bucket_bonus = bucket_weights.get(bucket, 0.0)
    priority_bonus = priority_boost * 2.5

    raw_score = (
        genre_score * 0.8 +
        manual_genre_bonus +
        franchise_score * 0.8 +
        manual_favorite_bonus +
        sequel_bonus * 0.3 +
        multiuser_bonus * 0.4 +
        bucket_bonus * 0.4 +
        priority_bonus * 0.5 +
        director_score * float(cfg.get("director_weight", 1.0) or 1.0) +
        writer_score * float(cfg.get("writer_weight", 0.6) or 0.6) +
        cast_score * float(cfg.get("cast_weight", 0.35) or 0.35) +
        manual_director_bonus +
        manual_writer_bonus +
        manual_actor_bonus +
        exact_title_penalty
    )

    score = round(raw_score, 2)

    reason = {
        "source": "external_catalog",
        "genre_score": round(genre_score, 2),
        "anime_penalty": round(anime_penalty, 2),
        "manual_genre_bonus": round(manual_genre_bonus, 2),
        "franchise_score": round(franchise_score, 2),
        "manual_favorite_bonus": round(manual_favorite_bonus, 2),
        "sequel_bonus": round(sequel_bonus, 2),
        "multiuser_bonus": round(multiuser_bonus, 2),
        "director_score": round(director_score, 2),
        "writer_score": round(writer_score, 2),
        "cast_score": round(cast_score, 2),
        "matched_directors": matched_directors,
        "matched_writers": matched_writers,
        "matched_cast": matched_cast,
        "manual_director_bonus": round(manual_director_bonus, 2),
        "manual_writer_bonus": round(manual_writer_bonus, 2),
        "manual_actor_bonus": round(manual_actor_bonus, 2),
        "bucket": bucket,
        "bucket_bonus": round(bucket_bonus, 2),
        "priority_boost": priority_boost,
        "priority_bonus": round(priority_bonus, 2),
        "genres": genres,
        "franchise": franchise,
        "base_franchise": base_franchise,
    }
    return score, reason




def normalize_series_query_title(title: str) -> str:
    s = (title or '').strip()
    s = re.sub(r'\s*-\s*S\d{1,2}\s*$', '', s, flags=re.I)
    s = re.sub(r'\s+S\d{1,2}\s*$', '', s, flags=re.I)
    s = re.sub(r'\s+', ' ', s).strip()
    return s
def init_db(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)

        existing_cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(view_events)").fetchall()
        }

        if "director" not in existing_cols:
            conn.execute("ALTER TABLE view_events ADD COLUMN director TEXT")
        if "writers_json" not in existing_cols:
            conn.execute("ALTER TABLE view_events ADD COLUMN writers_json TEXT")
        if "cast_json" not in existing_cols:
            conn.execute("ALTER TABLE view_events ADD COLUMN cast_json TEXT")

        conn.commit()


# -----------------------------
# Helpers
# -----------------------------


def utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()



def parse_dt(value: str) -> dt.datetime:
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return dt.datetime.fromisoformat(value)
    except Exception as exc:
        raise ValueError(f"invalid datetime: {value}") from exc



def normalize_title(value: str) -> str:
    return " ".join((value or "").strip().split())

def normalize_movie_presence_title(value: str | None) -> str:
    s = normalize_title(str(value or "")).lower()
    s = re.sub(r"\(\d{4}\)", " ", s)
    s = re.sub(r"\b\d{4}\b", " ", s)
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s



def safe_json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


# -----------------------------
# Ingest
# -----------------------------


def ingest_views_file(db_path: str, views_file: str) -> int:
    payload = json.loads(Path(views_file).read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "events" in payload:
        events = payload["events"]
    elif isinstance(payload, list):
        events = payload
    else:
        raise ValueError("views file must be a list or {'events': [...]} ")

    count = 0
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM view_events")
        for ev in events:
            genres = ev.get("genres", []) or []
            writers = ev.get("writers", []) or []
            cast = ev.get("cast", []) or []
            conn.execute(
                """
                INSERT INTO view_events (
                    user_id, item_id, item_type, title, series_title, season_number,
                    episode_number, completed, played_at, year, genres_json, franchise,
                    director, writers_json, cast_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(ev.get("user_id", "")),
                    str(ev.get("item_id", "")),
                    str(ev.get("item_type", "")),
                    normalize_title(str(ev.get("title", ""))),
                    normalize_title(str(ev.get("series_title", ""))),
                    ev.get("season_number"),
                    ev.get("episode_number"),
                    1 if ev.get("completed") else 0,
                    str(ev.get("played_at", "")),
                    ev.get("year"),
                    safe_json_dumps(genres),
                    ev.get("franchise"),
                    ev.get("director"),
                    safe_json_dumps(writers),
                    safe_json_dumps(cast),
                ),
            )
            count += 1
        conn.commit()
    return count


# -----------------------------
# Analysis
# -----------------------------


def load_recent_events(db_path: str, days: int) -> list[dict[str, Any]]:
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM view_events WHERE played_at >= ? ORDER BY played_at DESC",
            (cutoff,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["genres"] = json.loads(item.pop("genres_json") or "[]")
        except json.JSONDecodeError:
            item["genres"] = []

        try:
            item["writers"] = json.loads(item.pop("writers_json") or "[]")
        except json.JSONDecodeError:
            item["writers"] = []

        try:
            item["cast"] = json.loads(item.pop("cast_json") or "[]")
        except json.JSONDecodeError:
            item["cast"] = []

        out.append(item)
    return out



def build_profiles(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    by_user: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "genres": Counter(),
        "franchises": Counter(),
        "movies_completed": Counter(),
        "series_progress": defaultdict(set),
        "series_completed_seasons": Counter(),
        "directors": Counter(),
        "writers": Counter(),
        "cast": Counter(),
    })
    global_genres = Counter()
    global_franchises = Counter()
    global_directors = Counter()
    global_writers = Counter()
    global_cast = Counter()

    for ev in events:
        uid = ev.get("user_id") or "anon"
        profile = by_user[uid]
        for genre in ev.get("genres", []):
            profile["genres"][genre] += 1
            global_genres[genre] += 1
        if ev.get("franchise"):
            profile["franchises"][ev["franchise"]] += 1
            global_franchises[ev["franchise"]] += 1

        director = str(ev.get("director") or "").strip()
        if director:
            profile["directors"][director] += 1
            global_directors[director] += 1

        for w in ev.get("writers", []) or []:
            w = str(w).strip()
            if w:
                profile["writers"][w] += 1
                global_writers[w] += 1

        for a in ev.get("cast", []) or []:
            a = str(a).strip()
            if a:
                profile["cast"][a] += 1
                global_cast[a] += 1

        if ev.get("item_type") == "Movie" and ev.get("completed"):
            profile["movies_completed"][ev.get("title", "")] += 1
        if ev.get("item_type") == "Episode":
            series_title = ev.get("series_title") or ev.get("title")
            season = ev.get("season_number")
            episode = ev.get("episode_number")
            if series_title and season and episode:
                profile["series_progress"][(series_title, int(season))].add(int(episode))
                if ev.get("completed"):
                    profile["series_completed_seasons"][(series_title, int(season))] += 1

    return {
        "users": by_user,
        "global_genres": global_genres,
        "global_franchises": global_franchises,
        "global_directors": global_directors,
        "global_writers": global_writers,
        "global_cast": global_cast,
    }



def recency_points(played_at_values: list[str]) -> float:
    if not played_at_values:
        return 0.0

    parsed = []
    for v in played_at_values:
        if not v:
            continue
        d = parse_dt(v)
        if not d:
            continue
        if d.tzinfo is None or d.tzinfo.utcoffset(d) is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        else:
            d = d.astimezone(dt.timezone.utc)
        parsed.append(d)

    if not parsed:
        return 0.0

    latest = max(parsed)
    days = max((dt.datetime.now(dt.timezone.utc) - latest).days, 0)
    if days <= 7:
        return 2.0
    if days <= 30:
        return 1.0
    return 0.0



def propose_continuations(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int], dict[str, Any]] = defaultdict(lambda: {
        "users": set(),
        "completed_users": set(),
        "played_at": [],
        "max_episode": 0,
        "episode_count_seen": set(),
    })

    for ev in events:
        if ev.get("item_type") != "Episode":
            continue
        title = normalize_title(ev.get("series_title") or ev.get("title") or "")
        season = ev.get("season_number")
        ep = ev.get("episode_number")
        if not title or season is None or ep is None:
            continue
        key = (title, int(season))
        grouped[key]["users"].add(ev.get("user_id") or "anon")
        grouped[key]["played_at"].append(ev.get("played_at") or "")
        grouped[key]["episode_count_seen"].add(int(ep))
        grouped[key]["max_episode"] = max(grouped[key]["max_episode"], int(ep))
        if ev.get("completed"):
            grouped[key]["completed_users"].add(ev.get("user_id") or "anon")

    out: list[dict[str, Any]] = []
    for (title, season), data in grouped.items():
        distinct_users = len(data["users"])
        completed_users = len(data["completed_users"])
        unique_eps_seen = len(data["episode_count_seen"])
        completion_hint = 1.0 if unique_eps_seen >= max(data["max_episode"] - 1, 1) else 0.0
        score = (
            distinct_users * 4
            + completed_users * 3
            + recency_points(data["played_at"]) * 2
            + completion_hint * 3
        )
        reason = {
            "source": "series_continuation",
            "distinct_users": distinct_users,
            "completed_users": completed_users,
            "unique_eps_seen": unique_eps_seen,
            "last_season_seen": season,
        }
        out.append(
            {
                "candidate_id": f"series::{title}::S{season + 1:02d}",
                "kind": "series",
                "title": title,
                "season_number": season + 1,
                "score": round(score, 2),
                "reason": reason,
            }
        )
    out.sort(key=lambda x: x["score"], reverse=True)
    return out



def propose_from_catalog(events: list[dict[str, Any]], catalog_file: str) -> list[dict[str, Any]]:
    """
    Catalog schema (list):
    [{"kind":"movie|series","title":"...","season_number":2,"genres":[...],"franchise":"...","year":2026}]

    This is intentionally external: without an external catalog the agent cannot invent real new releases.
    """
    catalog = json.loads(Path(catalog_file).read_text(encoding="utf-8"))
    profiles = build_profiles(events)
    global_genres: Counter = profiles["global_genres"]
    global_franchises: Counter = profiles["global_franchises"]

    already_titles = {normalize_title(e.get("title", "")) for e in events if e.get("title")}
    already_movie_titles = {
        normalize_movie_presence_title(e.get("title", ""))
        for e in events
        if str(e.get("kind") or "").lower() == "movie" and e.get("title")
    }
    already_series = {
        (normalize_title(e.get("series_title") or e.get("title") or ""), e.get("season_number"))
        for e in events
        if e.get("item_type") == "Episode"
    }

    out: list[dict[str, Any]] = []
    for item in catalog:
        title = normalize_title(item.get("title", ""))
        if not title:
            continue
        kind = item.get("kind", "movie")
        season_number = item.get("season_number")
        if kind == "movie" and normalize_movie_presence_title(title) in already_movie_titles:
            continue
        if kind == "series" and (title, season_number) in already_series:
            continue

        genres = item.get("genres", []) or []
        genre_score = sum(global_genres.get(g, 0) for g in genres)
        franchise = item.get("franchise")
        franchise_score = global_franchises.get(franchise, 0) * 3 if franchise else 0
        score = genre_score * 0.5 + franchise_score
        if item.get("year") == dt.datetime.now().year:
            score += 2
        out.append(
            {
                "candidate_id": f"{kind}::{title}::{season_number or 0}",
                "kind": kind,
                "title": title,
                "season_number": season_number,
                "score": round(score, 2),
                "reason": {
                    "source": "external_catalog",
                    "genre_score": genre_score,
        "anime_penalty": 0.0,
                    "franchise_score": franchise_score,
                    "genres": genres,
                    "franchise": franchise,
                },
            }
        )
    out.sort(key=lambda x: x["score"], reverse=True)
    return out


# -----------------------------
# Jellyfin checks
# -----------------------------


def jellyfin_headers(cfg: Config) -> dict[str, str]:
    return {"X-Emby-Token": cfg.jellyfin_token} if cfg.jellyfin_token else {}



def jellyfin_has_movie(cfg: Config, title: str) -> bool:
    if not cfg.jellyfin_url or not cfg.jellyfin_token:
        return False

    target = normalize_movie_presence_title(title)
    if not target:
        return False

    queries = []
    raw = normalize_title(title)
    if raw:
        queries.append(raw)

    stripped = re.sub(r"\(\d{4}\)", " ", raw)
    stripped = re.sub(r"\b\d{4}\b", " ", stripped)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    if stripped and stripped not in queries:
        queries.append(stripped)

    norm = normalize_movie_presence_title(title)
    if norm and norm not in queries:
        queries.append(norm)

    for q in queries:
        try:
            r = requests.get(
                f"{cfg.jellyfin_url}/Items",
                params={
                    "Recursive": "true",
                    "IncludeItemTypes": "Movie",
                    "SearchTerm": q,
                },
                headers=jellyfin_headers(cfg),
                timeout=10,
            )
            r.raise_for_status()
            items = (r.json() or {}).get("Items") or []
        except requests.RequestException:
            items = []

        for item in items:
            name = item.get("Name") or ""
            if normalize_movie_presence_title(name) == target:
                return True

    return False

def jellyfin_has_any_episode_of_season(cfg: Config, title: str, season: int) -> bool:
    if not cfg.jellyfin_url or not cfg.jellyfin_token:
        return False
    try:
        r = requests.get(
            f"{cfg.jellyfin_url}/Items",
            params={
                "Recursive": "true",
                "IncludeItemTypes": "Episode",
                "SearchTerm": title,
                "ParentIndexNumber": season,
            },
            headers=jellyfin_headers(cfg),
            timeout=10,
        )
        r.raise_for_status()
        return r.json().get("TotalRecordCount", 0) > 0
    except requests.RequestException:
        return False


# -----------------------------
# aMule wrapper
# -----------------------------


def amule_headers(cfg: Config) -> dict[str, str]:
    return {"X-API-Key": cfg.amule_api_key, "Content-Type": "application/json"}



def amule_search(cfg: Config, query: str) -> list[dict[str, Any]]:
    if not cfg.amule_api_url:
        return []
    try:
        created = requests.post(
            f"{cfg.amule_api_url}/jobs",
            json={"query": query},
            headers=amule_headers(cfg),
            timeout=15,
        )
        created.raise_for_status()
        jid = created.json()["id"]
        runner = requests.post(
            f"{cfg.amule_api_url}/jobs/{jid}/run",
            headers=amule_headers(cfg),
            timeout=120,
        )
        runner.raise_for_status()
        res = requests.get(
            f"{cfg.amule_api_url}/jobs/{jid}/results",
            headers=amule_headers(cfg),
            timeout=20,
        )
        res.raise_for_status()
        return res.json()
    except requests.RequestException:
        return []



def amule_download(cfg: Config, result_id: int) -> bool:
    if not cfg.amule_api_url:
        return False
    try:
        r = requests.post(
            f"{cfg.amule_api_url}/download",
            json={"result_ids": [result_id]},
            headers=amule_headers(cfg),
            timeout=120,
        )
        r.raise_for_status()
        data = r.json()
        return data["results"][0].get("status") == "ok"
    except requests.RequestException:
        return False



def rank_search_results(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = []
    for it in raw:
        name = str(it.get("filename", ""))
        low = name.lower()
        score = int(it.get("sources", 0)) * 10
        if "ita" in low or "italian" in low:
            score += 500
        if "720p" in low:
            score += 300
        if "1080p" in low:
            score += 100
        if "2160p" in low or "4k" in low:
            score -= 1000
        if any(x in low for x in ["cam", "ts", "tc", "telesync"]):
            score -= 1000
        ranked.append(
            {
                "id": it.get("id"),
                "name": name,
                "sources": it.get("sources", 0),
                "size_bytes": it.get("size_bytes", 0),
                "score": score,
            }
        )
    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked


# -----------------------------
# Decisions
# -----------------------------



def propose_from_backlog(events: list[dict[str, Any]], backlog_file: str) -> list[dict[str, Any]]:
    path = Path(backlog_file)
    if not backlog_file or not path.exists():
        return []

    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    if not isinstance(rows, list):
        return []

    out: list[dict[str, Any]] = []
    for item in rows:
        if str(item.get("kind") or "").lower() != "movie":
            continue

        title = str(item.get("title") or "").strip()
        if not title:
            continue

        year = item.get("year")
        try:
            year = int(year) if year not in (None, "") else None
        except Exception:
            year = None

        candidate_title = f"{title} ({year})" if year else title

        out.append({
            "candidate_id": f"movie::{candidate_title}::0",
            "kind": "movie",
            "title": candidate_title,
            "season_number": None,
            "score": 0.0,
            "reason": {
                "source": "movie_backlog",
                "genres": item.get("genres") or item.get("genre_names") or [],
                "franchise": item.get("franchise"),
                "bucket": "back_catalog",
            },
            "director": item.get("director"),
            "writers": item.get("writers") or [],
            "cast": item.get("cast") or [],
            "year": year,
            "tmdb_id": item.get("tmdb_id"),
            "overview": item.get("overview") or "",
            "popularity": item.get("popularity") or 0,
            "vote_average": item.get("vote_average") or 0,
            "vote_count": item.get("vote_count") or 0,
            "genre_ids": item.get("genre_ids") or [],
            "franchise": item.get("franchise"),
            "bucket": "back_catalog",
            "priority_boost": int(item.get("priority") or 0),
        })

    return out


def filter_candidates(cfg: Config, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in candidates:
        c = dict(c)
        if c["kind"] == "movie":
            already = jellyfin_has_movie(cfg, c["title"])
            c["already_present"] = already
            if not already:
                out.append(c)
        else:
            # NON scartare una stagione solo perché su Jellyfin esiste già qualche episodio.
            # La deduplica fine degli episodi avviene più avanti nel path Peppule/Jellyfin.
            c["already_present"] = False
            out.append(c)
    return out





def build_movie_query_variants(candidate: dict[str, Any]) -> list[str]:
    title = str(candidate.get("title") or "").strip()
    if not title:
        return []

    variants = [title]

    if " - " in title:
        variants.append(title.split(" - ", 1)[0].strip())

    if ":" in title:
        variants.append(title.split(":", 1)[0].strip())

    clean = re.sub(r"[^\w\s]", " ", title)
    clean = re.sub(r"\s+", " ", clean).strip()
    if clean and clean != title:
        variants.append(clean)

    seeds = list(variants)
    for v in seeds:
        variants.append(f"{v} ita")
        variants.append(f"{v} 1080p")
        variants.append(f"{v} ita 1080p")

    out = []
    for v in variants:
        v = re.sub(r"\s+", " ", v).strip()
        if v and v not in out:
            out.append(v)
    return out

def probe_movie_query_variants(cfg: Config, candidate: dict[str, Any]) -> tuple[int, str | None]:
    import time

    best_hits = 0
    best_query = None

    queries = build_movie_query_variants(candidate)[:6]

    for q in queries:
        for delay in (0.0, 2.0):
            if delay > 0:
                time.sleep(delay)
            try:
                raw = amule_search(cfg, q)
                ranked = rank_search_results(raw)
            except Exception:
                ranked = []

            hits = len(ranked)
            if hits > best_hits:
                best_hits = hits
                best_query = q

            if hits > 0:
                return best_hits, best_query

    return best_hits, best_query


def build_query(candidate: dict[str, Any]) -> str:
    if candidate["kind"] == "movie":
        return f"{candidate['title']} ita"
    season = int(candidate.get("season_number") or 1)
    return f"{normalize_series_query_title(candidate['title'])} S{season:02d} ita"



def _infer_required_prequel_title(title: str | None) -> str | None:
    t = normalize_title(title or "")
    if not t:
        return None

    patterns = [
        r"^(.*?)(?:\s*[:-]\s*)?part\s+(?:two|2|ii)\s*$",
        r"^(.*?)\s+(?:2|ii)\s*$",
    ]

    for pat in patterns:
        m = re.match(pat, t, flags=re.I)
        if m:
            base = m.group(1).strip(" -:")
            base = re.sub(r"\s+", " ", base).strip()
            return base or None

    return None



def _norm_exec_title(t: str | None) -> str:
    t = str(t or "").strip().lower()
    t = re.sub(r'\s*[-:]\s*s\d+\s*$', '', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t

def _execution_item_key(x: dict[str, Any]) -> tuple[str, int]:
    payload = dict((x or {}).get("payload") or {})
    title = payload.get("title") or x.get("title") or ""
    season = payload.get("season") or x.get("season_number") or x.get("season") or 0
    try:
        season = int(season or 0)
    except Exception:
        season = 0
    return (_norm_exec_title(title), season)

def _candidate_queue_key(x: dict[str, Any]) -> tuple[str, int]:
    reason = dict((x or {}).get("reason") or {})
    kind = str((x or {}).get("kind") or "").lower()

    if kind == "movie":
        title = x.get("title") or ""
        return (_norm_exec_title(title), 0)

    title = reason.get("base_franchise") or x.get("title") or ""
    season = x.get("season_number") or x.get("season") or 0
    try:
        season = int(season or 0)
    except Exception:
        season = 0
    return (_norm_exec_title(title), season)

def _looks_already_on_jellyfin_exec(x: dict[str, Any]) -> bool:
    if str((x or {}).get("status") or "") != "ok":
        return False
    resp = dict((x or {}).get("response") or {})
    out = str(resp.get("output") or x.get("detail") or "").lower()
    return (
        "già su jellyfin" in out
        and "download ok 0" in out
        and "falliti 0" in out
        and "mancanti 0" in out
    )

def _already_on_jellyfin_from_history(out_dir: Path) -> set[tuple[str, int]]:
    p = out_dir / "executions.json"
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return set()
    out: set[tuple[str, int]] = set()
    for x in data if isinstance(data, list) else []:
        if _looks_already_on_jellyfin_exec(x):
            out.add(_execution_item_key(x))
    return out

def decide(cfg: Config, candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    suggestions: list[dict[str, Any]] = []
    queue: list[dict[str, Any]] = []
    downloads_used = 0

    for c in sorted(candidates, key=lambda x: x["score"], reverse=True):
        c = dict(c)
        reason = dict(c.get("reason") or {})

        weak_generic_signal = (
            float(reason.get("franchise_score", 0) or 0) == 0
            and float(reason.get("sequel_bonus", 0) or 0) == 0
            and float(reason.get("multiuser_bonus", 0) or 0) == 0
            and float(reason.get("manual_favorite_bonus", 0) or 0) == 0
            and float(reason.get("director_score", 0) or 0) == 0
            and float(reason.get("writer_score", 0) or 0) == 0
            and float(reason.get("cast_score", 0) or 0) == 0
            and not (reason.get("matched_directors") or [])
            and not (reason.get("matched_writers") or [])
            and not (reason.get("matched_cast") or [])
        )

        followed_promoted_series = (
            c.get("kind") == "series"
            and str(reason.get("queue_promote") or "") == "followed_series_first_available"
        )

        if c["kind"] == "movie":
            strong_movie_signal = not weak_generic_signal
        else:
            strong_movie_signal = True

        if c["kind"] == "series" and not c.get("season_number"):
            c["decision"] = "suggest_only"
            reason["queue_skip"] = "missing season_number"
            c["reason"] = reason
            suggestions.append(c)
            continue

        if c["kind"] == "movie" and weak_generic_signal:
            c["decision"] = "discard"
            reason["queue_skip"] = "genre_only_noise"
        elif followed_promoted_series and downloads_used < cfg.max_auto_download_per_day:
            c["decision"] = "auto_download"
            reason.pop("queue_skip", None)
            downloads_used += 1
            queue.append(c)
        elif c["score"] < (cfg.movie_suggest_threshold if c["kind"] == "movie" else cfg.suggest_threshold):
            c["decision"] = "discard"
        elif (
            (c["score"] >= (cfg.movie_auto_download_threshold if c["kind"] == "movie" else cfg.auto_download_threshold)) or followed_promoted_series
        ) and downloads_used < cfg.max_auto_download_per_day:
            if c["kind"] == "movie" and not strong_movie_signal:
                c["decision"] = "suggest_only"
                reason["queue_skip"] = "weak_movie_signal"
            else:
                c["decision"] = "auto_download"
                reason.pop("queue_skip", None)
                downloads_used += 1
                queue.append(c)
        else:
            c["decision"] = "suggest_only"

        c["reason"] = reason
        suggestions.append(c)



    # fallback: almeno 1 film al giorno, anche se in queue c'è già una serie
    has_movie_in_queue = any(str(x.get("kind") or "").lower() == "movie" for x in queue)

    if not has_movie_in_queue:
        movie_candidates = []
        for c in suggestions:
            reason = dict(c.get("reason") or {})
            if c.get("kind") != "movie":
                continue
            if c.get("already_present"):
                continue
            if reason.get("queue_skip") == "missing_prequel":
                continue
            if str(c.get("decision") or "") == "discard" and float(c.get("score") or 0) < float(cfg.suggest_threshold):
                continue

            strong_movie_signal = any([
                bool((reason.get("matched_directors") or [])),
                bool((reason.get("matched_writers") or [])),
                bool((reason.get("matched_cast") or [])),
                float(reason.get("manual_favorite_bonus", 0) or 0) > 0,
                float(reason.get("franchise_score", 0) or 0) > 0,
                str(reason.get("source") or "") == "movie_backlog",
            ])

            if not strong_movie_signal:
                continue

            movie_candidates.append(c)

        movie_candidates.sort(key=lambda x: float(x.get("score") or 0), reverse=True)

        for c in movie_candidates:
            reason = dict(c.get("reason") or {})
            best_hits, best_query = probe_movie_query_variants(cfg, c)

            if best_hits <= 0:
                continue

            c["decision"] = "auto_download"
            reason["queue_promote"] = "daily_fallback_movie_best_clean"
            reason["fallback_probe_hits"] = best_hits
            reason["fallback_probe_query"] = best_query
            c["reason"] = reason
            queue.append(c)
            break

    return suggestions, queue

def execute_queue(cfg: Config, queue: list[dict[str, Any]]) -> list[dict[str, Any]]:
    executions = []
    for c in queue:
        query = build_query(c)
        raw = amule_search(cfg, query)
        ranked = rank_search_results(raw)
        best = ranked[0] if ranked else None
        status = "no_results"
        detail = {"query": query, "top": best}
        if best and isinstance(best.get("id"), int):
            ok = amule_download(cfg, best["id"])
            status = "ok" if ok else "download_failed"
        executions.append(
            {
                "candidate_id": c["candidate_id"],
                "action": "download",
                "status": status,
                "detail": detail,
                "created_at": utcnow_iso(),
            }
        )
    return executions


# -----------------------------
# Persistence / output
# -----------------------------


def persist_candidates(db_path: str, items: list[dict[str, Any]]) -> None:
    now = utcnow_iso()
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM candidates")
        for c in items:
            conn.execute(
                "INSERT OR REPLACE INTO candidates(candidate_id, kind, title, season_number, score, reason_json, decision, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    c["candidate_id"],
                    c["kind"],
                    c["title"],
                    c.get("season_number"),
                    c["score"],
                    safe_json_dumps(c.get("reason", {})),
                    c.get("decision", "unknown"),
                    now,
                ),
            )
        conn.commit()



def persist_executions(db_path: str, executions: list[dict[str, Any]]) -> None:
    with sqlite3.connect(db_path) as conn:
        for idx, ex in enumerate(executions, start=1):
            execution_id = f"{ex['candidate_id']}::{idx}::{int(dt.datetime.now().timestamp())}"
            conn.execute(
                "INSERT OR REPLACE INTO executions(execution_id, candidate_id, action, status, detail, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    execution_id,
                    ex["candidate_id"],
                    ex["action"],
                    ex["status"],
                    safe_json_dumps(ex.get("detail", {})),
                    ex["created_at"],
                ),
            )
        conn.commit()



def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# -----------------------------
# CLI
# -----------------------------



def _apply_series_gate(items: list[dict], cfg) -> list[dict]:
    min_score = float(getattr(cfg, "series_min_auto_score", 12.0) or 12.0)
    max_auto = int(getattr(cfg, "series_max_auto", 1) or 1)

    out = []
    kept_nonfollowed = 0

    for item in items:
        if str(item.get("kind") or "").lower() != "series":
            out.append(item)
            continue

        score = float(item.get("score") or 0)
        reason = dict(item.get("reason") or {})

        followed = any([
        str(reason.get("queue_promote") or "") == "followed_series_first_available",
        str(reason.get("queue_gate") or "") == "series_followed_ge_3",
    ])

        if followed:
            out.append(item)
            continue

        if score < min_score:
            reason["queue_skip"] = reason.get("queue_skip") or "series_low_score"
            item["reason"] = reason
            out.append(item)
            continue

        kept_nonfollowed += 1
        if kept_nonfollowed > max_auto:
            reason["queue_skip"] = reason.get("queue_skip") or "series_limit_reached"
            item["reason"] = reason
            out.append(item)
            continue

        out.append(item)

    return out

def normalize_peppule_series_title(title: str) -> str:
    t = (title or "").strip()
    t = re.sub(r"\s*[-:]\s*S\d+\s*$", "", t, flags=re.I)
    t = re.sub(r"\s+S\d+\s*$", "", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip()
    return t


SERIES_ALIASES = {
    "scissione": "severance",
    "severance": "severance",
}


def _canonical_series_key(title: str | None, base_franchise: str | None = None) -> str:
    raw = normalize_peppule_series_title(base_franchise or title or "").strip().lower()
    raw = re.sub(r"\s*[-:]\s*s\d+\s*$", "", raw, flags=re.I)
    raw = re.sub(r"\s+", " ", raw).strip()
    return SERIES_ALIASES.get(raw, raw)

def _series_progress_from_views(path: Path) -> Dict[str, Dict[str, int]]:
    rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    if not isinstance(rows, list):
        return {}

    progress: Dict[str, Dict[str, int]] = {}
    for item in rows:
        if str(item.get("kind") or "").lower() != "series":
            continue

        key = _canonical_series_key(item.get("series_name") or item.get("title"))
        if not key:
            continue

        state = progress.setdefault(key, {"episodes_watched": 0, "max_season_watched": 0})
        state["episodes_watched"] += 1

        try:
            season = int(item.get("season_number") or 0)
        except Exception:
            season = 0

        if season > state["max_season_watched"]:
            state["max_season_watched"] = season

    return progress


def _promote_followed_series(filtered: List[Dict[str, Any]], queue: List[Dict[str, Any]], views_file: str, max_items: int) -> List[Dict[str, Any]]:
    if max_items <= 0:
        return queue

    progress = _series_progress_from_views(Path(views_file))

    # arricchisci il progresso con il massimo episodio visto per stagione, se disponibile nelle views
    try:
        raw_views = json.loads(Path(views_file).read_text(encoding="utf-8"))
        if isinstance(raw_views, dict):
            raw_views = raw_views.get("events", [])
    except Exception:
        raw_views = []

    for ev in raw_views if isinstance(raw_views, list) else []:
        title = ev.get("series_title") or ev.get("series_name") or ev.get("title")
        key = _canonical_series_key(title, None)
        if not key:
            continue

        try:
            season = int(ev.get("season_number") or 0)
        except Exception:
            season = 0

        try:
            episode = int(ev.get("episode_number") or ev.get("index_number") or 0)
        except Exception:
            episode = 0

        completed = ev.get("completed")
        try:
            progress_ratio = float(ev.get("progress") or 0)
        except Exception:
            progress_ratio = 0.0

        if season <= 0 or episode <= 0:
            continue
        if completed is False and progress_ratio < 0.9:
            continue

        state = progress.setdefault(key, {"episodes_watched": 0, "max_season_watched": 0})
        by_season = state.setdefault("max_episode_watched_by_season", {})
        prev = int(by_season.get(str(season)) or 0)
        if episode > prev:
            by_season[str(season)] = episode
        if season > int(state.get("max_season_watched", 0) or 0):
            state["max_season_watched"] = season

    existing = {str(item.get("candidate_id") or item.get("title") or "") for item in queue}

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for item in filtered:
        if str(item.get("kind") or "").lower() != "series":
            continue
        if item.get("already_present"):
            continue

        try:
            season_number = int(item.get("season_number") or 0)
        except Exception:
            season_number = 0

        if season_number <= 0:
            continue

        reason = item.get("reason") or {}
        key = _canonical_series_key(item.get("title"), reason.get("base_franchise"))
        if not key:
            continue

        grouped.setdefault(key, []).append(item)

    added = 0
    for key, state in sorted(progress.items(), key=lambda kv: (-int(kv[1].get("episodes_watched", 0)), kv[0])):
        if added >= max_items:
            break

        episodes_watched = int(state.get("episodes_watched", 0))
        max_season_watched = int(state.get("max_season_watched", 0))

        if episodes_watched < 3:
            continue

        candidates = grouped.get(key, [])
        if not candidates:
            continue

        candidates = sorted(candidates, key=lambda x: int(x.get("season_number") or 0))

        next_expected_season = max_season_watched + 1

        chosen = None

        # 1) preferisci la stagione immediatamente successiva a quella già vista
        for item in candidates:
            cid = str(item.get("candidate_id") or item.get("title") or "")
            try:
                item_season = int(item.get("season_number") or 0)
            except Exception:
                item_season = 0

            if cid in existing:
                continue
            if item_season == next_expected_season:
                chosen = item
                break

        # 2) fallback: prima stagione disponibile oltre quella già vista
        if not chosen:
            for item in candidates:
                cid = str(item.get("candidate_id") or item.get("title") or "")
                try:
                    item_season = int(item.get("season_number") or 0)
                except Exception:
                    item_season = 0

                if cid in existing:
                    continue
                if item_season > max_season_watched:
                    chosen = item
                    break

        if not chosen:
            continue

        promoted = dict(chosen)
        reason = dict(promoted.get("reason") or {})
        reason["queue_promote"] = "followed_series_first_available"
        reason["episodes_watched"] = episodes_watched
        reason["max_season_watched"] = max_season_watched

        try:
            promoted_season = int(promoted.get("season_number") or 0)
        except Exception:
            promoted_season = 0

        by_season = state.get("max_episode_watched_by_season") or {}
        last_ep_current = int(by_season.get(str(max_season_watched)) or 0)

        if promoted_season == max_season_watched and last_ep_current > 0:
            reason["peppule_start_episode"] = last_ep_current + 1
        elif promoted_season == max_season_watched + 1:
            reason["peppule_start_episode"] = 1

        reason["peppule_max_episodes"] = 4
        promoted["reason"] = reason
        promoted["decision"] = "auto_download"

        queue.append(promoted)
        existing.add(str(promoted.get("candidate_id") or promoted.get("title") or ""))
        added += 1

    return queue


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Jellyfin novità stats agent")
    parser.add_argument("--config", help="JSON config file", default=None)
    parser.add_argument("--views-file", help="Exported views JSON file", default=None)
    parser.add_argument("--catalog-file", help="Optional external candidate catalog JSON", default=None)
    parser.add_argument("--execute", action="store_true", help="Run aMule downloads for auto-download items")
    args = parser.parse_args(argv)

    cfg = Config.from_env_or_file(args.config)
    init_db(cfg.db_path)

    if args.views_file:
        ingested = ingest_views_file(cfg.db_path, args.views_file)
        print(f"ingested view events: {ingested}")

    events = load_recent_events(cfg.db_path, cfg.observation_days)
    if not events:
        print("no events available; import --views-file first", file=sys.stderr)
        return 2

    candidates = propose_continuations(events)
    if args.catalog_file:
        candidates.extend(propose_from_catalog(events, args.catalog_file))
    if cfg.movie_backlog_file:
        candidates.extend(propose_from_backlog(events, cfg.movie_backlog_file))

    deduped: dict[str, dict[str, Any]] = {}
    for c in candidates:
        prev = deduped.get(c["candidate_id"])
        if not prev or c["score"] > prev["score"]:
            deduped[c["candidate_id"]] = c
    candidates = list(deduped.values())
    candidates.sort(key=lambda x: x["score"], reverse=True)


    # --- v2 rescoring based on sequel/franchise ---
    if args.views_file:
        _profile_events = json.loads(Path(args.views_file).read_text(encoding="utf-8"))
        _profile = build_user_profile(_profile_events)
        _catalog_map = {}

        def _merge_catalog_items(_rows):
            for _item in _rows:
                _title = (_item.get("title") or "").strip().lower()
                if not _title:
                    continue
                _catalog_map[((_item.get("kind") or "").lower(), _title)] = _item

                _year = _item.get("year")
                if str((_item.get("kind") or "")).lower() == "movie" and _year not in (None, ""):
                    _title_y = f"{_item.get('title')} ({_year})".strip().lower()
                    _catalog_map[((_item.get("kind") or "").lower(), _title_y)] = _item

        if args.catalog_file:
            try:
                _catalog_items = json.loads(Path(args.catalog_file).read_text(encoding="utf-8"))
                _merge_catalog_items(_catalog_items)
            except Exception:
                _catalog_map = {}

        if cfg.movie_backlog_file:
            try:
                _backlog_items = json.loads(Path(cfg.movie_backlog_file).read_text(encoding="utf-8"))
                _merge_catalog_items(_backlog_items)
            except Exception:
                pass

        _reranked = []
        for _c in candidates:
            _meta = _catalog_map.get((
                (_c.get("kind") or "").lower(),
                (_c.get("title") or "").strip().lower()
            ), {})
            _proxy = {
                "title": _c.get("title"),
                "kind": _c.get("kind"),
                "genres": (
                    (_meta.get("genres") or [])
                    or (_meta.get("genre_names") or [])
                    or ((_c.get("reason") or {}).get("genres") or [])
                ),
                "franchise": (
                    _meta.get("franchise")
                    or ((_c.get("reason") or {}).get("franchise"))
                ),
                "bucket": (
                    _meta.get("bucket")
                    or ((_c.get("reason") or {}).get("bucket"))
                    or "recent_catalog"
                ),
                "priority_boost": _meta.get("priority_boost", _meta.get("priority", 0)),
                "director": _meta.get("director"),
                "writers": _meta.get("writers") or [],
                "cast": _meta.get("cast") or [],
                "year": _meta.get("year"),
            }
            _score, _reason = score_candidate(
                _proxy,
                _profile,
                dataclasses.asdict(cfg)
            )
            # salvagente finale: malus animazione sulle serie
            try:
                _genres = (_reason or {}).get("genres") or _proxy.get("genres") or []
                _norm_genres = [
                    normalize_genre_name(str(g))
                    for g in _genres
                    if str(g).strip()
                ]
                if str(_proxy.get("kind") or "").lower() == "series" and any(g in {"animation", "animazione"} for g in _norm_genres):
                    _anim_pen = float(
                        dataclasses.asdict(cfg).get(
                            "series_animation_penalty",
                            dataclasses.asdict(cfg).get("series_anime_penalty", 12.0)
                        ) or 12.0
                    )
                    _reason = dict(_reason or {})
                    if not _reason.get("anime_penalty"):
                        _reason["anime_penalty"] = round(_anim_pen, 2)
                        _reason["genre_score"] = round(float(_reason.get("genre_score") or 0) - _anim_pen, 2)
                        _score = round(float(_score) - _anim_pen, 2)
            except Exception:
                pass
            _new = dict(_c)
            _new["score"] = _score
            _new["reason"] = _reason
            _reranked.append(_new)
        candidates = sorted(_reranked, key=lambda x: x["score"], reverse=True)
    # --- end v2 rescoring based on sequel/franchise ---

    filtered = filter_candidates(cfg, candidates)
    filtered = _apply_series_gate(filtered, cfg)

    pre_queue = _promote_followed_series(
        filtered,
        [],
        args.views_file,
        int(getattr(cfg, "max_auto_download_per_day", 3) or 3),
    )
    promoted_map = {
        (
            str(x.get("candidate_id") or ""),
            str(x.get("title") or ""),
            int(x.get("season_number") or x.get("season") or 0),
        ): dict(x)
        for x in pre_queue
    }

    promoted_filtered = []
    for x in filtered:
        y = dict(x)
        key = (
            str(y.get("candidate_id") or ""),
            str(y.get("title") or ""),
            int(y.get("season_number") or y.get("season") or 0),
        )
        pr = promoted_map.get(key)
        if pr:
            y = dict(pr)
            r = dict(y.get("reason") or {})
            r.pop("queue_skip", None)
            y["reason"] = r
        promoted_filtered.append(y)

    filtered = promoted_filtered
    suggestions, queue = decide(cfg, filtered)

    out_dir = Path(cfg.output_dir)
    already_done = _already_on_jellyfin_from_history(out_dir)

    queue_clean = []
    for item in queue:
        qk = _candidate_queue_key(item)
        if qk in already_done:
            continue
        queue_clean.append(item)
    queue = queue_clean

    target_queue_len = int(getattr(cfg, "max_auto_download_per_day", 1) or 1)
    min_movie_queue_len = int(getattr(cfg, "min_movie_downloads_per_day", 0) or 0)

    queued_ids = {str(x.get("candidate_id") or "") for x in queue}
    queued_keys = {_candidate_queue_key(x) for x in queue}

    def _movie_has_strong_signal(c: dict[str, Any]) -> bool:
        reason = dict(c.get("reason") or {})
        return any([
            bool(reason.get("matched_directors") or []),
            bool(reason.get("matched_writers") or []),
            bool(reason.get("matched_cast") or []),
            float(reason.get("manual_favorite_bonus", 0) or 0) > 0,
            float(reason.get("franchise_score", 0) or 0) > 0,
            float(reason.get("director_score", 0) or 0) > 0,
            float(reason.get("writer_score", 0) or 0) > 0,
            float(reason.get("cast_score", 0) or 0) > 0,
        ])

    def _candidate_is_fillable(c: dict[str, Any]) -> bool:
        reason = dict(c.get("reason") or {})
        cid = str(c.get("candidate_id") or "")
        ckey = _candidate_queue_key(c)
        kind = str(c.get("kind") or "").lower()

        if cid in queued_ids:
            return False
        if ckey in queued_keys:
            return False
        if c.get("already_present"):
            return False
        if ckey in already_done:
            return False
        if reason.get("queue_skip") == "missing_prequel":
            return False
        if str(c.get("decision") or "") == "discard":
            return False

        # queue_backfill solo per:
        # - film
        # - serie già seguite/promosse dal gate
        if kind == "movie":
            return True

        if kind == "series":
            qp = str(reason.get("queue_promote") or "")
            qg = str(reason.get("queue_gate") or "")
            eps = int(reason.get("episodes_watched") or 0)
            followed = (
                qp == "followed_series_first_available"
                or qg == "series_followed_ge_3"
                or eps >= 3
            )
            return followed

        return False

    fill_pool = [dict(x) for x in suggestions if _candidate_is_fillable(dict(x))]
    fill_pool.sort(key=lambda x: float(x.get("score") or 0), reverse=True)

    # fallback movie post-clean: se la queue finale è vuota o senza film,
    # prova a promuovere il miglior film valido non già presente e non già fatto.
    current_movie_count = sum(1 for x in queue if str(x.get("kind") or "").lower() == "movie")

    if current_movie_count < min_movie_queue_len:
        movie_candidates = []
        for c in suggestions:
            c = dict(c)
            reason = dict(c.get("reason") or {})

            if str(c.get("kind") or "").lower() != "movie":
                continue
            if c.get("already_present"):
                continue
            if _candidate_queue_key(c) in already_done:
                continue
            if str(c.get("candidate_id") or "") in queued_ids:
                continue
            if _candidate_queue_key(c) in queued_keys:
                continue
            if reason.get("queue_skip") == "missing_prequel":
                continue

            # per la quota minima film accetta anche segnali più deboli:
            # il filtro vero lo farà probe_movie_query_variants tramite hits > 0
            movie_candidates.append(c)

        probed_movie_candidates = []
        for c in movie_candidates:
            best_hits, best_query = probe_movie_query_variants(cfg, c)
            if best_hits < 10:
                continue
            probed_movie_candidates.append((best_hits, best_query, c))

        probed_movie_candidates.sort(
            key=lambda t: (int(t[0]), float((t[2] or {}).get("score") or 0)),
            reverse=True
        )

        for best_hits, best_query, c in probed_movie_candidates:
            if current_movie_count >= min_movie_queue_len:
                break

            # se la queue è piena, sostituisci una serie seguita meno prioritaria
            if len(queue) >= target_queue_len:
                replace_idx = None
                for i in range(len(queue) - 1, -1, -1):
                    qitem = queue[i]
                    if str(qitem.get("kind") or "").lower() != "series":
                        continue
                    qreason = dict(qitem.get("reason") or {})
                    if str(qreason.get("queue_promote") or "") == "followed_series_first_available":
                        replace_idx = i
                        break
                if replace_idx is None:
                    break

                removed = queue.pop(replace_idx)
                queued_ids.discard(str(removed.get("candidate_id") or ""))
                try:
                    queued_keys.discard(_candidate_queue_key(removed))
                except Exception:
                    pass

            reason = dict(c.get("reason") or {})
            c["decision"] = "auto_download"
            reason["queue_promote"] = "min_movie_quota"
            reason["fallback_probe_hits"] = best_hits
            reason["fallback_probe_query"] = best_query
            c["reason"] = reason
            queue.append(c)
            queued_ids.add(str(c.get("candidate_id") or ""))
            queued_keys.add(_candidate_queue_key(c))
            current_movie_count += 1

    if not any(str(x.get("kind") or "").lower() == "movie" for x in queue):
        movie_candidates = []
        for c in suggestions:
            c = dict(c)
            reason = dict(c.get("reason") or {})

            if str(c.get("kind") or "").lower() != "movie":
                continue
            if c.get("already_present"):
                continue
            if _candidate_queue_key(c) in already_done:
                continue
            if reason.get("queue_skip") == "missing_prequel":
                continue

            strong_movie_signal = any([
                bool(reason.get("matched_directors") or []),
                bool(reason.get("matched_writers") or []),
                bool(reason.get("matched_cast") or []),
                float(reason.get("manual_favorite_bonus", 0) or 0) > 0,
                float(reason.get("franchise_score", 0) or 0) > 0,
                float(reason.get("director_score", 0) or 0) > 0,
                float(reason.get("writer_score", 0) or 0) > 0,
                float(reason.get("cast_score", 0) or 0) > 0,
            ])
            if not strong_movie_signal:
                continue

            movie_candidates.append(c)

        probed_movie_candidates = []
        for c in movie_candidates:
            best_hits, best_query = probe_movie_query_variants(cfg, c)
            if best_hits <= 0:
                continue
            probed_movie_candidates.append((best_hits, best_query, c))

        probed_movie_candidates.sort(
            key=lambda t: (int(t[0]), float((t[2] or {}).get("score") or 0)),
            reverse=True
        )

        for best_hits, best_query, c in probed_movie_candidates:
            reason = dict(c.get("reason") or {})
            c["decision"] = "auto_download"
            reason["queue_promote"] = "postclean_daily_fallback_movie"
            reason["fallback_probe_hits"] = best_hits
            reason["fallback_probe_query"] = best_query
            c["reason"] = reason
            queue.append(c)
            queued_ids.add(str(c.get("candidate_id") or ""))
            queued_keys.add(_candidate_queue_key(c))
            break

    if len(queue) < target_queue_len:
        for c in fill_pool:
            if len(queue) >= target_queue_len:
                break

            cid = str(c.get("candidate_id") or "")
            ckey = _candidate_queue_key(c)
            if cid in queued_ids or ckey in queued_keys:
                continue

            reason = dict(c.get("reason") or {})
            c["decision"] = "auto_download"
            reason.setdefault("queue_promote", "queue_backfill")
            c["reason"] = reason
            queue.append(c)
            queued_ids.add(cid)
            queued_keys.add(ckey)

    persist_candidates(cfg.db_path, suggestions)

    write_json(out_dir / "candidate_novita.json", candidates)

    queue_map = {
        str(x.get("candidate_id") or ""): x
        for x in queue
    }

    suggestions_aligned = []
    for x in suggestions:
        y = dict(x)
        r = dict(y.get("reason") or {})
        q = queue_map.get(str(y.get("candidate_id") or ""))

        if q:
            y["decision"] = q.get("decision", y.get("decision"))
            rq = dict(q.get("reason") or {})
            for k, v in rq.items():
                r[k] = v
            y["reason"] = r

        suggestions_aligned.append(y)

    write_json(out_dir / "novita_filtrate.json", suggestions_aligned)
    write_json(out_dir / "download_queue.json", queue)

    executions: list[dict[str, Any]] = []
    if args.execute and queue:
        executions = execute_queue(cfg, queue)
        persist_executions(cfg.db_path, executions)
        write_json(out_dir / "executions.json", executions)

    summary = {
        "events": len(events),
        "candidates": len(candidates),
        "filtered": len(suggestions),
        "queue": len(queue),
        "executed": len(executions),
        "output_dir": str(out_dir),
        "db_path": cfg.db_path,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
