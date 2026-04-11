import requests
import re
import json
from difflib import SequenceMatcher
from .utils import log_console, normalize_string
from .settings import PeppuleSettings

def get_peppule_settings():
    try:
        from cat.looking_glass.cheshire_cat import CheshireCat
        cat = CheshireCat()
        plugin_name = "peppule"
        if plugin_name in cat.mad_hatter.plugins:
            settings_dict = cat.mad_hatter.plugins[plugin_name].load_settings()
            return PeppuleSettings(**settings_dict)
    except:
        pass
    return PeppuleSettings()

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
        log_console(f"📡 TMDb movie check: '{clean_title}'...")

        def norm(x):
            return re.sub(r"[^a-z0-9]+", " ", (x or "").lower()).strip()

        qnorm = norm(clean_title)
        qtokens = set(qnorm.split())

        r = requests.get(
            "https://api.themoviedb.org/3/search/movie",
            params={
                "query": clean_title,
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
            scored.append((exact, overlap, pop, release, it))

        scored.sort(reverse=True, key=lambda x: (x[0], x[1], x[2], x[3]))
        best = scored[0][4]

        official_title = best.get("title") or best.get("original_title") or clean_title
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

def _similar_title(a: str, b: str) -> bool:
    na = _norm_compare(a)
    nb = _norm_compare(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if na in nb or nb in na:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= 0.84

# --- WRAPPER & SCORING ---

def execute_search_amule(query):
    s = get_peppule_settings()
    base = s.amule_api_url.rstrip("/")

    VIDEO_EXT = (".mkv", ".mp4", ".avi", ".m4v", ".ts", ".mov", ".wmv")
    BAD_TOKENS = [
        ".torrent", " torrent", ".nfo", ".txt", ".srt", ".sub",
        ".idx", ".zip", ".rar", ".7z", "sample"
    ]
    BAD_QUALITY = ["cam", "hdcam", "ts", "tc", "telesync"]
    GOOD_TAGS_1080 = ["1080p", "fullhd", "bluray", "bdrip", "webrip", "web-dl", "webdl"]
    GOOD_TAGS_720 = ["720p"]

    try:
        res = requests.post(f"{base}/jobs", json={"query": query}, headers=_headers(), timeout=15).json()
        jid = res["id"]
        requests.post(f"{base}/jobs/{jid}/run", headers=_headers(), timeout=100)
        raw = requests.get(f"{base}/jobs/{jid}/results", headers=_headers(), timeout=20).json()

        results = []
        for it in raw:
            filename = it["filename"]
            name = filename.lower()

            # Scarta roba palesemente non video / inutile
            if any(tok in name for tok in BAD_TOKENS):
                continue

            # Tieni solo file con estensione video consentita
            if not name.endswith(VIDEO_EXT):
                continue

            score = it["sources"] * 10

            # Lingua
            if "ita" in name or "italian" in name:
                score += 500

            # Qualità: meglio 1080p, 720p va bene, 4K declassato
            if any(tag in name for tag in GOOD_TAGS_1080):
                score += 220
            elif any(tag in name for tag in GOOD_TAGS_720):
                score += 180

            if "2160p" in name or "4k" in name or "uhd" in name:
                score -= 400

            # Preferenze codec/container
            if name.endswith(".mkv"):
                score += 80
            elif name.endswith(".mp4"):
                score += 40
            elif name.endswith(".avi"):
                score -= 120

            if any(x in name for x in ["x264", "h264", "avc"]):
                score += 70
            if any(x in name for x in ["x265", "h265", "hevc"]):
                score += 40
            if "xvid" in name:
                score -= 140

            if "ac3" in name:
                score += 30
            if "aac" in name:
                score += 10

            # Qualità pessime / cinema
            if any(x in name for x in BAD_QUALITY):
                score -= 1000

            # Bonus episodico / stagione utile per serie
            if re.search(r'(?i)(^|[^0-9])s\d{1,2}e\d{1,2}([^0-9]|$)', name):
                score += 80
            if re.search(r'(?i)(^|[^0-9])\d{1,2}x\d{1,2}([^0-9]|$)', name):
                score += 80

            # Penalità file troppo piccoli
            size_mb = round(it["size_bytes"] / (1024 * 1024), 2)
            if size_mb < 250:
                score -= 250
            elif size_mb < 500:
                score -= 100

            results.append({
                "id": it["id"],
                "name": filename,
                "fonti": it["sources"],
                "size": f"{size_mb} MB",
                "score": score
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results
    except:
        return []


def execute_download_amule(rid):
    s = get_peppule_settings()
    try:
        r = requests.post(
            f"{s.amule_api_url.rstrip('/')}/download",
            json={"result_ids": [rid]},
            headers=_headers(),
            timeout=120
        ).json()
        return r["results"][0].get("status") == "ok"
    except:
        return False

# --- JELLYFIN ---
def check_jellyfin_movie(title_or_titles, year=""):
    s = get_peppule_settings()
    if not s.jellyfin_token:
        return False

    for title in _candidate_titles(title_or_titles):
        try:
            params = {
                "Recursive": "true",
                "IncludeItemTypes": "Movie",
                "SearchTerm": f"{title} {year}".strip(),
                "api_key": s.jellyfin_token
            }
            r = requests.get(f"{s.jellyfin_url.rstrip('/')}/Items", params=params, timeout=5).json()
            if r.get("TotalRecordCount", 0) > 0:
                log_console(f"🛡️ Jellyfin movie match: '{title}'")
                return True
        except:
            pass
    return False

def check_jellyfin_episode(title_or_titles, season, episode):
    s = get_peppule_settings()
    if not s.jellyfin_token:
        return False

    aliases = _candidate_titles(title_or_titles)

    # 1) tentativo diretto con SearchTerm
    for title in aliases:
        try:
            params = {
                "Recursive": "true",
                "IncludeItemTypes": "Episode",
                "SearchTerm": title,
                "ParentIndexNumber": season,
                "IndexNumber": episode,
                "api_key": s.jellyfin_token
            }
            r = requests.get(f"{s.jellyfin_url.rstrip('/')}/Items", params=params, timeout=5).json()
            if r.get("TotalRecordCount", 0) > 0:
                log_console(f"🛡️ Jellyfin episode match: '{title}' S{season}E{episode}")
                return True
        except:
            pass

    # 2) fallback senza SearchTerm + confronto fuzzy su SeriesName/Name
    try:
        params = {
            "Recursive": "true",
            "IncludeItemTypes": "Episode",
            "ParentIndexNumber": season,
            "IndexNumber": episode,
            "api_key": s.jellyfin_token
        }
        r = requests.get(f"{s.jellyfin_url.rstrip('/')}/Items", params=params, timeout=8).json()
        items = r.get("Items", []) or []
        for it in items:
            fields = [
                it.get("SeriesName", ""),
                it.get("Name", ""),
                it.get("SortName", ""),
                it.get("OriginalTitle", ""),
            ]
            for alias in aliases:
                if any(_similar_title(alias, f) for f in fields if f):
                    log_console(f"🛡️ Jellyfin fuzzy episode match: '{alias}' ~ '{it.get('SeriesName','')}' S{season}E{episode}")
                    return True
    except:
        pass

    return False
