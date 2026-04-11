from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
SEED_PATH = BASE_DIR / "catalog_seed.json"
CATALOG_PATH = BASE_DIR / "external_catalog.json"
VIEWS_PATH = BASE_DIR / "views_90d.clean.json"
OUT_DIR = BASE_DIR / "out"
DATA_DIR = BASE_DIR / "data"
AGENT_PATH = BASE_DIR / "jellyfin_novita_agent.py"
BUILD_CATALOG_PATH = BASE_DIR / "build_external_catalog_tmdb.py"

DEFAULT_CONFIG: Dict[str, Any] = {
    "jellyfin_url": "http://10.252.14.7:8096",
    "jellyfin_token": "",
    "amule_api_url": "http://10.252.14.7:8081",
    "amule_api_key": "",
    "cheshire_ws_url": "ws://10.252.14.7:1865/ws",
    "cheshire_api_key": "",
    "tmdb_bearer": "",
    "tmdb_api_key": "",
    "db_path": str(DATA_DIR / "novita.db"),
    "output_dir": str(OUT_DIR),
    "observation_days": 90,
    "auto_download_threshold": 30,
    "suggest_threshold": 12,
    "max_auto_download_per_day": 3,
    "tmdb_language": "it-IT",
    "tmdb_region": "IT",
    "tmdb_movie_pages": 1,
    "tmdb_tv_pages": 1,
    "execution_path": "peppule",
    "dry_run": True,
}


class BuildCatalogRequest(BaseModel):
    seed_file: str = Field(default=str(SEED_PATH))
    out_file: str = Field(default=str(CATALOG_PATH))
    movie_pages: int = Field(default=1, ge=1, le=10)
    tv_pages: int = Field(default=1, ge=1, le=10)
    language: str = Field(default="it-IT")
    region: str = Field(default="IT")


class RunAgentRequest(BaseModel):
    config_file: str = Field(default=str(CONFIG_PATH))
    views_file: str = Field(default=str(VIEWS_PATH))
    catalog_file: str = Field(default=str(CATALOG_PATH))
    execute: bool = False


class ConnectionTestRequest(BaseModel):
    url: Optional[str] = None
    token: Optional[str] = None
    api_key: Optional[str] = None
    bearer: Optional[str] = None


class SaveConfigRequest(BaseModel):
    config: Dict[str, Any]


app = FastAPI(title="Jellyfin Novita Backend", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def ensure_dirs() -> None:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)


ensure_dirs()


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


@app.get("/api/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config")
def get_config() -> Dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(read_json(CONFIG_PATH, {}))
    return cfg


@app.post("/api/config")
def save_config(req: SaveConfigRequest) -> Dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(req.config)
    write_json(CONFIG_PATH, cfg)
    return {"status": "saved", "path": str(CONFIG_PATH)}


@app.get("/api/seed")
def get_seed() -> List[Dict[str, Any]]:
    return read_json(SEED_PATH, [])


@app.post("/api/seed")
def save_seed(payload: List[Dict[str, Any]]) -> Dict[str, Any]:
    write_json(SEED_PATH, payload)
    return {"status": "saved", "path": str(SEED_PATH), "items": len(payload)}


@app.get("/api/catalog")
def get_catalog() -> List[Dict[str, Any]]:
    return read_json(CATALOG_PATH, [])


@app.get("/api/results")
def get_results() -> Dict[str, Any]:
    files = {}
    for name in ["candidate_novita.json", "novita_filtrate.json", "download_queue.json"]:
        path = OUT_DIR / name
        files[name] = read_json(path, []) if path.exists() else []
    return files


@app.post("/api/build-catalog")
def build_catalog(req: BuildCatalogRequest) -> Dict[str, Any]:
    if not BUILD_CATALOG_PATH.exists():
        raise HTTPException(status_code=500, detail=f"script non trovato: {BUILD_CATALOG_PATH}")

    cfg = get_config()
    env = os.environ.copy()
    if cfg.get("tmdb_bearer"):
        env["TMDB_BEARER"] = cfg["tmdb_bearer"]
    if cfg.get("tmdb_api_key"):
        env["TMDB_API_KEY"] = cfg["tmdb_api_key"]
    env["TMDB_LANGUAGE"] = req.language
    env["TMDB_REGION"] = req.region

    cmd = [
        sys.executable,
        str(BUILD_CATALOG_PATH),
        "--seed",
        req.seed_file,
        "--out",
        req.out_file,
        "--movie-pages",
        str(req.movie_pages),
        "--tv-pages",
        str(req.tv_pages),
    ]

    try:
        result = subprocess.run(
            cmd,
            cwd=str(BASE_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="build catalog timeout")

    if result.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail={
                "message": "build catalog fallito",
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
            },
        )

    payload = {}
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1]) if result.stdout.strip() else {}
    except Exception:
        payload = {"stdout": result.stdout}

    return {
        "status": "ok",
        "command": cmd,
        "result": payload,
        "stdout": result.stdout,
    }

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
    rows = read_json(path, [])
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

        chosen = None
        for item in candidates:
            cid = str(item.get("candidate_id") or item.get("title") or "")
            if cid in existing:
                continue
            chosen = item
            break

        if not chosen:
            continue

        promoted = dict(chosen)
        reason = dict(promoted.get("reason") or {})
        reason["queue_promote"] = "followed_series_first_available"
        reason["episodes_watched"] = episodes_watched
        reason["max_season_watched"] = max_season_watched
        promoted["reason"] = reason
        promoted["decision"] = "auto_download"

        queue.append(promoted)
        existing.add(str(promoted.get("candidate_id") or promoted.get("title") or ""))
        added += 1

    return queue


def _rewrite_series_queue_for_onboarding(queue: List[Dict[str, Any]], views_file: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    for item in queue:
        if str(item.get("kind") or "").lower() != "series":
            out.append(item)
            continue

        reason = dict(item.get("reason") or {})
        qp = str(reason.get("queue_promote") or "")
        eps = int(reason.get("episodes_watched") or 0)

        followed = (
            qp == "followed_series_first_available"
            or str(reason.get("queue_gate") or "") == "series_followed_ge_3"
            or eps >= 3
        )

        if followed:
            out.append(item)
            continue

        if qp == "taste_based_series_onboarding":
            new_item = dict(item)
            new_reason = dict(reason)
            new_item["season_number"] = 1
            new_item["decision"] = "auto_download"
            new_reason["peppule_start_episode"] = 1
            new_reason["peppule_max_episodes"] = 3
            new_item["reason"] = new_reason
            out.append(new_item)
            continue

        # tutto il resto non deve entrare in auto queue
        continue

    return out

def _movie_title_key(title: str | None) -> str:
    t = (title or "").lower().strip()
    t = re.sub(r"\(\d{4}\)", " ", t)
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _movie_exec_ok(res: Dict[str, Any]) -> bool:
    if res.get("status") != "ok":
        return False
    response = res.get("response") or {}
    out = str(response.get("output") or "")
    return "AUTO-DL:" in out and "✅ OK" in out



def _collect_unfollowed_continuation_onboarding(filtered: List[Dict[str, Any]], existing_queue: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    existing_keys = set()
    for item in existing_queue:
        if str(item.get("kind") or "").lower() != "series":
            continue
        key = str((item.get("reason") or {}).get("base_franchise") or item.get("title") or "").strip().lower()
        if key:
            existing_keys.add(key)

    out: List[Dict[str, Any]] = []
    seen = set()

    for item in filtered:
        if str(item.get("kind") or "").lower() != "series":
            continue
        if item.get("already_present"):
            continue
        if str(item.get("decision") or "").lower() != "discard":
            continue

        reason = dict(item.get("reason") or {})
        if reason.get("bucket") != "continuation":
            continue

        franchise_score = float(reason.get("franchise_score") or 0)
        sequel_bonus = float(reason.get("sequel_bonus") or 0)
        multiuser_bonus = float(reason.get("multiuser_bonus") or 0)

        if franchise_score > 0 or sequel_bonus > 0 or multiuser_bonus > 0:
            continue

        base_key = str(reason.get("base_franchise") or item.get("title") or "").strip().lower()
        if not base_key or base_key in existing_keys or base_key in seen:
            continue

        new_item = dict(item)
        new_reason = dict(reason)
        new_item["season_number"] = 1
        new_item["decision"] = "auto_download"
        new_reason["queue_promote"] = "unfollowed_continuation_onboarding"
        new_reason["force_onboarding"] = True
        new_reason["peppule_start_episode"] = 1
        new_reason["peppule_max_episodes"] = 3
        new_item["reason"] = new_reason

        out.append(new_item)
        seen.add(base_key)

    return out


def _collect_taste_based_series_onboarding(
    filtered: List[Dict[str, Any]],
    existing_queue: List[Dict[str, Any]],
    views_file: str,
    min_genre_score: float = 8.0,
    min_total_score: float = 18.0,
    max_items: int = 1,
    block_title_terms: List[str] | None = None,
    block_genres: List[str] | None = None,
) -> List[Dict[str, Any]]:
    progress = _series_progress_from_views(Path(views_file))
    block_title_terms = [str(x).strip().lower() for x in (block_title_terms or []) if str(x).strip()]
    block_genres = [str(x).strip().lower() for x in (block_genres or []) if str(x).strip()]

    existing_keys = set()
    for item in existing_queue:
        if str(item.get("kind") or "").lower() != "series":
            continue
        reason = item.get("reason") or {}
        key = _canonical_series_key(item.get("title"), reason.get("base_franchise"))
        if key:
            existing_keys.add(key)

    out: List[Dict[str, Any]] = []
    seen = set()

    for item in filtered:
        if str(item.get("kind") or "").lower() != "series":
            continue
        if item.get("already_present"):
            continue

        reason = dict(item.get("reason") or {})
        key = _canonical_series_key(item.get("title"), reason.get("base_franchise"))
        if not key or key in existing_keys or key in seen:
            continue

        title_l = str(item.get("title") or "").strip().lower()
        if any(term in title_l for term in block_title_terms):
            continue

        raw_genres = [str(x).strip().lower() for x in (reason.get("genres") or [])]
        if any(g in block_genres for g in raw_genres):
            continue

        blocked_genres = [str(x).strip().lower() for x in (cfg.get("series_suggested_block_genres", []) if "cfg" in globals() else [])]
        raw_genres = [str(x).strip().lower() for x in (reason.get("genres") or [])]
        if any(g in blocked_genres for g in raw_genres):
            continue

        state = progress.get(key, {})
        episodes_watched = int(state.get("episodes_watched", 0))

        franchise_score = float(reason.get("franchise_score") or 0)
        sequel_bonus = float(reason.get("sequel_bonus") or 0)
        multiuser_bonus = float(reason.get("multiuser_bonus") or 0)

        followed = (
            episodes_watched > 0
            or franchise_score > 0
            or sequel_bonus > 0
            or multiuser_bonus > 0
        )
        if followed:
            continue

        genre_score = float(reason.get("genre_score") or 0)
        manual_genre_bonus = float(reason.get("manual_genre_bonus") or 0)
        taste_score = genre_score + manual_genre_bonus
        total_score = float(item.get("score") or 0)

        if taste_score < float(min_genre_score):
            continue
        if total_score < float(min_total_score):
            continue

        new_item = dict(item)
        new_reason = dict(reason)
        new_item["season_number"] = 1
        new_item["decision"] = "auto_download"
        new_reason["queue_promote"] = "taste_based_series_onboarding"
        new_reason["force_onboarding"] = True
        new_reason["episodes_watched"] = episodes_watched
        new_reason["taste_score"] = taste_score
        new_reason["peppule_start_episode"] = 1
        new_reason["peppule_max_episodes"] = 3
        new_item["reason"] = new_reason

        out.append(new_item)
        seen.add(key)

    out.sort(key=lambda x: (-float((x.get("reason") or {}).get("taste_score") or 0), -float(x.get("score") or 0), str(x.get("title") or "")))
    if max_items and max_items > 0:
        out = out[:int(max_items)]
    return out

def _collect_new_movie_candidates(filtered: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for item in filtered:
        if str(item.get("kind") or "").lower() != "movie":
            continue
        if item.get("already_present"):
            continue
        out.append(item)
    out.sort(key=lambda x: float(x.get("score") or 0), reverse=True)
    return out


def _load_backlog_candidates(path_text: str | None) -> List[Dict[str, Any]]:
    if not path_text:
        return []

    path = Path(path_text)
    if not path.exists():
        return []

    rows = read_json(path, [])
    if not isinstance(rows, list):
        return []

    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("kind") or "movie").lower() != "movie":
            continue

        title = str(row.get("title") or row.get("query") or "").strip()
        if not title:
            continue

        year = row.get("year")
        query = f"{title} ({year})" if year else title

        out.append({
            "candidate_id": f"movie::{query}::0",
            "kind": "movie",
            "title": query,
            "season_number": None,
            "score": float(row.get("score") or 0),
            "reason": {
                "source": "movie_backlog",
                "bucket": "backlog",
                "genres": row.get("genres") or [],
            },
            "already_present": False,
            "decision": "auto_download",
        })

    return out


def _execute_movie_bucket(
    candidates: List[Dict[str, Any]],
    cfg: Dict[str, Any],
    bucket_name: str,
    target: int = 1,
    max_attempts: int = 5,
    exclude_titles: set[str] | None = None,
) -> Dict[str, Any]:
    exclude_titles = exclude_titles or set()
    executions: List[Dict[str, Any]] = []
    picked: List[Dict[str, Any]] = []
    attempts = 0

    for item in candidates:
        if len(picked) >= target or attempts >= max_attempts:
            break

        tkey = _movie_title_key(item.get("title"))
        if not tkey or tkey in exclude_titles:
            continue

        attempts += 1
        res = call_peppule_entrypoint(item, cfg)
        executions.append(res)
        exclude_titles.add(tkey)

        if _movie_exec_ok(res):
            picked.append(item)

    return {
        "bucket": bucket_name,
        "attempts": attempts,
        "success": len(picked),
        "picked": picked,
        "executions": executions,
    }

def call_peppule_entrypoint(item: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    base_ws = cfg.get("cheshire_ws_url", "").strip()
    api_key = cfg.get("cheshire_api_key", "").strip()

    if not base_ws:
        raise HTTPException(status_code=400, detail="cheshire_ws_url mancante in config")

    parsed = urlparse(base_ws)
    scheme = "https" if parsed.scheme == "wss" else "http"
    host = parsed.netloc or parsed.path
    if not host:
        raise HTTPException(status_code=400, detail="cheshire_ws_url non valido")

    base_http = f"{scheme}://{host}"

    kind = (item.get("kind") or "").strip().lower()
    title = (item.get("title") or "").strip()
    season_number = item.get("season_number")

    headers = {
        "Content-Type": "application/json",
        "user_id": "novita-agent",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    if kind == "movie":
        target = base_http + "/custom/peppule/movie"
        reason = item.get("reason") or {}
        movie_query = reason.get("fallback_probe_query") or title
        payload = {"query": movie_query}
    elif kind == "series":
        clean_title = normalize_peppule_series_title(title)
        reason = item.get("reason") or {}
        start_episode = reason.get("peppule_start_episode")
        max_episodes = reason.get("peppule_max_episodes")

        if not season_number:
            return {
                "status": "skipped",
                "error_type": "no_season",
                "status_code": 204,
                "target": None,
                "payload": {
                    "kind": "series",
                    "title": clean_title,
                    "season": None,
                },
                "error": "missing season_number",
            }

        target = base_http + "/custom/peppule/series"
        payload = {"title": clean_title, "season": int(season_number)}
        if start_episode is not None:
            payload["start_episode"] = int(start_episode)
        if max_episodes is not None:
            payload["max_episodes"] = int(max_episodes)
    else:
        target = base_http + "/custom/peppule/run"
        payload = {"kind": kind, "query": title}

    req = Request(
        target,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urlopen(req, timeout=600) as resp:
            body = resp.read().decode("utf-8")
            return {
                "status": "ok",
                "status_code": resp.status,
                "target": target,
                "payload": payload,
                "response": json.loads(body) if body else None,
            }
    except HTTPError as e:
        detail = e.read().decode("utf-8", errors="ignore") if hasattr(e, "read") else str(e)
        return {
            "status": "error",
            "error_type": "http",
            "status_code": e.code,
            "target": target,
            "payload": payload,
            "error": detail,
        }
    except URLError as e:
        return {
            "status": "error",
            "error_type": "url",
            "status_code": 502,
            "target": target,
            "payload": payload,
            "error": str(e.reason),
        }
    except TimeoutError:
        return {
            "status": "error",
            "error_type": "timeout",
            "status_code": 504,
            "target": target,
            "payload": payload,
            "error": "timeout",
        }
    except Exception as e:
        return {
            "status": "error",
            "error_type": "generic",
            "status_code": 500,
            "target": target,
            "payload": payload,
            "error": str(e),
        }




class ConfigFileRequest(BaseModel):
    config_file: str

class ConfigUpdateRequest(BaseModel):
    config_file: str
    updates: Dict[str, Any]

@app.post("/api/config/get")
def api_config_get(req: ConfigFileRequest) -> Dict[str, Any]:
    p = Path(req.config_file)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"config non trovato: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"lettura config fallita: {e}")
    return {"status": "ok", "config": data}

@app.post("/api/config/update")
def api_config_update(req: ConfigUpdateRequest) -> Dict[str, Any]:
    p = Path(req.config_file)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"config non trovato: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"lettura config fallita: {e}")

    for k, v in req.updates.items():
        data[k] = v

    try:
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"scrittura config fallita: {e}")

    return {"status": "ok", "config": data}

@app.post("/api/run-agent")
def run_agent(req: RunAgentRequest) -> Dict[str, Any]:
    if not AGENT_PATH.exists():
        raise HTTPException(status_code=500, detail=f"agent non trovato: {AGENT_PATH}")

    cfg = dict(DEFAULT_CONFIG)
    cfg.update(read_json(Path(req.config_file), {}))

    # 1) dry build of candidates/queue always via agent without execute
    cmd = [
        sys.executable,
        str(AGENT_PATH),
        "--config",
        req.config_file,
        "--views-file",
        req.views_file,
        "--catalog-file",
        req.catalog_file,
    ]

    try:
        result = subprocess.run(
            cmd,
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="run agent timeout")

    if result.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail={
                "message": "run agent fallito",
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
            },
        )

    parsed = None
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    if lines:
        try:
            parsed = json.loads(lines[-1])
        except Exception:
            parsed = {"last_line": lines[-1]}

    executions: List[Dict[str, Any]] = []
    execution_path = (cfg.get("execution_path") or "wrapper").strip().lower()

    if execution_path == "peppule":
        queue_path = OUT_DIR / "download_queue.json"
        queue = read_json(queue_path, [])
        filtered_path = OUT_DIR / "novita_filtrate.json"
        filtered = read_json(filtered_path, [])

        new_candidates = _collect_new_movie_candidates(filtered)
        backlog_candidates = _load_backlog_candidates(cfg.get("movie_backlog_file"))

        def _movie_key(x: dict) -> tuple[str, int]:
            title = str(x.get("title") or "").strip().lower()
            year = x.get("year") or 0
            try:
                year = int(year or 0)
            except Exception:
                year = 0
            return (title, year)

        merged_movies = []
        seen_movie_keys = set()

        for item in new_candidates + backlog_candidates:
            if str(item.get("kind") or "").lower() != "movie":
                continue
            k = _movie_key(item)
            if k in seen_movie_keys:
                continue
            seen_movie_keys.add(k)
            merged_movies.append(item)

        parsed = dict(parsed or {})
        parsed["new_candidates"] = new_candidates
        parsed["backlog_candidates"] = backlog_candidates
        parsed["merged_movie_candidates"] = merged_movies

    # 2) optional execute phase
    if req.execute:
        if execution_path == "peppule":
            queue_path = OUT_DIR / "download_queue.json"
            queue = read_json(queue_path, [])
            final_queue = list(queue)

            exec_hist_path = OUT_DIR / "executions.json"
            exec_hist = read_json(exec_hist_path, [])

            def _norm_exec_title(t: str) -> str:
                t = str(t or "").strip().lower()
                t = re.sub(r'\s*[-:]\s*s\d+\s*$', '', t)
                t = re.sub(r'\s+', ' ', t).strip()
                return t

            def _exec_item_key(x: dict) -> tuple[str, int]:
                payload = (x or {}).get("payload") or {}
                title = payload.get("title") or x.get("title") or ""
                season = payload.get("season") or x.get("season_number") or x.get("season") or 0
                try:
                    season = int(season or 0)
                except Exception:
                    season = 0
                return (_norm_exec_title(title), season)

            def _looks_already_on_jellyfin(x: dict) -> bool:
                if str((x or {}).get("status") or "") != "ok":
                    return False
                resp = (x or {}).get("response") or {}
                out = str(resp.get("output") or x.get("detail") or "").lower()
                return (
                    "già su jellyfin" in out
                    and "download ok 0" in out
                    and "falliti 0" in out
                    and "mancanti 0" in out
                )

            already_done = {
                _exec_item_key(x)
                for x in exec_hist
                if _looks_already_on_jellyfin(x)
            }

            filtered_queue = []
            for item in final_queue:
                item_key = (
                    _norm_exec_title(item.get("title") or ""),
                    int(item.get("season_number") or item.get("season") or 0),
                )
                if item.get("already_present"):
                    executions.append({
                        "status": "skipped",
                        "status_code": 200,
                        "target": "peppule",
                        "payload": {
                            "title": item.get("title"),
                            "season": item.get("season_number") or item.get("season"),
                        },
                        "detail": "skip execute: candidate già marcato come presente su Jellyfin",
                    })
                    continue
                if item_key in already_done:
                    executions.append({
                        "status": "skipped",
                        "status_code": 200,
                        "target": "peppule",
                        "payload": {
                            "title": item.get("title"),
                            "season": item.get("season_number") or item.get("season"),
                        },
                        "detail": "skip execute: già confermato su Jellyfin in executions.json",
                    })
                    continue
                filtered_queue.append(item)

            final_queue = filtered_queue

            for item in final_queue:
                item_key = (
                    _norm_exec_title(item.get("title") or ""),
                    int(item.get("season_number") or item.get("season") or 0),
                )

                if item_key in already_done:
                    executions.append({
                        "status": "skipped",
                        "status_code": 200,
                        "target": "peppule",
                        "payload": {
                            "title": item.get("title"),
                            "season": item.get("season_number") or item.get("season"),
                        },
                        "detail": "skip execute: già confermato su Jellyfin in executions.json",
                    })
                    continue

                res = call_peppule_entrypoint(item, cfg)
                executions.append(res)

                if _looks_already_on_jellyfin(res):
                    already_done.add(_exec_item_key(res))

        else:
            cmd_exec = list(cmd) + ["--execute"]
            try:
                result_exec = subprocess.run(
                    cmd_exec,
                    cwd=str(BASE_DIR),
                    capture_output=True,
                    text=True,
                    timeout=300,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                raise HTTPException(status_code=504, detail="run agent execute timeout")

            if result_exec.returncode != 0:
                raise HTTPException(
                    status_code=500,
                    detail={
                        "message": "run agent execute fallito",
                        "stdout": result_exec.stdout,
                        "stderr": result_exec.stderr,
                        "returncode": result_exec.returncode,
                    },
                )

            executions.append({
                "status": "ok",
                "path": "wrapper",
                "stdout": result_exec.stdout,
                "stderr": result_exec.stderr,
            })

    executions_path = OUT_DIR / "executions.json"
    existing_exec = read_json(executions_path, [])
    if not isinstance(existing_exec, list):
        existing_exec = []
    if executions:
        existing_exec.extend(executions)
        write_json(executions_path, existing_exec)

    return {
        "status": "ok",
        "command": cmd,
        "result": parsed,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "execution_path": (cfg.get("execution_path") or "wrapper"),
        "executions": executions,
    }


def _http_json(url: str, headers: Dict[str, str], timeout: int = 15) -> Dict[str, Any]:
    req = Request(url, headers=headers)
    with urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return {
            "status": resp.status,
            "headers": dict(resp.headers),
            "json": json.loads(body) if body else None,
        }


@app.post("/api/test/jellyfin")
def test_jellyfin(req: ConnectionTestRequest) -> Dict[str, Any]:
    url = req.url or get_config().get("jellyfin_url")
    token = req.token or get_config().get("jellyfin_token")
    if not url or not token:
        raise HTTPException(status_code=400, detail="manca jellyfin url o token")

    target = url.rstrip("/") + "/System/Info/Public"
    headers = {"X-Emby-Token": token}
    try:
        return {"status": "ok", "target": target, "response": _http_json(target, headers)}
    except HTTPError as e:
        raise HTTPException(status_code=e.code, detail=f"HTTP error: {e.reason}")
    except URLError as e:
        raise HTTPException(status_code=502, detail=f"URL error: {e.reason}")


@app.post("/api/test/tmdb")
def test_tmdb(req: ConnectionTestRequest) -> Dict[str, Any]:
    cfg = get_config()
    bearer = req.bearer or cfg.get("tmdb_bearer")
    api_key = req.api_key or cfg.get("tmdb_api_key")
    target = "https://api.themoviedb.org/3/genre/movie/list?language=it-IT"
    headers = {"accept": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    elif api_key:
        target += f"&api_key={api_key}"
    else:
        raise HTTPException(status_code=400, detail="manca tmdb bearer o api key")

    try:
        return {"status": "ok", "target": target, "response": _http_json(target, headers)}
    except HTTPError as e:
        raise HTTPException(status_code=e.code, detail=f"HTTP error: {e.reason}")
    except URLError as e:
        raise HTTPException(status_code=502, detail=f"URL error: {e.reason}")


@app.post("/api/test/wrapper")
def test_wrapper(req: ConnectionTestRequest) -> Dict[str, Any]:
    url = req.url or get_config().get("amule_api_url")
    api_key = req.api_key or get_config().get("amule_api_key")
    if not url or not api_key:
        raise HTTPException(status_code=400, detail="manca wrapper url o api key")

    target = url.rstrip("/") + "/docs"
    headers = {"X-API-Key": api_key}
    try:
        return {"status": "ok", "target": target, "response": _http_json(target, headers)}
    except Exception:
        # /docs is html, so fallback with openapi if present
        target = url.rstrip("/") + "/openapi.json"
        try:
            return {"status": "ok", "target": target, "response": _http_json(target, headers)}
        except HTTPError as e:
            raise HTTPException(status_code=e.code, detail=f"HTTP error: {e.reason}")
        except URLError as e:
            raise HTTPException(status_code=502, detail=f"URL error: {e.reason}")


@app.post("/api/test/cheshire")
def test_cheshire(req: ConnectionTestRequest) -> Dict[str, Any]:
    url = req.url or get_config().get("cheshire_ws_url")
    if not url:
        raise HTTPException(status_code=400, detail="manca cheshire ws url")
    return {
        "status": "ok",
        "target": url,
        "note": "test minimale: URL presente. Il test websocket reale va fatto nel client o con una route dedicata.",
    }


GUI_DIR = Path("${HOME}/jellyfin-novita-agent/gui")
GUI_FILE = GUI_DIR / "jellyfin_novita_gui_connected.jsx"

@app.get("/ui", response_class=HTMLResponse)
def ui_page():
    html = """<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Jellyfin Novità GUI</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script crossorigin src="https://unpkg.com/react@18/umd/react.development.js"></script>
  <script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.development.js"></script>
  <script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
</head>
<body class="bg-slate-50">
  <div id="root"></div>
  <script>
    window.API_BASE_DEFAULT = "";
  </script>
  <script type="text/babel" data-presets="react" src="/ui/code"></script>
</body>
</html>"""
    return HTMLResponse(html)

@app.get("/ui/code")
def ui_code():
    if not GUI_FILE.exists():
        raise HTTPException(status_code=404, detail=f"GUI file non trovato: {GUI_FILE}")
    return FileResponse(str(GUI_FILE), media_type="text/plain")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=9079)
