import importlib.util
import json
import os
import re
import sys
import types

BASE = os.environ.get(
    "PEPPULE_BASE",
    "/home/sibilla-cumana/jellyfin-novita-agent/peppule_runtime",
)


def _load_modules():
    pkg = types.ModuleType("peppule_local")
    pkg.__path__ = [BASE]
    sys.modules["peppule_local"] = pkg
    for name in ("utils", "settings", "request_parser", "services", "pipelines"):
        path = f"{BASE}/{name}.py"
        spec = importlib.util.spec_from_file_location(f"peppule_local.{name}", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load Peppule module: {path}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"peppule_local.{name}"] = mod
        spec.loader.exec_module(mod)
    import peppule_local.services as services
    import peppule_local.pipelines as pipelines
    return services, pipelines


def _enable_execution(services, pipelines, *, movie=False, series=False):
    settings = services.get_peppule_settings()
    settings.auto_download_enabled = True
    if movie:
        settings.movie_auto_download_enabled = True
    if series:
        settings.series_auto_download_enabled = True
    services.get_peppule_settings = lambda: settings
    pipelines.get_peppule_settings = lambda: settings
    return settings


class DummyMovie:
    def __init__(self, title):
        self.title = title

    def llm(self, prompt: str) -> str:
        m = re.search(r"\b(19\d{2}|20\d{2})\b", self.title or "")
        year = m.group(1) if m else ""
        clean = re.sub(r"\b(19\d{2}|20\d{2})\b", "", self.title or "")
        clean = re.sub(r"\s+", " ", clean).strip()
        return json.dumps({"title": clean or self.title, "year": year})


class DummySeries:
    def __init__(self, title, episodes=12):
        self.title = title
        self.episodes = max(1, int(episodes or 12))

    def llm(self, prompt: str) -> str:
        return json.dumps({"title": self.title, "episodes": self.episodes})


def run_movie(title: str, *, language="ita", replacement=False) -> str:
    services, pipelines = _load_modules()
    _enable_execution(services, pipelines, movie=True)
    if replacement:
        pipelines.check_jellyfin_movie = lambda *args, **kwargs: False
    return pipelines.pipeline_movie(DummyMovie(title), title, language=language)


def run_series(title: str, season: int, *, start_episode=1, max_episodes=None, language="ita", replacement=False) -> str:
    services, pipelines = _load_modules()
    _enable_execution(services, pipelines, series=True)
    if replacement:
        services.check_jellyfin_episode = lambda *args, **kwargs: False
        pipelines.check_jellyfin_episode = lambda *args, **kwargs: False
    cap = int(max_episodes or os.getenv("PEPPULE_SERIES_MAX_EPISODES", "24") or "24")
    return pipelines.pipeline_series(
        DummySeries(title, cap), title, int(season),
        start_episode=int(start_episode or 1), max_episodes=cap, language=language,
    )


def run_all_seasons(title: str, *, language="ita", replacement=False) -> str:
    services, _ = _load_modules()
    official_title, seasons = services.get_tmdb_series_seasons(title)
    if not seasons:
        return f"📭 Nessuna stagione TMDb trovata per: {title}"
    max_seasons = int(os.getenv("PEPPULE_ALL_SEASONS_MAX", "20") or "20")
    seasons = seasons[:max_seasons]
    out = [f"📚 Serie: {official_title or title} — stagioni {seasons} — audio {language.upper()}"]
    for season in seasons:
        out.append(f"\n===== STAGIONE {season} =====")
        out.append(run_series(official_title or title, season, language=language, replacement=replacement))
    return "\n".join(out)


def plan_request(query: str) -> dict:
    services, _ = _load_modules()
    from peppule_local.request_parser import parse_media_request
    req = parse_media_request(query)
    out = {
        "kind": req.kind,
        "title": req.title,
        "language": req.language,
        "season": req.season,
        "all_seasons": req.all_seasons,
    }
    if req.kind == "series_all":
        official_title, seasons = services.get_tmdb_series_seasons(req.title)
        out["official_title"] = official_title
        out["seasons"] = seasons
    return out


def main(argv):
    if len(argv) >= 2 and argv[1] == "health":
        services, _ = _load_modules()
        settings = services.get_peppule_settings()
        print(json.dumps({
            "status": "ok",
            "runner": "local",
            "peppule_base": BASE,
            "jellyfin_url": settings.jellyfin_url,
            "amule_api_url": settings.amule_api_url,
            "jellyfin_check_enabled": bool(settings.jellyfin_check_enabled),
        }))
        return
    if len(argv) >= 3 and argv[1] == "plan":
        print(json.dumps(plan_request(argv[2]), ensure_ascii=False, sort_keys=True))
        return
    if len(argv) < 3:
        raise SystemExit("usage: local_peppule_runner.py MODE TITLE [SEASON] [START] [MAX] [LANGUAGE]")

    mode, title = argv[1], argv[2]
    if mode in {"movie", "replace-movie"}:
        # Meowgram historically dispatches every non-explicit-season request as movie.
        # Re-parse here so natural TV requests cannot reach the movie metadata path.
        _load_modules()
        from peppule_local.request_parser import parse_media_request
        req = parse_media_request(title)
        replacement = mode == "replace-movie"
        if req.kind == "series_all":
            print(run_all_seasons(req.title, language=req.language, replacement=replacement))
        elif req.kind == "series":
            print(run_series(req.title, req.season, language=req.language, replacement=replacement))
        else:
            print(run_movie(req.title, language=req.language, replacement=replacement))
    elif mode in {"series", "replace-series", "preview3"}:
        season = int(argv[3]) if len(argv) > 3 else 1
        start = int(argv[4]) if len(argv) > 4 else 1
        cap = int(argv[5]) if len(argv) > 5 and argv[5] else (3 if mode == "preview3" else None)
        language = argv[6] if len(argv) > 6 and argv[6] else "ita"
        print(run_series(title, season, start_episode=start, max_episodes=cap, language=language, replacement=mode == "replace-series"))
    else:
        raise SystemExit(f"unknown mode: {mode}")


if __name__ == "__main__":
    main(sys.argv)
