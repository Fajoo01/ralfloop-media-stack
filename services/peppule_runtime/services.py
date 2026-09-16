import requests
import re
import json
import sqlite3
import urllib.parse
import subprocess
import os
from pathlib import Path
from difflib import SequenceMatcher
from .utils import log_console, normalize_string
from .settings import PeppuleSettings



def _has_italian_audio_marker(name: str) -> bool:
    """
    True solo se il filename contiene un marker ITA reale.
    Evita falsi positivi tipo capITAna, rucATALA, ecc.
    """
    low = normalize_string(name or "").lower()
    tokens = re.findall(r"[a-z0-9]+", low)

    good = {
        "ita",
        "italian",
        "italiano",
        "italiana",
        "italiani",
        "italiane",
        "italianaudio",
        "audioita",
    }

    return any(t in good for t in tokens)


def _has_english_audio_marker(name: str) -> bool:
    """True only for explicit English-audio markers, not the word in a title."""
    low = normalize_string(name or "").lower()
    tokens = set(re.findall(r"[a-z0-9]+", low))
    if {"eng", "englishaudio", "audioeng"} & tokens:
        return True
    padded = " " + re.sub(r"[^a-z0-9]+", " ", low) + " "
    return bool(re.search(r"\baudio\s+english\b|\benglish\s+audio\b", padded))


def _has_requested_audio_marker(name: str, language: str = "ita") -> bool:
    lang = (language or "ita").strip().lower()
    if lang == "eng":
        return _has_english_audio_marker(name)
    return _has_italian_audio_marker(name)


def _subtitle_only_ita_release(name: str) -> bool:
    low = str(name or "").lower()
    tokens = set(re.findall(r"[a-z0-9]+", low))
    if "subbed" in tokens:
        return True
    if ("sub" in tokens or "subs" in tokens or "subita" in tokens) and ("ita" in tokens or "italian" in tokens):
        return not bool({"ac3", "aac", "eac3", "dts", "ddp", "dlmux", "webmux", "mux"} & tokens)
    return False


def _apply_novita_runtime_config(settings_or_dict):
    cfg_path = Path(os.environ.get(
        "PEPPULE_NOVITA_CONFIG",
        "/home/sibilla-cumana/jellyfin-novita-agent/config.json",
    ))
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    mapping = {
        "amule_api_url": cfg.get("amule_api_url"),
        "amule_api_key": cfg.get("amule_api_key"),
        "jellyfin_url": cfg.get("jellyfin_url"),
        "jellyfin_token": cfg.get("jellyfin_token"),
        "tmdb_api_key": cfg.get("tmdb_bearer") or cfg.get("tmdb_api_key"),
        "tmdb_language": cfg.get("tmdb_language"),
        "auto_download_enabled": cfg.get("auto_download_enabled", cfg.get("novita_auto_download_enabled")),
        "movie_auto_download_enabled": cfg.get("movie_auto_download_enabled", cfg.get("novita_movie_auto_download_enabled")),
        "series_auto_download_enabled": cfg.get("series_auto_download_enabled", cfg.get("novita_series_auto_download_enabled")),
    }
    for key, value in mapping.items():
        if value is None or value == "":
            continue
        if isinstance(settings_or_dict, dict):
            settings_or_dict[key] = value
        else:
            setattr(settings_or_dict, key, value)


def get_peppule_settings():
    """Load Peppule settings without Cheshire Cat or plugin-manager state."""
    settings = PeppuleSettings()
    settings.amule_api_url = os.environ.get("PEPPULE_AMULE_API_URL", settings.amule_api_url)
    settings.jellyfin_url = os.environ.get("PEPPULE_JELLYFIN_URL", settings.jellyfin_url)
    settings.jellyfin_check_enabled = True
    try:
        _apply_novita_runtime_config(settings)
    except Exception as exc:
        try:
            log_console(f"Novita settings unavailable: {exc}")
        except Exception:
            pass
    return settings


def _headers():
    s = get_peppule_settings()
    return {"X-API-Key": s.amule_api_key, "Content-Type": "application/json"}

def _tmdb_headers():
    s = get_peppule_settings()
    token = getattr(s, "tmdb_api_key", "") or ""
    if not token:
        return {}
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

def get_tmdb_series_info(title, season):
    try:
        s = get_peppule_settings()
        token = getattr(s, "tmdb_api_key", "") or ""
        if not token:
            return None, None

        language = getattr(s, "tmdb_language", "it-IT") or "it-IT"
        log_console(f"📡 TMDb check: '{title}' S{season}...")

        def norm(x):
            return re.sub(r"[^a-z0-9]+", " ", (x or "").lower()).strip()

        qnorm = norm(title)
        qtokens = set(qnorm.split())

        r = requests.get(
            "https://api.themoviedb.org/3/search/tv",
            params={
                "query": title,
                "language": language,
                "include_adult": "false",
            },
            headers=_tmdb_headers(),
            timeout=10,
        )
        data = r.json()
        results = data.get("results", []) or []
        if not results:
            return None, None

        scored = []
        for it in results[:10]:
            names = [it.get("name", ""), it.get("original_name", "")]
            nnorms = [norm(x) for x in names if x]
            exact = 1 if qnorm in nnorms else 0

            ctokens = set()
            for n in nnorms:
                ctokens |= set(n.split())

            overlap = len(qtokens & ctokens)

            # Automatic downloads must not let popularity turn a nearby title into
            # a different movie.  Single-token requests (for example "Room")
            # require an exact TMDb title/original-title match.  Short multi-token
            # titles require every token unless TMDb reports an exact title.
            if len(qtokens) == 1 and not exact:
                continue
            required_overlap = (
                len(qtokens) if len(qtokens) <= 3
                else max(2, len(qtokens) - 1)
            )
            if not exact and overlap < required_overlap:
                continue

            pop = float(it.get("popularity") or 0)
            first_air = it.get("first_air_date") or ""
            scored.append((exact, overlap, pop, first_air, it))

        scored.sort(reverse=True, key=lambda x: (x[0], x[1], x[2], x[3]))
        best = scored[0][4]

        show_id = best.get("id")
        official_title = best.get("name") or best.get("original_name") or title
        if not show_id:
            return None, None

        rs = requests.get(
            f"https://api.themoviedb.org/3/tv/{show_id}/season/{season}",
            params={"language": language},
            headers=_tmdb_headers(),
            timeout=10,
        )
        sdata = rs.json()
        episodes = sdata.get("episodes") or []
        count = len(episodes)

        log_console(f"📡 TMDb picked: id={show_id} title='{official_title}' season={season} episodes={count}")

        if count > 0:
            return official_title, count

        return official_title, None

    except Exception as e:
        log_console(f"⚠️ TMDb error: {e}")
        return None, None

def get_tmdb_series_seasons(title):
    """Return released numbered seasons for the best exact/near-exact TMDb TV match.

    Season 0 (specials) is intentionally excluded. This is metadata-only and does
    not trigger any download.
    """
    try:
        s = get_peppule_settings()
        token = getattr(s, "tmdb_api_key", "") or ""
        if not token:
            return None, []
        language = getattr(s, "tmdb_language", "it-IT") or "it-IT"

        def norm(x):
            return re.sub(r"[^a-z0-9]+", " ", (x or "").lower()).strip()

        qnorm = norm(title)
        qtokens = set(qnorm.split())
        r = requests.get(
            "https://api.themoviedb.org/3/search/tv",
            params={"query": title, "language": language, "include_adult": "false"},
            headers=_tmdb_headers(), timeout=10,
        )
        results = (r.json() or {}).get("results", []) or []
        scored = []
        for it in results[:10]:
            names = [it.get("name", ""), it.get("original_name", "")]
            nnorms = [norm(x) for x in names if x]
            exact = 1 if qnorm in nnorms else 0
            ctokens = set()
            for n in nnorms:
                ctokens |= set(n.split())
            overlap = len(qtokens & ctokens)
            if len(qtokens) == 1 and not exact:
                continue
            required_overlap = len(qtokens) if len(qtokens) <= 3 else max(2, len(qtokens) - 1)
            if not exact and overlap < required_overlap:
                continue
            scored.append((exact, overlap, float(it.get("popularity") or 0), it))
        if not scored:
            return None, []
        scored.sort(reverse=True, key=lambda x: (x[0], x[1], x[2]))
        best = scored[0][3]
        show_id = best.get("id")
        official_title = best.get("name") or best.get("original_name") or title
        if not show_id:
            return None, []
        detail = requests.get(
            f"https://api.themoviedb.org/3/tv/{show_id}",
            params={"language": language}, headers=_tmdb_headers(), timeout=10,
        ).json()
        seasons = []
        for row in detail.get("seasons", []) or []:
            try:
                number = int(row.get("season_number"))
                episodes = int(row.get("episode_count") or 0)
            except Exception:
                continue
            if number <= 0 or episodes <= 0:
                continue
            seasons.append(number)
        seasons = sorted(set(seasons))
        log_console(f"📺 TMDb series seasons: id={show_id} title='{official_title}' seasons={seasons}")
        return official_title, seasons
    except Exception as exc:
        log_console(f"⚠️ TMDb seasons error: {exc}")
        return None, []


# --- FONTE REALE: TVMAZE ---
def get_tvmaze_info(title, season):
    try:
        log_console(f" TVMaze check: '{title}' S{season}...")
        r = requests.get(f"https://api.tvmaze.com/singlesearch/shows?q={title}", timeout=10).json()
        official_title = r["name"]
        show_id = r["id"]
        eps = requests.get(
            f"https://api.tvmaze.com/shows/{show_id}/episodesbyseason?season={season}",
            timeout=10
        ).json()

        episode_titles = {}
        for row in eps:
            num = row.get("number")
            name = row.get("name") or ""
            if isinstance(num, int) and num > 0:
                episode_titles[num] = name

        return official_title, len(eps), episode_titles
    except:
        return None, None, {}

def _sanitize_media_query(q: str) -> str:
    q = (q or "").strip()
    q = re.sub(r'[`*_#>\[\]\(\)]', ' ', q)
    q = re.sub(r'\s+', ' ', q).strip()
    return q

def get_tmdb_movie_info(title):
    try:
        s = get_peppule_settings()
        token = getattr(s, "tmdb_api_key", "") or ""
        if not token:
            return None, ""

        language = getattr(s, "tmdb_language", "it-IT") or "it-IT"
        clean_title = _sanitize_media_query(title)
        year_match = re.search(r"\b(19\d{2}|20\d{2})\b", clean_title)
        requested_year = int(year_match.group(1)) if year_match else None
        search_title = re.sub(r"\b(19\d{2}|20\d{2})\b", " ", clean_title)
        search_title = re.sub(r"\s+", " ", search_title).strip() or clean_title
        log_console(f"📡 TMDb movie check: '{search_title}'...")

        def norm(x):
            return re.sub(r"[^a-z0-9]+", " ", (x or "").lower()).strip()

        qnorm = norm(search_title)
        qtokens = set(qnorm.split())

        r = requests.get(
            "https://api.themoviedb.org/3/search/movie",
            params={
                "query": search_title,
                "language": language,
                "include_adult": "false",
            },
            headers=_tmdb_headers(),
            timeout=10,
        )
        data = r.json()
        results = data.get("results", []) or []
        if not results:
            return None, ""

        scored = []
        for it in results[:10]:
            names = [it.get("title", ""), it.get("original_title", "")]
            nnorms = [norm(x) for x in names if x]
            exact = 1 if qnorm in nnorms else 0

            ctokens = set()
            for n in nnorms:
                ctokens |= set(n.split())

            overlap = len(qtokens & ctokens)
            pop = float(it.get("popularity") or 0)
            release = it.get("release_date") or ""
            try:
                release_year = int(release[:4])
            except Exception:
                release_year = None
            year_score = (
                2 if requested_year and release_year == requested_year
                else 1 if requested_year and release_year and abs(release_year - requested_year) <= 1
                else 0
            )
            scored.append((year_score, exact, overlap, pop, release, it))

        if not scored:
            log_console(f"⛔ TMDb movie identity not strong enough for {search_title!r}")
            return None, ""

        scored.sort(reverse=True, key=lambda x: (x[0], x[1], x[2], x[3], x[4]))
        best = scored[0][5]

        official_title = best.get("title") or best.get("original_title") or search_title
        release_date = best.get("release_date") or ""
        year = release_date[:4] if len(release_date) >= 4 else ""

        log_console(f"📡 TMDb movie picked: title='{official_title}' year='{year}'")
        return official_title, year

    except Exception as e:
        log_console(f"⚠️ TMDb movie error: {e}")
        return None, ""

# --- INTELLIGENZA LLM ---
def ask_llm_movie_info(cat, query):
    clean_query = _sanitize_media_query(query)

    # Prima TMDb
    title, year = get_tmdb_movie_info(clean_query)
    if title:
        return title, year

    # Fallback LLM minimo
    prompt = f"Analizza richiesta film: '{clean_query}'. JSON: {{\"title\": \"titolo corretto o uguale alla query se incerto\", \"year\": \"anno uscita oppure stringa vuota se incerto\"}}"
    try:
        res = cat.llm(prompt)
        data = json.loads(re.search(r"\{.*\}", res, re.DOTALL).group(0))
        title = (data.get("title") or clean_query).strip()
        year = (data.get("year") or "").strip()

        nq = normalize_string(clean_query).lower()
        nt = normalize_string(title).lower()
        if not nt or (nq not in nt and nt not in nq):
            title = clean_query

        if year and not re.fullmatch(r"\d{4}", year):
            year = ""

        return title, year
    except:
        return clean_query, ""

def ask_llm_series_info(cat, query, season):
    # Prima TMDb
    off_title, count = get_tmdb_series_info(query, season)
    if off_title and count:
        return off_title, count, {}

    # Poi TVMaze
    off_title, count, episode_titles = get_tvmaze_info(query, season)
    if off_title and count:
        return off_title, count, episode_titles

    # Fallback LLM molto vincolato
    prompt = (
        f"Analizza serie: '{query}' S{season}. "
        f"Rispondi SOLO con JSON nel formato "
        f'{{"title": "titolo ufficiale o uguale alla query se incerto", "episodes": numero o 0 se incerto}}. '
        f"NON inventare titoli alternativi, sottotitoli o numeri episodi non verificati."
    )

    try:
        res = cat.llm(prompt)
        data = json.loads(re.search(r'\{.*\}', res, re.DOTALL).group(0))

        title = (data.get("title") or query).strip()
        episodes = data.get("episodes", 0)

        nq = normalize_string(query).lower()
        nt = normalize_string(title).lower()
        if not nt or (nq not in nt and nt not in nq):
            title = query

        if not isinstance(episodes, int) or episodes <= 0 or episodes > 40:
            episodes = 12

        return title, episodes, {}
    except:
        return query, 12, {}

def _dedupe_keep_order(items):
    out = []
    seen = set()
    for x in items:
        k = (x or "").strip().lower()
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(x.strip())
    return out

def _swap_word(title: str, old: str, new: str):
    return re.sub(rf"(?i)\b{re.escape(old)}\b", new, title)

def _name_variants(title: str):
    base = (title or "").strip()
    if not base:
        return []

    out = [base, normalize_string(base)]

    # connettivi
    out.append(_swap_word(base, "et", "e"))
    out.append(_swap_word(base, "et", "and"))
    out.append(_swap_word(base, "e", "et"))
    out.append(_swap_word(base, "e", "and"))
    out.append(_swap_word(base, "and", "e"))
    out.append(_swap_word(base, "and", "et"))

    # caso reale Astrid: Raphael / Raphaelle / Raphaëlle
    swaps = [
        ("raphael", "raphaelle"),
        ("raphael", "raphaëlle"),
        ("raphaelle", "raphael"),
        ("raphaelle", "raphaëlle"),
        ("raphaëlle", "raphael"),
        ("raphaëlle", "raphaelle"),
    ]
    seed = list(out)
    for s in seed:
        for a, b in swaps:
            out.append(_swap_word(s, a, b))

    # anche su forme già normalizzate
    seed2 = [normalize_string(x) for x in out]
    out.extend(seed2)

    return _dedupe_keep_order(out)

def _candidate_titles(title_or_titles):
    if isinstance(title_or_titles, (list, tuple, set)):
        raw = list(title_or_titles)
    else:
        raw = [title_or_titles]

    out = []
    for t in raw:
        out.extend(_name_variants(str(t)))
    return _dedupe_keep_order(out)

def _norm_compare(s: str) -> str:
    s = normalize_string(s or "")
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _movie_chapter_marker(norm_title: str) -> str:
    tokens = [t for t in (norm_title or "").split() if not re.fullmatch(r"(19|20)\d{2}", t)]
    if not tokens:
        return ""
    roman = {"ii": "2", "iii": "3"}
    last = tokens[-1]
    if last in {"2", "3"}:
        return last
    if last in roman:
        return roman[last]
    if len(tokens) >= 2 and tokens[-2] in {"parte", "part", "chapter", "capitolo"}:
        if last in {"2", "3"}:
            return last
        if last in roman:
            return roman[last]
    return ""

def _similar_title(a: str, b: str) -> bool:
    na = _norm_compare(a)
    nb = _norm_compare(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    chapter_a = _movie_chapter_marker(na)
    chapter_b = _movie_chapter_marker(nb)
    if chapter_a != chapter_b:
        return False
    if na in nb or nb in na:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= 0.84

# --- WRAPPER & SCORING ---


def execute_search_amule(query, preferred_language="ita"):
    s = get_peppule_settings()
    base = s.amule_api_url.rstrip("/")

    VIDEO_EXT = (".mkv", ".mp4", ".avi", ".m4v")
    BAD_TOKENS = [
        ".torrent", " torrent", ".nfo", ".txt", ".srt", ".sub", ".idx",
        ".zip", ".rar", ".7z", ".pdf", ".jpg", ".jpeg", ".png", ".gif",
        ".mp3", ".flac", ".epub", ".mobi", ".cbr", ".cbz",
        "sample", "trailer", "teaser",
        "spycam", "preteen", "lolita", "pedo", "nude", "nacked", "sexy",
        "fyeo", "wife minx", "amatrice ragazza", "ragazza geil",
    ]
    BAD_QUALITY = ["cam", "hdcam", "ts", "tc", "telesync", "telecine", "dvdscr", "screener"]
    GOOD_TAGS_1080 = ["1080p", "fullhd", "bluray", "bdrip", "webrip", "web-dl", "webdl"]
    GOOD_TAGS_720 = ["720p"]
    GOOD_AUDIO = ["ac3", "aac", "ddp", "dts"]
    GOOD_CODEC = ["x264", "h264", "avc", "x265", "h265", "hevc"]

    try:
        import time

        res = requests.post(
            f"{base}/jobs",
            json={"query": query},
            headers=_headers(),
              timeout=5,
        ).json()
        jid = res["id"]

        search_timeout = int(os.getenv("PEPPULE_AMULE_SEARCH_TIMEOUT", "75") or "75")
        run = requests.post(
            f"{base}/jobs/{jid}/run",
            headers=_headers(),
              timeout=search_timeout,
        ).json()

        raw = []
        for delay in (0.0, 1.0):
            if delay > 0:
                time.sleep(delay)
            try:
                cur = requests.get(
                    f"{base}/jobs/{jid}/results",
                    headers=_headers(),
                      timeout=4,
                ).json()
            except Exception:
                cur = []
            if cur:
                raw = cur
                break

        results = []
        for it in raw:
            filename = it.get("filename", "") or ""
            name = filename.lower()
            result_hash = it.get("hash", "") or ""
            network = it.get("network") or run.get("network")

            if any(tok in name for tok in BAD_TOKENS):
                continue

            if not name.endswith(VIDEO_EXT):
                continue

            if (preferred_language or "ita").lower() == "ita" and _subtitle_only_ita_release(filename):
                continue

            if any(x in name for x in BAD_QUALITY):
                continue

            size_bytes = int(it.get("size_bytes") or 0)
            size_mb = round(size_bytes / (1024 * 1024), 2) if size_bytes > 0 else 0

            # Filtri dimensione: evita fake minuscoli e mostri ingestibili.
            if size_mb and size_mb < 250:
                continue
            if size_mb and size_mb > 14000:
                continue

            sources = int(it.get("sources") or 0)
            score = sources * 12

            if _has_requested_audio_marker(filename, preferred_language):
                score += 520
            elif (preferred_language or "ita").lower() == "eng" and _has_italian_audio_marker(filename):
                score -= 180
            if (preferred_language or "ita").lower() == "ita" and ("sub ita" in name or "subs ita" in name):
                score += 80

            if any(tag in name for tag in GOOD_TAGS_1080):
                score += 240
            elif any(tag in name for tag in GOOD_TAGS_720):
                score += 180

            if "2160p" in name or "4k" in name or "uhd" in name:
                score -= 420

            if name.endswith(".mkv"):
                score += 100
            elif name.endswith(".mp4") or name.endswith(".m4v"):
                score += 55
            elif name.endswith(".avi"):
                score -= 140

            if any(x in name for x in GOOD_CODEC):
                score += 70
            if "xvid" in name or "divx" in name:
                score -= 120

            if any(x in name for x in GOOD_AUDIO):
                score += 35

            # Preferenza dimensione film ragionevole.
            if size_mb:
                if 1200 <= size_mb <= 4500:
                    score += 140
                elif 700 <= size_mb < 1200:
                    score += 60
                elif 4500 < size_mb <= 8000:
                    score -= 80
                elif size_mb > 8000:
                    score -= min(800, 120 + int((size_mb - 8000) / 250))

            try:
                size_bytes = int(it.get("size_bytes") or it.get("size_bytes_int") or 0)
            except Exception:
                size_bytes = 0

            results.append({
                "id": it["id"],
                "hash": result_hash,
                "network": network,
                "name": filename,
                "size": f"{size_mb} MB" if size_mb else it.get("size", 0),
                "size_mb": size_mb,
                "size_bytes": size_bytes,
                "sources": sources,
                "fonti": sources,
                "score": score,
            })

        return sorted(results, key=lambda x: x["score"], reverse=True)

    except Exception as e:
        log_console(f"⚠️ Errore ricerca aMule: {e}")
        return []



def _amule_int_size_bytes(selected_result):
    if not isinstance(selected_result, dict):
        return 0
    for k in ("size_bytes", "ed2k_size", "bytes"):
        try:
            v = int(selected_result.get(k) or 0)
            if v > 0:
                return v
        except Exception:
            pass
    try:
        mb = float(selected_result.get("size_mb") or 0)
        if mb > 0:
            return int(mb * 1000 * 1000)
    except Exception:
        pass
    return 0


def save_amule_download_record(selected_result=None, context=None, dl_response=None, status=None):
    """
    Salva l'hash ed2k/aMule al momento del download.
    Retrocompatibile: se selected_result manca o non ha hash, non fa nulla.
    """
    if not isinstance(selected_result, dict):
        return

    h = selected_result.get("hash") or selected_result.get("ed2k_hash")
    name = selected_result.get("name") or selected_result.get("filename") or selected_result.get("ed2k_name")
    if not h or not name:
        return

    size_bytes = _amule_int_size_bytes(selected_result)
    sources = selected_result.get("sources", selected_result.get("fonti"))
    score = selected_result.get("score")
    rid = selected_result.get("id")

    try:
        sources = int(sources or 0)
    except Exception:
        sources = 0

    try:
        score = float(score or 0)
    except Exception:
        score = 0.0

    context = context or {}
    query = context.get("query") or selected_result.get("query")
    kind = context.get("kind")
    title = context.get("title")
    season = context.get("season")
    episode = context.get("episode")

    try:
        season = int(season) if season not in (None, "") else None
    except Exception:
        season = None

    try:
        episode = int(episode) if episode not in (None, "") else None
    except Exception:
        episode = None

    if size_bytes > 0:
        safe_name = urllib.parse.quote(str(name))
        ed2k_link = f"ed2k://|file|{safe_name}|{size_bytes}|{h}|/"
    else:
        ed2k_link = None

    db = Path("/home/sibilla-cumana/Dati/tomb/db/tomb.sqlite")
    try:
        # Tomb history is auxiliary. A permissions/configuration problem here
        # must never turn an already submitted aMule request into a failure.
        db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(db)
        con.execute("""
        CREATE TABLE IF NOT EXISTS amule_downloads (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          kind TEXT,
          title TEXT,
          season INTEGER,
          episode INTEGER,
          query TEXT,
          result_id TEXT,
          ed2k_hash TEXT,
          ed2k_name TEXT,
          ed2k_size INTEGER,
          ed2k_link TEXT,
          sources INTEGER,
          score REAL,
          download_status TEXT,
          matched_local_path TEXT,
          notes TEXT
        )
        """)
        con.execute("""
        CREATE INDEX IF NOT EXISTS idx_amule_downloads_hash
        ON amule_downloads(ed2k_hash)
        """)
        con.execute("""
        CREATE INDEX IF NOT EXISTS idx_amule_downloads_name_size
        ON amule_downloads(ed2k_name, ed2k_size)
        """)
        con.execute("""
        INSERT INTO amule_downloads (
          kind,title,season,episode,query,
          result_id,ed2k_hash,ed2k_name,ed2k_size,ed2k_link,
          sources,score,download_status,notes
        )
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            kind, title, season, episode, query,
            str(rid) if rid is not None else None,
            str(h), str(name), int(size_bytes or 0), ed2k_link,
            sources, score, status,
            "saved_by_peppule_execute_download_amule"
        ))
        con.commit()
        con.close()
        log_console(f"💾 Salvato hash aMule nel DB Tomb: {name} [{h}]")
    except Exception as e:
        log_console(f"⚠️ Impossibile salvare hash aMule nel DB Tomb: {e}")



def execute_download_amule(rid, selected_result=None, context=None):
    s = get_peppule_settings()
    try:
        r = requests.post(
            f"{s.amule_api_url.rstrip('/')}/download",
            json={"result_ids": [rid]},
            headers=_headers(),
            timeout=60,
        ).json()

        item = (r.get("results") or [{}])[0]
        status = item.get("status")
        already_have = bool(item.get("already_have"))

        ok = (
            status == "ok"
            or status == "already_have"
            or already_have
            or bool(item.get("ed2k_fallback_ok"))
            or bool(item.get("queue_verified_ec"))
        )

        save_amule_download_record(
            selected_result=selected_result,
            context=context,
            dl_response=r,
            status=status or ("ok" if ok else "unknown"),
        )

        if ok:
            return {
                "ok": True,
                "status": status,
                "already_have": already_have or status == "already_have",
                "item": item,
                "response": r,
            }

        log_console(f"⚠️ Download aMule fallito: rid={rid} response={r}")
        return {
            "ok": False,
            "status": status or "unknown",
            "already_have": False,
            "item": item,
            "response": r,
        }

    except Exception as e:
        log_console(f"⚠️ Errore download aMule: {e}")
        return {
            "ok": False,
            "status": "error",
            "already_have": False,
            "error": str(e),
        }


def get_amule_queue():
    s = get_peppule_settings()
    try:
        return requests.get(
            f"{s.amule_api_url.rstrip('/')}/ec/queue",
            headers=_headers(),
            timeout=20,
        ).json()
    except Exception as e:
        return {"ok": False, "error": str(e)}

def is_jellyfin_check_enabled():
    try:
        return bool(getattr(get_peppule_settings(), "jellyfin_check_enabled", True))
    except Exception:
        return True


def _mark_bad_media(path_text: str, reason: str, detail: str = "") -> None:
    try:
        db = Path("/home/sibilla-cumana/jellyfin-novita-agent/data/bad_media.sqlite")
        db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(db)
        con.execute("""
        CREATE TABLE IF NOT EXISTS bad_media_probe (
          path TEXT PRIMARY KEY,
          detected_at TEXT DEFAULT CURRENT_TIMESTAMP,
          reason TEXT,
          detail TEXT
        )
        """)
        con.execute(
            "INSERT OR REPLACE INTO bad_media_probe(path, detected_at, reason, detail) VALUES (?, CURRENT_TIMESTAMP, ?, ?)",
            (str(path_text or ""), str(reason or ""), str(detail or "")[:4000]),
        )
        con.commit()
        con.close()
    except Exception:
        pass


def _quick_local_media_ok(path_text: str) -> bool:
    if not path_text:
        return True
    p = Path(str(path_text))
    if not p.exists() or not p.is_file():
        log_console(f"⚠️ Jellyfin path non leggibile: {p}")
        _mark_bad_media(str(p), "missing_or_unreadable", "")
        return False
    try:
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration:stream=codec_type,codec_name",
                "-of", "json", str(p),
            ],
            capture_output=True, text=True, timeout=20,
        )
        if probe.returncode != 0:
            detail = (probe.stderr or '')[:4000]
            log_console(f"⚠️ Jellyfin media ffprobe fallito: {p.name}: {detail[:240]}")
            _mark_bad_media(str(p), "ffprobe_failed", detail)
            return False
        data = json.loads(probe.stdout or "{}")
        streams = data.get("streams") or []
        if not any(x.get("codec_type") == "video" for x in streams):
            _mark_bad_media(str(p), "missing_video_stream", json.dumps(data, ensure_ascii=False)[:4000])
            return False
        if not any(x.get("codec_type") == "audio" for x in streams):
            _mark_bad_media(str(p), "missing_audio_stream", json.dumps(data, ensure_ascii=False)[:4000])
            return False
        try:
            duration = float((data.get("format") or {}).get("duration") or 0)
        except Exception:
            duration = 0.0
        starts = [int(max(0, duration * 0.50))] if duration > 60 else [0]
        if duration > 120:
            starts.append(int(max(0, duration - 30)))
        for start in starts:
            dec = subprocess.run(
                ["ffmpeg", "-v", "error", "-ss", str(start), "-i", str(p), "-t", "4", "-map", "0:v:0", "-f", "null", "-"],
                capture_output=True, text=True, timeout=18,
            )
            if dec.returncode != 0:
                detail = (dec.stderr or '')[:4000]
                log_console(f"⚠️ Jellyfin media decode fallito: {p.name}: {detail[:240]}")
                _mark_bad_media(str(p), f"decode_failed_at_{start}", detail)
                return False
        return True
    except Exception as e:
        log_console(f"⚠️ Jellyfin media verify errore: {p.name}: {e}")
        _mark_bad_media(str(p), "verify_exception", str(e))
        return False


TOMB_DB = Path(os.environ.get("PEPPULE_TOMB_DB", "/home/sibilla-cumana/Dati/tomb/db/tomb.sqlite"))


def _localize_jellyfin_path(path_text: str) -> Path:
    p = Path(str(path_text or ""))
    if str(p).startswith("/media/"):
        return Path("/mnt/media_cachefirst") / p.relative_to("/media")
    return p


def _valid_tomb_sidecar(path_text: str) -> bool:
    if not path_text or not str(path_text).lower().endswith(".tomb.strm"):
        return False
    sidecar = _localize_jellyfin_path(path_text)
    if not sidecar.is_file():
        return False
    try:
        lines = [x.strip() for x in sidecar.read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()]
        if len(lines) != 1:
            return False
        target = urllib.parse.urlparse(lines[0])
        if target.scheme not in {"http", "https"} or not target.netloc:
            return False
        if not TOMB_DB.is_file():
            return False
        con = sqlite3.connect(TOMB_DB)
        try:
            row = con.execute(
                "SELECT state,cold_size,cold_path,archive_path,restored_file FROM tomb_items WHERE strm_path=? LIMIT 1",
                (str(sidecar),),
            ).fetchone()
        finally:
            con.close()
        if not row or row[0] not in {"archived_lossless", "restored"} or int(row[1] or 0) <= 0:
            return False
        # The logical media must still have at least one recoverable backing object.
        return any(v and Path(v).exists() for v in row[2:])
    except Exception as exc:
        log_console(f"⚠️ Tomb sidecar non validato: {sidecar}: {exc}")
        return False


def _jellyfin_item_has_audio_language(it: dict, language: str) -> bool:
    lang = (language or "").strip().lower()
    aliases = {
        "eng": {"eng", "en", "english"},
        "ita": {"ita", "it", "italian", "italiano"},
    }.get(lang, {lang})
    streams = list((it or {}).get("MediaStreams") or [])
    for src in (it or {}).get("MediaSources") or []:
        streams.extend(src.get("MediaStreams") or [])
    for st in streams:
        st_type = str(st.get("Type") or st.get("type") or "").lower()
        if st_type and st_type != "audio":
            continue
        value = str(st.get("Language") or st.get("language") or "").lower().strip()
        title = str(st.get("DisplayTitle") or st.get("Title") or "").lower()
        if value in aliases or any(a in title.split() for a in aliases):
            return True
    return False


def _jellyfin_item_playable(it: dict) -> bool:
    path = (it or {}).get("Path")
    if path and str(path).lower().endswith(".tomb.strm"):
        if _valid_tomb_sidecar(path):
            log_console(f"🪦 Tomb valido: {path}")
            return True
        return False
    return _quick_local_media_ok(path)


# --- JELLYFIN ---
def check_jellyfin_movie(title_or_titles, year="", language="ita"):
    if not is_jellyfin_check_enabled():
        return False
    s = get_peppule_settings()
    if not s.jellyfin_token:
        return False

    headers = {"X-Emby-Token": s.jellyfin_token}
    base = s.jellyfin_url.rstrip("/")

    wanted_year = None
    try:
        wanted_year = int(year) if year not in (None, "", 0) else None
    except Exception:
        wanted_year = None

    aliases = _candidate_titles(title_or_titles)

    for title in aliases:
        title = str(title or "").strip()
        if not title:
            continue

        # Jellyfin spesso NON trova "Titolo Anno".
        # Cerca solo il titolo e poi confronta ProductionYear.
        search_terms = [title]
        if wanted_year:
            search_terms.append(f"{title} {wanted_year}")  # fallback, ma non il percorso principale

        seen_ids = set()

        for term in search_terms:
            try:
                params = {
                    "Recursive": "true",
                    "IncludeItemTypes": "Movie",
                    "SearchTerm": term,
                    "Limit": 20,
                    "Fields": "Path,MediaSources,MediaStreams",
                }
                r = requests.get(
                    f"{base}/Items",
                    params=params,
                    headers=headers,
                    timeout=8,
                ).json()

                for it in r.get("Items", []) or []:
                    iid = it.get("Id")
                    if iid in seen_ids:
                        continue
                    seen_ids.add(iid)

                    name = it.get("Name", "") or ""
                    prod_year = it.get("ProductionYear")

                    if not _similar_title(title, name):
                        continue

                    if wanted_year:
                        try:
                            py = int(prod_year) if prod_year not in (None, "") else None
                        except Exception:
                            py = None

                        # Accetta anno esatto o differenza di 1 anno per casi festival/uscita italiana.
                        if py is not None and abs(py - wanted_year) <= 1:
                            if _jellyfin_item_playable(it):
                                if (language or "ita").lower() != "ita" and not _jellyfin_item_has_audio_language(it, language):
                                    continue
                                log_console(f"🛡️ Jellyfin movie match playable: '{name}' ({py})")
                                return True
                            log_console(f"⚠️ Jellyfin movie presente ma non validato: '{name}' ({py})")
                            continue

                        continue

                    if _jellyfin_item_playable(it):
                        if (language or "ita").lower() != "ita" and not _jellyfin_item_has_audio_language(it, language):
                            continue
                        log_console(f"🛡️ Jellyfin movie match playable: '{name}'")
                        return True
                    log_console(f"⚠️ Jellyfin movie presente ma non validato: '{name}'")
                    continue

            except Exception:
                pass

    return False

def check_jellyfin_episode(title_or_titles, season, episode, language="ita"):
    if not is_jellyfin_check_enabled():
        return False
    s = get_peppule_settings()
    if not s.jellyfin_token:
        return False

    aliases = _candidate_titles(title_or_titles)
    headers = {"X-Emby-Token": s.jellyfin_token}
    base = s.jellyfin_url.rstrip("/")

    # 1) trova la serie migliore
    series_candidates = []
    seen_ids = set()

    for title in aliases:
        try:
            r = requests.get(
                f"{base}/Items",
                params={
                    "Recursive": "true",
                    "IncludeItemTypes": "Series",
                    "SearchTerm": title,
                    "Limit": 20,
                },
                headers=headers,
                timeout=8,
            ).json()

            for it in r.get("Items", []) or []:
                sid = it.get("Id")
                name = it.get("Name", "") or ""
                if not sid:
                    continue
                if sid in seen_ids:
                    continue
                if _similar_title(title, name) or any(_similar_title(a, name) for a in aliases):
                    seen_ids.add(sid)
                    series_candidates.append(it)
        except Exception:
            pass

    if not series_candidates:
        return False

    # 2) controlla gli episodi della stagione della/e serie trovata/e
    for series in series_candidates:
        sid = series.get("Id")
        if not sid:
            continue
        try:
            r = requests.get(
                f"{base}/Shows/{sid}/Episodes",
                params={"Season": season, "Fields": "Path,MediaSources,MediaStreams"},
                headers=headers,
                timeout=8,
            ).json()

            for it in r.get("Items", []) or []:
                try:
                    ep_num = int(it.get("IndexNumber") or 0)
                except Exception:
                    ep_num = 0
                if ep_num == int(episode):
                    if _jellyfin_item_playable(it):
                        if (language or "ita").lower() != "ita" and not _jellyfin_item_has_audio_language(it, language):
                            log_console(f"ℹ️ Jellyfin episodio presente ma senza audio {language}: '{series.get('Name','')}' S{season}E{episode}")
                            continue
                        log_console(f" Jellyfin series-id episode playable match: '{series.get('Name','')}' S{season}E{episode}")
                        return True
                    log_console(f"⚠️ Jellyfin episode presente ma non validato: '{series.get('Name','')}' S{season}E{episode}")
                    continue
        except Exception:
            pass

    return False
