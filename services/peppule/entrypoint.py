from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field

from cat.mad_hatter.decorators import endpoint
from cat.looking_glass.cheshire_cat import CheshireCat

from .pipelines import pipeline_movie, pipeline_series


class MoviePayload(BaseModel):
    query: str = Field(..., min_length=1)


class SeriesPayload(BaseModel):
    title: str = Field(..., min_length=1)
    season: int = Field(..., ge=1)
    start_episode: int = Field(default=1, ge=1)
    max_episodes: Optional[int] = Field(default=None, ge=1)


class RunPayload(BaseModel):
    kind: str = Field(..., pattern="^(movie|series)$")
    query: Optional[str] = None
    title: Optional[str] = None
    season: Optional[int] = Field(default=None, ge=1)
    start_episode: Optional[int] = Field(default=1, ge=1)
    max_episodes: Optional[int] = Field(default=None, ge=1)


def _cat_runtime():
    try:
        return CheshireCat()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Cheshire runtime unavailable: {e}")


@endpoint.get("/peppule/health")
def peppule_health():
    return {
        "status": "ok",
        "plugin": "peppule",
        "entrypoint": "machine-http",
        "routes": [
            "/custom/peppule/health",
            "/custom/peppule/movie",
            "/custom/peppule/series",
            "/custom/peppule/run",
        ],
    }


@endpoint.post("/peppule/movie")
def peppule_movie(payload: MoviePayload):
    try:
        cat = _cat_runtime()
        output = pipeline_movie(cat, payload.query.strip())
        return {
            "status": "ok",
            "kind": "movie",
            "query": payload.query.strip(),
            "output": output,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"movie pipeline failed: {e}")


@endpoint.post("/peppule/series")
def peppule_series(payload: SeriesPayload):
    try:
        cat = _cat_runtime()
        start_episode = int(getattr(payload, "start_episode", 1) or 1)
        max_episodes = int(getattr(payload, "max_episodes", 0) or 0) or None
        output = pipeline_series(
            cat,
            payload.title.strip(),
            int(payload.season),
            start_episode=start_episode,
            max_episodes=max_episodes,
        )
        return {
            "status": "ok",
            "kind": "series",
            "title": payload.title.strip(),
            "season": int(payload.season),
            "output": output,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"series pipeline failed: {e}")

@endpoint.post("/peppule/run")
def peppule_run(payload: RunPayload):
    kind = payload.kind.strip().lower()

    if kind == "movie":
        if not payload.query or not payload.query.strip():
            raise HTTPException(status_code=400, detail="movie richiede query")
        return peppule_movie(MoviePayload(query=payload.query.strip()))

    if kind == "series":
        if not payload.title or not payload.title.strip():
            raise HTTPException(status_code=400, detail="series richiede title")
        if not payload.season:
            raise HTTPException(status_code=400, detail="series richiede season")
        return peppule_series(
            SeriesPayload(
                title=payload.title.strip(),
                season=int(payload.season),
                start_episode=int(payload.start_episode or 1),
                max_episodes=payload.max_episodes,
            )
        )

    raise HTTPException(status_code=400, detail="kind non valido")
