# pipelines.py
"""
Peppule pipelines

This module exposes the functions imported by main_plugin.py:
- pipeline_movie(cat, query)
- pipeline_series(cat, title, season)

Key features (series):
- Dual season search: "Sxx" and "x" formats (e.g. S02 and 2x)
- Builds a per-episode ranked list of candidates (by score) and stores it in cache
- Downloads the best candidate per episode (skipping episodes already in Jellyfin)
- Falls back to per-episode dual search only when needed
"""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Set, Any

from .services import (
    ask_llm_movie_info,
    ask_llm_series_info,
    execute_search_amule,
    execute_download_amule,
    get_peppule_settings,
    check_jellyfin_movie,
    check_jellyfin_episode,
)
from .utils import normalize_string, log_console, write_cache, movie_search_variants


# ---------- helpers ----------

_PACK_HINTS = (
    "pack",
    "complete",
    "stagione completa",
    "season complete",
    "complete season",
    "full season",
    "all episodes",
)

def _dedupe_by_id(results: List[dict]) -> List[dict]:
    seen = set()
    out = []
    for r in results or []:
        rid = r.get("id")
        if rid in seen:
            continue
        seen.add(rid)
        out.append(r)
    return out

def _dedupe_by_name(results: List[dict]) -> List[dict]:
    best_by_name = {}
    for r in results or []:
        name = (r.get("name") or r.get("filename") or r.get("file") or "").strip().lower()
        if not name:
            continue
        prev = best_by_name.get(name)
        prev_score = prev.get("score", 0) if prev else 0
        cur_score = r.get("score", 0)
        if prev is None or cur_score > prev_score:
            best_by_name[name] = r
    return list(best_by_name.values())

def _merge_and_sort(*lists: List[dict]) -> List[dict]:
    merged: List[dict] = []
    for lst in lists:
        if lst:
            merged.extend(lst)
    merged = _dedupe_by_id(merged)
    merged = _dedupe_by_name(merged)
    merged.sort(key=lambda x: x.get("score", 0), reverse=True)
    return merged

def _extract_episodes_from_name(name: str, season: int) -> Set[int]:
    """
    Extract episode numbers from a filename, supporting:
      - S02E03 (multiple occurrences)
      - 2x03 (multiple occurrences)
      - ranges like S02E01-12, S02E01-E12, 2x01-12
    """
    s = (name or "").lower()
    eps: Set[int] = set()

    # SxxEyy occurrences (supports multiple E tokens)
    for ss, ee in re.findall(r"s(\d{1,2})e(\d{1,2})", s):
        if int(ss) == int(season):
            eps.add(int(ee))

    # xxXyy occurrences
    for ss, ee in re.findall(r"(\d{1,2})x(\d{1,2})", s):
        if int(ss) == int(season):
            eps.add(int(ee))

    # Ranges: S02E01-12 or S02E01-E12
    m = re.search(r"s(\d{1,2})e(\d{1,2})\s*[-_]\s*e?(\d{1,2})", s)
    if m and int(m.group(1)) == int(season):
        a, b = int(m.group(2)), int(m.group(3))
        for e in range(min(a, b), max(a, b) + 1):
            eps.add(e)

    # Ranges: 2x01-12
    m = re.search(r"(\d{1,2})x(\d{1,2})\s*[-_]\s*(\d{1,2})", s)
    if m and int(m.group(1)) == int(season):
        a, b = int(m.group(2)), int(m.group(3))
        for e in range(min(a, b), max(a, b) + 1):
            eps.add(e)


    # Pattern generico fiction/TV vecchie:
    # "[Titolo 1] 1.01. Nome episodio"
    # "Titolo 1.03 Nome episodio"
    # Non è hardcode: usa stagione richiesta + numero episodio.
    try:
        season_i = int(season or 1)
    except Exception:
        season_i = 1

    for m in re.finditer(rf"(?<!\d){season_i}[\.\-_\s]+(\d{{1,2}})(?!\d)", name, flags=re.I):
        try:
            ep = int(m.group(1))
            if 1 <= ep <= 99:
                eps.add(ep)
        except Exception:
            pass

    return eps



def _anchor_tokens_from_name(name: str) -> Set[str]:
    bad = {
        "ita","eng","sub","subs","ac3","aac","ddp5","ddp51","webrip","webdl","web","bdrip",
        "bluray","x264","x265","h264","h265","hdr","repack","proper","nf","amzn","atvp",
        "mkv","mp4","avi","720p","1080p","2160p","fullhd","uhd","rip","italian",
        "season","stagione","episode","episodio","series","serie","strike"
    }
    toks = []
    for t in re.findall(r"[a-z0-9]+", (name or "").lower()):
        if t in bad:
            continue
        if len(t) <= 2:
            continue
        if re.fullmatch(r"(19\d{2}|20\d{2})", t):
            continue
        if re.fullmatch(r"s\d{1,2}e\d{1,2}", t):
            continue
        if re.fullmatch(r"\d{1,2}x\d{1,2}", t):
            continue
        toks.append(t)
    return set(toks)

def _candidate_matches_anchor(name: str, anchor_tokens: Set[str]) -> bool:
    if not anchor_tokens:
        return True
    cand = _anchor_tokens_from_name(name)
    overlap = len(cand & anchor_tokens)
    need = 1 if len(anchor_tokens) == 1 else 2
    return overlap >= min(need, len(anchor_tokens))



def _strict_series_title_ok(name: str, titles: List[str]) -> bool:
    s = (name or "").lower()
    clean_titles = [t.strip().lower() for t in titles if (t or "").strip()]
    if not clean_titles:
        return True

    alias_map = {
        "strike": [["cormoran", "strike"], ["detective", "cormoran", "strike"]],
    }

    for t in clean_titles:
        toks = [x for x in re.findall(r"[a-z0-9]+", t) if len(x) > 2]
        if not toks:
            continue

        if t in alias_map:
            for alt in alias_map[t]:
                if all(tok in s for tok in alt):
                    return True
            # titolo ambiguo con alias noti: se non matcha l'alias, boccia subito
            continue

        if all(tok in s for tok in toks):
            return True

    return False

def _looks_like_season_pack(name: str, season: int) -> bool:
    s = (name or "").lower()
    # must reference the season somehow
    has_season_marker = (f"s{season:02d}" in s) or (f"{season}x" in s)
    if not has_season_marker:
        return False
    return any(h in s for h in _PACK_HINTS)


def _series_title_match_ok(title: str, name: str) -> bool:
    t = normalize_string(title).lower()
    n = (name or "").lower()

    ttoks = [x for x in re.findall(r'[a-z0-9]+', t) if len(x) >= 3]
    ntoks = re.findall(r'[a-z0-9]+', n)

    if not ttoks:
        return False

    # titolo a una parola: deve comparire chiaramente
    if len(ttoks) == 1:
        tok = ttoks[0]
        return tok in ntoks[:8] or tok in ntoks

    # titolo multi-parola: almeno tutti meno uno
    hits = sum(1 for tok in ttoks if tok in ntoks)
    needed = max(1, len(ttoks) - 1)
    return hits >= needed
def _tokenize_for_title(s: str) -> List[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if t]

def _series_title_match_ok(name: str, titles: List[str]) -> bool:
    ntoks = _tokenize_for_title(name)
    if not ntoks:
        return False

    for t in titles or []:
        ttoks = _tokenize_for_title(t)
        if not ttoks:
            continue

        # titolo a una parola: deve comparire nel filename,
        # ma evitiamo falsi positivi noti tipo "strike back"
        if len(ttoks) == 1:
            tok = ttoks[0]
            joined = " ".join(ntoks)
            if tok == "strike":
                # falsi positivi noti
                if "strike back" in joined:
                    continue
                if "strike first" in joined:
                    continue

                # accetta "strike" solo se è il titolo serie o parte finale del titolo serie
                # esempi buoni: "strike 1x01", "detective cormoran strike 1x01"
                if tok in ntoks:
                    idxs = [i for i, x in enumerate(ntoks) if x == tok]
                    for idx in idxs:
                        prev = ntoks[idx - 1] if idx > 0 else ""
                        nxt = ntoks[idx + 1] if idx + 1 < len(ntoks) else ""

                        if nxt.startswith("s0") or re.fullmatch(r"\d{1,2}x\d{1,2}", nxt) or re.fullmatch(r"\d{1,2}x", nxt):
                            return True
                        if prev in {"cormoran", "detective"}:
                            return True
                    continue
            else:
                if tok in ntoks:
                    return True
            continue

        # titolo multi-parola: tutti i token devono comparire nelle prime posizioni
        head = ntoks[:max(6, len(ttoks) + 2)]
        if all(tok in head for tok in ttoks):
            return True

    return False



def _norm_text(s: str) -> str:
    s = (s or "").lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def _title_tokens(title: str) -> set[str]:
    return set(_norm_text(title).split())

def _episode_title_variants(name: str) -> set[str]:
    n = _norm_text(name)
    out = {n}
    if not n:
        return out

    out.add(n.replace(" part 1", " parte 1"))
    out.add(n.replace(" part 2", " parte 2"))
    out.add(n.replace(" parte 1", " part 1"))
    out.add(n.replace(" parte 2", " part 2"))

    if "career of evil" in n:
        out.add(n.replace("career of evil", "la via del male"))
    if "la via del male" in n:
        out.add(n.replace("la via del male", "career of evil"))

    return {x.strip() for x in out if x.strip()}

def _series_match_ok(series_title: str, filename: str) -> bool:
    st = _norm_text(series_title)
    fn = _norm_text(filename)

    if not st or not fn:
        return False

    if st == "strike":
        if "strike back" in fn:
            return False
        return "strike" in fn

    series_tokens = _title_tokens(series_title)
    file_tokens = set(fn.split())

    required = {t for t in series_tokens if len(t) >= 3}
    if not required:
        return False

    return required.issubset(file_tokens)

def _series_match_soft_ok(series_title: str, filename: str) -> bool:
    st = _norm_text(series_title)
    fn = _norm_text(filename)

    if not st or not fn:
        return False

    # mantieni il caso ambiguo già protetto
    if st == "strike":
        return False

    series_tokens = [t for t in _title_tokens(series_title) if len(t) >= 3]
    file_tokens = set(fn.split())

    if not series_tokens:
        return False

    hits = sum(1 for t in series_tokens if t in file_tokens)

    # titolo a 1 token: niente fallback morbido
    if len(series_tokens) == 1:
        return False

    # titolo a 2 token: devono esserci entrambi
    if len(series_tokens) == 2:
        return hits == 2

    # titolo 3+ token: basta 2 token forti
    return hits >= 2

def _episode_title_match_score(expected_title: str, filename: str) -> int:
    if not expected_title:
        return 0

    fn = _norm_text(filename)
    variants = _episode_title_variants(expected_title)

    for v in variants:
        if v and v in fn:
            return 2

    expected_tokens = set()
    for v in variants:
        expected_tokens |= {t for t in v.split() if len(t) >= 4}

    if not expected_tokens:
        return 0

    hits = sum(1 for t in expected_tokens if t in fn)
    if hits >= 2:
        return 1

    return 0






def _is_subtitle_only_ita_release(name: str) -> bool:
    low = str(name or "").lower()
    tokens = re.findall(r"[a-z0-9]+", low)
    token_set = set(tokens)
    if "subbed" in token_set:
        return True
    if ("sub" in token_set or "subs" in token_set or "subita" in token_set) and ("ita" in token_set or "italian" in token_set):
        audio_tokens = {
            "ac3", "aac", "eac3", "dts", "ddp", "ddp5", "ddp51",
            "dlmux", "webmux", "mux", "itaeng", "ita", "italian",
        }
        # "sub ita" senza codec/mux esplicito di solito è solo sottotitoli.
        codec_or_mux = {"ac3", "aac", "eac3", "dts", "ddp", "dlmux", "webmux", "mux"} & token_set
        return not bool(codec_or_mux)
    return False


def _has_italian_audio_marker(name: str) -> bool:
    """
    True solo se il filename contiene un marker audio italiano come token separato.
    Accetta:
      ITA, iTA, Italian, iTALiAN, Italiano
    Non accetta falsi positivi dentro parole:
      capITAna, rucATALA, ecc.
    """
    raw = str(name or "")
    low = raw.lower()

    if _is_subtitle_only_ita_release(raw):
        return False

    # Esclusioni lingue sicuramente non italiane.
    foreign_bad = [
        " catala ", " catalan ", " català ", " rucatala ",
        " french ", " truefrench ", " vf ", " vostfr ",
        " esp ", " spanish ", " german ", " deutsch ",
    ]
    padded = " " + re.sub(r"[^a-z0-9àèéìòù]+", " ", low) + " "
    if any(x in padded for x in foreign_bad):
        # Non basta a bloccare un multi-audio, ma evita casi catalani/francesi puri.
        # Se contiene anche marker ITA sotto, lo accettiamo comunque.
        pass

    # Tokenizzazione robusta: separa su punti, virgole, parentesi, trattini, underscore.
    tokens = re.findall(r"[a-z0-9]+", low)

    ita_tokens = {
        "ita",
        "italian",
        "italiano",
        "italiana",
        "italiani",
        "italiane",
        "italian",
        "italianaudio",
        "audioita",
        "italianmux",
        "italianwebmux",
        "italianwebrip",
        "italianhdtv",
    }

    if any(t in ita_tokens for t in tokens):
        return True

    # Pattern frequenti in release.
    compact = re.sub(r"[^a-z0-9]+", ".", low)
    patterns = [
        r"(^|\.)ita(\.|$)",
        r"(^|\.)italian(\.|$)",
        r"(^|\.)italiano(\.|$)",
        r"(^|\.)italian\.webmux(\.|$)",
        r"(^|\.)italian\.dlmux(\.|$)",
    ]

    return any(re.search(p, compact) for p in patterns)

def _has_english_audio_marker(name: str) -> bool:
    """True only for explicit English-audio markers, not the word in a title."""
    low = normalize_string(name or "").lower()
    tokens = set(re.findall(r"[a-z0-9]+", low))
    if {"eng", "englishaudio", "audioeng"} & tokens:
        return True
    padded = " " + re.sub(r"[^a-z0-9]+", " ", low) + " "
    return bool(re.search(r"\baudio\s+english\b|\benglish\s+audio\b", padded))


def _normalize_requested_language(language: str) -> str:
    return "eng" if (language or "ita").strip().lower() == "eng" else "ita"


def _has_requested_audio_marker(name: str, language: str = "ita") -> bool:
    if _normalize_requested_language(language) == "eng":
        return _has_english_audio_marker(name)
    return _has_italian_audio_marker(name)


def _candidate_belongs_to_requested_series(name: str, titles: List[str], season: int) -> bool:
    """
    Filtro anti-spin-off, ma non troppo rigido sui titoli normali/localizzati.

    Casi:
      title = "FBI"
      OK:  "FBI S01E01", "FBI - 1x02"
      NO:  "FBI International 1x02", "FBI Most Wanted 1x03"

      title = "Capitan Marleau"
      OK:  "Capitaine Marleau 1x01", "Capitan.Marleau.S01E01"
    """
    raw = name or ""

    raw_low = str(raw or "").lower()
    requested_fbi = any(str(t or "").strip().lower() == "fbi" for t in titles or [])
    if requested_fbi and (
        "1965" in raw_low
        or re.search(r"\bthe[\s._-]*f[\s._-]*b[\s._-]*i\b", raw_low)
        or "serie tv 1965" in raw_low
    ):
        return False

    def norm_alias(x: str) -> str:
        x = normalize_string(x or "").lower()

        # Alias/localizzazioni comuni che non devono far fallire il match.
        replacements = {
            "capitaine": "capitan",
            "captain": "capitan",
            "capitano": "capitan",
            "capitana": "capitan",
        }
        for a, b in replacements.items():
            x = re.sub(rf"\b{re.escape(a)}\b", b, x)

        return x

    norm = norm_alias(raw)
    tokens = re.findall(r"[a-z0-9]+", norm)
    compact_name = re.sub(r"[^a-z0-9]+", "", norm)

    def is_ep_marker(tok: str) -> bool:
        tok = tok.lower()
        return bool(
            re.fullmatch(rf"s0?{int(season)}e\d{{1,2}}", tok)
            or re.fullmatch(rf"{int(season)}x\d{{1,2}}", tok)
        )

    stop = {
        "the", "and", "of", "a", "an",
        "il", "lo", "la", "i", "gli", "le", "un", "una", "uno",
        "di", "del", "della", "delle", "dei", "degli",
        "e", "ed",
    }

    for title in titles or []:
        raw_title = str(title or "").strip()
        tnorm = norm_alias(raw_title)
        if not tnorm:
            continue

        title_tokens_all = re.findall(r"[a-z0-9]+", tnorm)
        title_tokens = [t for t in title_tokens_all if t not in stop]

        if not title_tokens:
            continue

        compact_title = re.sub(r"[^a-z0-9]+", "", tnorm)

        # Caso acronimo/titolo brevissimo maiuscolo: FBI, CSI, NCIS.
        # Qui serve match stretto per evitare spin-off.
        raw_compact = re.sub(r"[^A-Za-z0-9]+", "", raw_title)
        is_acronym = (
            len(title_tokens) == 1
            and len(title_tokens[0]) <= 5
            and raw_compact.isupper()
        )

        if is_acronym:
            root = title_tokens[0]

            if root == "fbi" and (
                "1965" in tokens
                or "thefbi" in compact_name
                or "thefbiserietv1965" in compact_name
            ):
                return False

            for i, tok in enumerate(tokens):
                if tok != root:
                    continue

                # FBI S01E01 oppure FBI 1x02
                if i + 1 < len(tokens) and is_ep_marker(tokens[i + 1]):
                    return True

                # FBI - stagione 1 / FBI season 1
                if i + 2 < len(tokens) and tokens[i + 1] in {"stagione", "season"} and tokens[i + 2] == str(int(season)):
                    return True

            return False

        # Titoli normali: prima prova compatta esatta.
        if compact_title and compact_title in compact_name:
            return True

        # Titoli brevi/generici a 2 parole: NON accettare parole sparse.
        # Esempio:
        #   "Donna Detective" deve accettare "Donna.Detective"
        #   ma NON "Detective Hole ... La Donna Nell Acqua"
        if len(title_tokens) == 2:
            return False

        # Fuzzy prudente: per titoli multi-token lunghi, richiede quasi tutti i token
        # e almeno il token più caratterizzante.
        if len(title_tokens) >= 3:
            hits = sum(1 for t in title_tokens if t in tokens or t in compact_name)
            anchor = max(title_tokens, key=len)

            if hits >= max(2, len(title_tokens) - 1) and (anchor in tokens or anchor in compact_name):
                return True

        # Titolo singolo non-acronimo: accetta se compare come token intero.
        if len(title_tokens) == 1:
            root = title_tokens[0]
            if root in tokens:
                return True

    return False




def _llm_series_aliases(cat, title: str) -> List[str]:
    """
    Genera alias/titoli alternativi cercabili su aMule.
    Serve per casi tipo:
      Capitan Marleau -> Capitaine Marleau / La capitana Marleau
    Non decide cosa scaricare: i risultati passano comunque dai filtri ITA/stagione/spin-off.
    """
    title = (title or "").strip()
    if not title:
        return []

    prompt = f"""
Dato questo titolo di serie TV: "{title}"

Restituisci SOLO JSON valido:
{{"aliases": ["titolo originale", "titolo italiano", "titolo internazionale"]}}

Regole:
- massimo 6 alias
- niente descrizioni
- niente anni
- niente stagione
- includi titoli noti con cui può comparire nei file italiani o europei
"""

    try:
        res = cat.llm(prompt)
        import json as _json
        m = re.search(r"\{.*\}", str(res), re.S)
        if not m:
            return []
        data = _json.loads(m.group(0))
        aliases = data.get("aliases") or []
    except Exception:
        return []

    out = []
    for a in aliases:
        a = normalize_string(str(a or "")).strip()
        if not a:
            continue
        if len(a) > 80:
            continue
        if a.lower() in {"none", "null", "n/a"}:
            continue
        if a not in out:
            out.append(a)

    return out





def _catalog_series_aliases(title: str) -> List[str]:
    """
    Alias serie da TMDb, versione stretta:
    - titolo richiesto
    - titolo italiano TMDb
    - titolo originale TMDb

    Basta. Niente translations globali, niente alternative_titles generiche.
    Serve a evitare timeout e alias inutili.
    """
    title = (title or "").strip()
    if not title:
        return []

    import json as _json
    import requests as _requests
    from pathlib import Path as _Path

    def read_token() -> str:
        for fp in (
            _Path("/app/cat/plugins/peppule/settings.json"),
            _Path("/data/plugins/peppule/settings.json"),
        ):
            try:
                if fp.exists():
                    data = _json.loads(fp.read_text(encoding="utf-8"))
                    for k in ("tmdb_api_key", "omdb_api_key", "tmdb_token", "tmdb_bearer_token"):
                        v = data.get(k)
                        if v:
                            return str(v).strip()
            except Exception:
                pass

        try:
            from .services import get_peppule_settings
            st = get_peppule_settings()
            for k in ("tmdb_api_key", "omdb_api_key", "tmdb_token", "tmdb_bearer_token"):
                v = getattr(st, k, None)
                if v:
                    return str(v).strip()
        except Exception:
            pass

        for fp in (
            _Path("/home/sibilla-cumana/jellyfin-novita-agent/config.json"),
            _Path("/app/jellyfin-novita-agent/config.json"),
        ):
            try:
                if fp.exists():
                    data = _json.loads(fp.read_text(encoding="utf-8"))
                    nested = data.get("config") if isinstance(data.get("config"), dict) else {}
                    for src in (data, nested):
                        for k in ("tmdb_bearer", "tmdb_api_key", "tmdb_token", "tmdb_bearer_token"):
                            v = src.get(k)
                            if v:
                                return str(v).strip()
            except Exception:
                pass

        return ""

    token = read_token()
    if not token:
        log_console(f"TMDb alias token mancante per {title!r}")
        return [title]

    headers = {
        "Authorization": f"Bearer {token}",
        "accept": "application/json",
    }

    def tmdb_get(path, params=None):
        try:
            r = _requests.get(
                "https://api.themoviedb.org/3" + path,
                headers=headers,
                params=params or {},
                timeout=20,
            )
            if r.status_code >= 400:
                log_console(f"TMDb alias HTTP {r.status_code} {path}: {r.text[:180]}")
                return None
            return r.json()
        except Exception as e:
            log_console(f"TMDb alias errore {path}: {e!r}")
            return None

    def add(out, value):
        value = normalize_string(str(value or "")).strip()
        if not value or len(value) > 90:
            return
        if value.lower() in {"none", "null", "n/a"}:
            return

        # Scarta alfabeti non latini.
        if re.search(r"[\u4e00-\u9fff\u3400-\u4dbf\u3040-\u30ff\uac00-\ud7af\u0400-\u04ff]", value):
            return

        if value not in out:
            out.append(value)

    # Query completa + token distintivi.
    query_candidates = [title]
    stop = {"serie", "season", "stagione", "capitan", "capitano", "captain"}
    toks = [
        t for t in re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]+", title)
        if len(t) >= 4 and t.lower() not in stop
    ]
    for t in sorted(toks, key=len, reverse=True):
        if t not in query_candidates:
            query_candidates.append(t)

    rows = []
    used_query = None

    for q in query_candidates:
        for lang in ("it-IT", "fr-FR", "en-US"):
            data = tmdb_get("/search/tv", {"query": q, "language": lang})
            got = (data or {}).get("results") or []
            if got:
                rows = got
                used_query = q
                break
        if rows:
            break

    if not rows:
        log_console(f"TMDb alias nessun risultato per {title!r}, queries={query_candidates}")
        return [title]

    tv_id = rows[0].get("id")
    if not tv_id:
        return [title]

    aliases: List[str] = []
    add(aliases, title)

    # Solo titolo italiano e originale.
    it_data = tmdb_get(f"/tv/{tv_id}", {"language": "it-IT"}) or {}
    add(aliases, it_data.get("name"))
    add(aliases, it_data.get("original_name"))

    # Se l'italiano è vuoto o uguale alla query, recupera dettaglio lingua originale/francese/inglese
    # solo per avere original_name corretto.
    for lang in ("fr-FR", "en-US"):
        data = tmdb_get(f"/tv/{tv_id}", {"language": lang}) or {}
        add(aliases, data.get("original_name"))
        add(aliases, data.get("name"))

    # Massimo 3 alias effettivi.
    aliases = aliases[:3]

    log_console(f"TMDb alias IT/original {title!r}: query={used_query!r} id={tv_id} aliases={aliases}")
    return aliases




def _catalog_series_episode_count(title: str, season: int) -> int:
    """
    Conta episodi reali della stagione da TMDb.
    Usa query completa + token distintivi, come _catalog_series_aliases.
    Ritorna 0 se non riesce.
    """
    title = (title or "").strip()
    if not title:
        return 0

    import json as _json
    import requests as _requests
    from pathlib import Path as _Path

    def read_token() -> str:
        for fp in (
            _Path("/app/cat/plugins/peppule/settings.json"),
            _Path("/data/plugins/peppule/settings.json"),
        ):
            try:
                if fp.exists():
                    data = _json.loads(fp.read_text(encoding="utf-8"))
                    for k in ("tmdb_api_key", "omdb_api_key", "tmdb_token", "tmdb_bearer_token"):
                        v = data.get(k)
                        if v:
                            return str(v).strip()
            except Exception:
                pass

        try:
            from .services import get_peppule_settings
            st = get_peppule_settings()
            for k in ("tmdb_api_key", "omdb_api_key", "tmdb_token", "tmdb_bearer_token"):
                v = getattr(st, k, None)
                if v:
                    return str(v).strip()
        except Exception:
            pass

        for fp in (
            _Path("/home/sibilla-cumana/jellyfin-novita-agent/config.json"),
            _Path("/app/jellyfin-novita-agent/config.json"),
        ):
            try:
                if fp.exists():
                    data = _json.loads(fp.read_text(encoding="utf-8"))
                    nested = data.get("config") if isinstance(data.get("config"), dict) else {}
                    for src in (data, nested):
                        for k in ("tmdb_bearer", "tmdb_api_key", "tmdb_token", "tmdb_bearer_token"):
                            v = src.get(k)
                            if v:
                                return str(v).strip()
            except Exception:
                pass

        return ""

    token = read_token()
    if not token:
        log_console(f"TMDb epcount token mancante per {title!r}")
        return 0

    headers = {
        "Authorization": f"Bearer {token}",
        "accept": "application/json",
    }

    def tmdb_get(path, params=None):
        try:
            r = _requests.get(
                "https://api.themoviedb.org/3" + path,
                headers=headers,
                params=params or {},
                timeout=20,
            )
            if r.status_code >= 400:
                log_console(f"TMDb epcount HTTP {r.status_code} {path}: {r.text[:180]}")
                return None
            return r.json()
        except Exception as e:
            log_console(f"TMDb epcount errore {path}: {e!r}")
            return None

    query_candidates = [title]
    stop = {"serie", "season", "stagione", "capitan", "capitano", "captain"}
    toks = [
        t for t in re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]+", title)
        if len(t) >= 4 and t.lower() not in stop
    ]
    for t in sorted(toks, key=len, reverse=True):
        if t not in query_candidates:
            query_candidates.append(t)

    rows = []
    used_query = None

    for q in query_candidates:
        for lang in ("it-IT", "fr-FR", "en-US"):
            data = tmdb_get("/search/tv", {"query": q, "language": lang})
            got = (data or {}).get("results") or []
            if got:
                rows = got
                used_query = q
                break
        if rows:
            break

    if not rows:
        log_console(f"TMDb epcount nessun risultato per {title!r}, queries={query_candidates}")
        return 0

    tv_id = rows[0].get("id")
    if not tv_id:
        return 0

    try:
        season_i = int(season or 1)
    except Exception:
        season_i = 1

    data = tmdb_get(f"/tv/{tv_id}/season/{season_i}", {"language": "it-IT"}) or {}
    episodes = data.get("episodes") or []

    # Conta solo episodi veri numerati.
    count = 0
    for ep in episodes:
        try:
            n = int(ep.get("episode_number") or 0)
        except Exception:
            n = 0
        if n > 0:
            count = max(count, n)

    log_console(f"TMDb epcount {title!r}: query={used_query!r} id={tv_id} S{season_i} episodes={count}")
    return count





def _deep_amule_job_search(query: str) -> List[dict]:
    """
    Ricerca profonda direttamente sul wrapper aMule /jobs.
    Serve quando execute_search_amule restituisce pochi risultati parziali.
    Normalizza i risultati nel formato usato da Peppule.
    """
    query = (query or "").strip()
    if not query:
        return []

    try:
        from .services import get_peppule_settings
        import requests as _requests
    except Exception:
        return []

    try:
        st = get_peppule_settings()
        base = str(getattr(st, "amule_api_url", "") or "").rstrip("/")
        key = str(getattr(st, "amule_api_key", "") or "")
    except Exception:
        return []

    if not base:
        return []

    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-API-Key"] = key

    try:
        job = _requests.post(
            base + "/jobs",
            headers=headers,
            json={"query": query},
            timeout=20,
        ).json()

        jid = job.get("id") or job.get("job_id")
        if not jid:
            return []

        _requests.post(
            base + f"/jobs/{jid}/run",
            headers=headers,
            timeout=160,
        )

        rows = _requests.get(
            base + f"/jobs/{jid}/results",
            headers=headers,
            timeout=40,
        ).json()
    except Exception as e:
        try:
            log_console(f"DEEP_RESCUE job error query={query!r}: {e!r}")
        except Exception:
            pass
        return []

    out = []
    for r in rows or []:
        name = (
            r.get("name")
            or r.get("filename")
            or r.get("file_name")
            or r.get("matched_name")
            or ""
        )
        if not name:
            continue
        if not _is_video_filename(name):
            continue

        rid = r.get("id") or r.get("result_id") or r.get("index") or r.get("idx")
        size_bytes = r.get("size_bytes") or r.get("size") or r.get("size_full") or 0
        sources = r.get("sources") or r.get("fonti") or r.get("source_count") or 0

        try:
            sources_i = int(sources or 0)
        except Exception:
            sources_i = 0

        item = dict(r)
        item["id"] = rid
        item["name"] = name
        item["filename"] = name
        item["size_bytes"] = size_bytes
        item["size"] = size_bytes
        item["sources"] = sources_i
        item["fonti"] = sources_i
        item["query"] = query

        # Score rescue: preferisci candidati con fonti.
        # Questo evita di scegliere file sospetti sources=0.
        item["score"] = int(item.get("score") or 0) + sources_i * 1000

        out.append(item)

    pass  # debug log removed

    return out





def _is_video_filename(name: str) -> bool:
    low = str(name or "").lower().strip()
    good = (
        ".mkv", ".mp4", ".avi", ".mov", ".m4v", ".mpg", ".mpeg",
        ".ts", ".m2ts", ".wmv", ".webm"
    )
    bad = (
        ".mht", ".html", ".htm", ".txt", ".nfo", ".srt", ".sub",
        ".jpg", ".jpeg", ".png", ".gif", ".pdf", ".zip", ".rar"
    )
    if any(low.endswith(x) for x in bad):
        return False
    return any(low.endswith(x) for x in good)



# ---------- pipelines ----------



def _movie_match_ok(title: str, year: int | None, filename: str) -> bool:
    title = (title or "").strip()
    filename = (filename or "").strip()

    if not title or not filename:
        return False

    # controllo anno vero: usa year separato, non il titolo
    f_years = re.findall(r"\b(19\d{2}|20\d{2})\b", filename)
    if year and f_years and str(year) != f_years[0]:
        return False

    # usa solo la parte principale del titolo
    base = title
    if " - " in base:
        base = base.split(" - ", 1)[0].strip()
    if ":" in base:
        base = base.split(":", 1)[0].strip()

    nt = re.findall(r"[a-z0-9]+", normalize_string(base).lower())
    nf = re.findall(r"[a-z0-9]+", normalize_string(filename).lower())

    if not nt or not nf:
        return False

    # titolo a una parola: accettalo solo se compare molto presto nel filename
    # così evitiamo ambiguità ma non blocchiamo film come Sicario / Zodiac / Prisoners
    if len(nt) == 1:
        tok = nt[0]
        return tok in nf[:4]

    # multi-parola: i token devono comparire presto nel filename
    head = nf[:max(6, len(nt) + 2)]
    return all(tok in head for tok in nt)

def _movie_quality_bonus(filename: str) -> int:
    s = normalize_string(filename or "")
    bonus = 0

    # risoluzione
    if "2160p" in s or "4k" in s or "uhd" in s:
        bonus -= 80
    elif "1080p" in s:
        bonus += 120
    elif "720p" in s:
        bonus += 40

    # sorgente
    if "remux" in s:
        bonus += 80
    if "bluray" in s or "bdrip" in s or "bdrip" in s:
        bonus += 60
    if "webdl" in s or "webrip" in s or "web-dl" in s:
        bonus += 35

    # audio / lingua
    if " ita " in f" {s} " or "italian" in s:
        bonus += 40
    if "ac3" in s:
        bonus += 25
    if "dts" in s or "truehd" in s:
        bonus += 35

    # contenitore
    if s.endswith(".mkv") or ".mkv" in s:
        bonus += 20
    if s.endswith(".mp4") or ".mp4" in s:
        bonus += 5
    if s.endswith(".avi") or ".avi" in s:
        bonus -= 25

    # malus spazzatura
    bad_tokens = [" md ", " cam ", " ts ", " telesync ", " hdts ", " hdcam "]
    for tok in bad_tokens:
        if tok in f" {s} ":
            bonus -= 180

    return bonus

def _movie_rank_value(r: dict) -> int:
    name = (
        r.get("filename")
        or r.get("file")
        or r.get("name")
        or r.get("title")
        or ""
    )
    try:
        base_score = int(r.get("score") or 0)
    except Exception:
        base_score = 0
    return base_score + _movie_quality_bonus(name)


def _movie_is_bad_release_name(name: str) -> bool:
    s = (name or "").lower()
    tokens = set(re.findall(r"[a-z0-9]+", s))
    hard_bad_tokens = {
        "cam", "ts", "tc", "telesync", "hdts", "hdcam",
        "md", "ld", "dvdrip", "screener", "dvdscr", "r5",
        "480p", "360p",
    }
    return (
        s.endswith(".avi")
        or ".avi" in s
        or "divx" in tokens
        or "xvid" in tokens
        or bool(tokens & hard_bad_tokens)
    )


def _movie_has_strong_quality_signal(name: str) -> bool:
    s = (name or "").lower()
    # Fail closed for automatic downloads: at least HD resolution must be
    # explicitly advertised. Source tags alone are not enough.
    return any(tok in s for tok in ("720p", "1080p", "2160p", "4k", "uhd"))


def _movie_is_hard_bad_release_name(name: str) -> bool:
    s = normalize_string(name or "").lower()
    return any(x in f" {s} " for x in [" cam ", " ts ", " tc ", " telesync ", " hdts ", " hdcam ", " md "])


def _movie_chapter_marker_for_match(text: str) -> str:
    s = normalize_string(text or "").lower()
    s = re.sub(r"\b(19|20)\d{2}\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    tokens = [t for t in s.split() if t]
    if not tokens:
        return ""
    roman = {"ii": "2", "iii": "3"}
    for token in tokens:
        if token in {"2", "3"}:
            return token
        if token in roman:
            return roman[token]
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


def _movie_legacy_acceptable(title: str, year, cand: dict, language: str = "ita") -> bool:
    name = cand.get("name") or cand.get("filename") or cand.get("file") or ""
    low = normalize_string(name).lower()

    if _movie_is_bad_release_name(name):
        return False
    if not _movie_has_strong_quality_signal(name):
        return False

    if not _movie_match_ok(title, year, name):
        return False

    if year:
        try:
            if int(year) > 2016:
                return False
        except Exception:
            return False
    else:
        return False

    if not _has_requested_audio_marker(name, language):
        return False

    try:
        fonti = int(cand.get("fonti") or cand.get("sources") or 0)
    except Exception:
        fonti = 0

    try:
        size_mb = float(cand.get("size_mb") or 0)
    except Exception:
        size_mb = 0

    if fonti < 2:
        return False

    if not (650 <= size_mb <= 2500):
        return False

    return True


def _looks_like_episode_release(name: str) -> bool:
    s = (name or "").lower()

    # Pattern serie classici: S01E07, 1x07
    if re.search(r'(^|[^0-9])s\d{1,2}e\d{1,2}([^0-9]|$)', s):
        return True
    if re.search(r'(^|[^0-9])\d{1,2}x\d{1,2}([^0-9]|$)', s):
        return True

    # Anime / release episodiche: titolo_07_nomeepisodio, titolo - 07, [07]
    if re.search(r'(^|[._\-\[\(\s])\d{1,3}([._\-\]\)\s]|$)', s):
        episodeish_words = [
            "episode", "episodio", "sub", "subbed", "fansub", "anime",
            "higashira", "isana", "stepmom", "daughter", "ex"
        ]
        if any(w in s for w in episodeish_words):
            return True

    tv_tokens = [
        "stagione", "season", "episodio", "episode"
    ]
    return any(tok in s for tok in tv_tokens)

def _movie_candidate_ok(title: str, year, name: str, language: str = "ita") -> bool:
    s = (name or "").lower()

    if _looks_like_episode_release(s):
        return False
    if not _has_requested_audio_marker(name, language):
        return False

    if _movie_chapter_marker_for_match(title) != _movie_chapter_marker_for_match(name):
        return False

    title_norm = normalize_string(title).lower()
    title_tokens = [t for t in re.findall(r'[a-z0-9]+', title_norm) if len(t) >= 3]

    if not title_tokens:
        return False

    hits = sum(1 for t in title_tokens if t in s)

    if len(title_tokens) == 1:
        if hits < 1:
            return False
    elif len(title_tokens) <= 3:
        # Short titles are collision-prone: "It - Capitolo due" must never
        # match "I due Papi" merely because both contain "due".
        if hits < len(title_tokens):
            return False
    else:
        if hits < max(2, len(title_tokens) - 1):
            return False

    if year:
        y = str(year)
        filename_years = set(re.findall(r"\b(?:19|20)\d{2}\b", s))
        if filename_years and y not in filename_years:
            return False
        # Automatic selection is fail-closed: when the canonical identity has
        # an year, the release name must carry that same year.
        if y not in s:
            return False


    else:
        # Senza anno, titoli corti/generici sono troppo pericolosi:
        # "amore", "illusione", ecc. pescano episodi, anime o film sbagliati.
        if len(title_tokens) <= 1:
            return False
        if len(title_tokens) == 2 and hits < 2:
            return False

    return True


def pipeline_movie(cat, raw_query: str, language: str = "ita") -> str:
    clean = normalize_string(raw_query)
    lang = _normalize_requested_language(language)
    lang_tag = lang
    title, year = ask_llm_movie_info(cat, clean)

    # Guard precoce: titoli corti/generici senza anno non devono nemmeno interrogare aMule.
    # Esempio reale: "Cos'è l'amore?" pescava episodi/anime non pertinenti.
    title_norm = normalize_string(title).lower()
    title_tokens = [t for t in re.findall(r'[a-z0-9]+', title_norm) if len(t) >= 3]
    if not year and len(title_tokens) <= 2:
        return f"⛔ BLOCCATO: titolo film troppo generico senza anno — **{title}**. Specifica l'anno o un titolo più preciso."

    if check_jellyfin_movie(title, year, language=lang):
        return f" **{title} ({year})** è già presente su Jellyfin."

    queries = []
    for t in movie_search_variants(title):
        if year:
            queries.append(f"{t} {year} {lang_tag}")
        queries.append(f"{t} {lang_tag}")
    
    seen = set()
    queries = [q for q in queries if not (q in seen or seen.add(q))]
    
    all_results = []
    used_query = None
    deep_search_used = False
    deep_queries = []
    
    def _collect_movie_results(query_list):
        for q in query_list:
            raw_res = execute_search_amule(q, preferred_language=lang)
            if not raw_res:
                continue
            for r in raw_res:
                rr = dict(r)
                rr_name = (
                    rr.get("filename")
                    or rr.get("file")
                    or rr.get("name")
                    or rr.get("title")
                    or ""
                )
                if not _movie_candidate_ok(title, year, rr_name, language=lang):
                    continue
                rr["_used_query"] = q
                rr["_rank"] = _movie_rank_value(rr)
                all_results.append(rr)
    
    def _ranked_unique_results():
        by_name = {}
        for r in all_results:
            name_key = (r.get("name") or "").strip().lower()
            if not name_key:
                continue
            prev = by_name.get(name_key)
            if prev is None or r.get("_rank", 0) > prev.get("_rank", 0):
                by_name[name_key] = r
        out_res = list(by_name.values())
        out_res.sort(key=lambda x: x.get("_rank", 0), reverse=True)
        return out_res
    
    _collect_movie_results(queries)

    # If the broad pass finds only candidates rejected by the strict gates,
    # do not stop before trying quality-specific queries.  Replacement/repair
    # jobs in particular need this path, but the same fail-closed filtering
    # still applies to every result collected here.
    if not all_results and year:
        deep_search_used = True
        pre_deep_terms = [
            f"1080p {lang_tag}", f"720p {lang_tag}", f"bdrip {lang_tag}", f"bluray {lang_tag}",
            f"mkv {lang_tag}", f"webrip {lang_tag}", f"web-dl {lang_tag}",
        ]
        for t in movie_search_variants(title):
            for term in pre_deep_terms:
                deep_queries.append(f"{t} {year} {term}")
        seen_q = set(queries)
        deep_queries = [q for q in deep_queries if not (q in seen_q or seen_q.add(q))]
        _collect_movie_results(deep_queries)
        queries.extend(deep_queries)

    if not all_results:
        return f" Nulla per: **{title}**"

    res = _ranked_unique_results()
    
    # Vera deep search: se la prima passata trova solo release legacy/scadenti,
    # prova query mirate a qualità migliori prima di accettare il fallback legacy.
    def _movie_has_strong_quality_signal(name: str) -> bool:
        s = (name or "").lower()
        return any(tok in s for tok in [
            "1080p", "720p", "2160p", "4k", "uhd",
            "bluray", "blu-ray", "bdrip", "brrip", "bdmux", "remux",
            "web-dl", "webdl", "webrip", "web-rip", "hdtv"
        ])

    top_candidates = res[:8]

    only_bad_candidates = bool(res) and not any(
        not _movie_is_bad_release_name(x.get("name") or "")
        for x in top_candidates
    )

    best_name_for_deep = res[0].get("name") or ""
    best_is_ambiguous_quality = bool(res) and (
        not _movie_is_bad_release_name(best_name_for_deep)
        and not _movie_has_strong_quality_signal(best_name_for_deep)
    )

    needs_deep_search = bool(res) and (
        only_bad_candidates
        or best_is_ambiguous_quality
    )

    def _movie_light_aliases_from_results():
        aliases = []
        base_norms = {normalize_string(x).lower() for x in movie_search_variants(title)}

        for r in res[:5]:
            name = (
                r.get("filename")
                or r.get("file")
                or r.get("name")
                or r.get("title")
                or ""
            )

            raw = name.replace("–", " - ").replace("—", " - ")
            parts = re.split(r"\s+-\s+", raw)

            # Esempio utile:
            # La Banda Baader Meinhof - Der Baader Meinhof Komplex (...)
            for part in parts[1:]:
                part = re.split(r"[\(\[]", part, 1)[0]
                part = part.replace(".", " ")
                part = re.sub(r"\b(19\d{2}|20\d{2})\b.*$", "", part)
                part = re.sub(r"\b(ita|italian|sub|subs|subita|eng|multi|dvdrip|bdrip|bluray|webdl|web-dl|webrip|xvid|divx|h264|x264|h265|x265|mkv|avi)\b.*$", "", part, flags=re.I)
                part = re.sub(r"[^A-Za-z0-9À-ÿ ]+", " ", part)
                part = re.sub(r"\s+", " ", part).strip()

                pn = normalize_string(part).lower()
                if len(part.split()) >= 2 and pn and pn not in base_norms and part not in aliases:
                    aliases.append(part)

                if len(aliases) >= 2:
                    return aliases

        return aliases

    if needs_deep_search and year:
        deep_search_used = True
        deep_terms = [
            f"1080p {lang_tag}",
            f"720p {lang_tag}",
            f"bdrip {lang_tag}",
            f"bluray {lang_tag}",
            f"mkv {lang_tag}",
            f"webrip {lang_tag}",
            f"web-dl {lang_tag}",
        ]
        alias_deep_terms = [
            f"1080p {lang_tag}",
            f"720p {lang_tag}",
            f"bdrip {lang_tag}",
            f"bluray {lang_tag}",
        ]

        for t in movie_search_variants(title):
            for term in deep_terms:
                deep_queries.append(f"{t} {year} {term}")

        # Fallback leggero: prova anche 1-2 titoli alternativi ricavati dai risultati.
        for t in _movie_light_aliases_from_results():
            for term in alias_deep_terms:
                deep_queries.append(f"{t} {year} {term}")

        seen = set(queries)
        deep_queries = [q for q in deep_queries if not (q in seen or seen.add(q))]
        _collect_movie_results(deep_queries)
        queries.extend(deep_queries)
        res = _ranked_unique_results()
    
    if not res:
        return f" Nulla per: **{title}**"
    
    if res:
        used_query = res[0].get("_used_query")
    
    write_cache({
        "kind": "movie",
        "query": clean,
        "title": title,
        "year": year,
        "used_query": used_query,
        "queries_tried": queries,
        "deep_search_used": deep_search_used,
        "deep_queries": deep_queries,
        "results": res[:50],
    })
    
    best = res[0]
    best_name = (best.get("name") or "").lower()

    bad_release = (
        best_name.endswith(".avi")
        or ".avi" in best_name
        or "divx" in best_name
        or "xvid" in best_name
        or any(x in f" {best_name} " for x in [" cam ", " ts ", " tc ", " telesync ", " hdts ", " hdcam "])
    )

    settings = get_peppule_settings()
    auto_download_enabled = bool(getattr(settings, "auto_download_enabled", False))
    movie_auto_download_enabled = bool(getattr(settings, "movie_auto_download_enabled", False))
    allow_movie_download = auto_download_enabled and movie_auto_download_enabled

    ok = False
    downloaded = None
    download_status = None
    attempted = []

    out: List[str] = []
    out.append(f" Risultati per **{title} ({year})** — audio {lang.upper()}:\n")
    out.append(f" Query usata: `{used_query}`\n")

    if not allow_movie_download:
        out.append(" **AUTO-DL:** ⏸ disabilitato da settings — preview risultati soltanto.\n")
    else:
        # 1) Prima prova candidati di qualità non legacy.
        for cand in res[:8]:
            cname = cand.get("name") or ""
            if _movie_is_bad_release_name(cname):
                continue
            if not _movie_has_strong_quality_signal(cname):
                continue
    
            attempted.append(cand.get("name",""))
            dlres = execute_download_amule(cand["id"], selected_result=cand, context={"kind": "Movie", "title": title, "query": used_query})
            ok = bool(dlres.get("ok")) if isinstance(dlres, dict) else bool(dlres)
            download_status = dlres.get("status") if isinstance(dlres, dict) else ("ok" if ok else None)
            if ok:
                downloaded = cand
                break
    
        # 2) Se non c'è nulla di meglio, per film vecchi accetta legacy controllato.
        if not ok:
            legacy = None
            for cand in res[:12]:
                if _movie_legacy_acceptable(title, year, cand, language=lang):
                    legacy = cand
                    break
    
            if legacy:
                attempted.append(legacy.get("name",""))
                dlres = execute_download_amule(legacy["id"], selected_result=legacy, context={"kind": "Movie", "title": title, "query": used_query})
                ok = bool(dlres.get("ok")) if isinstance(dlres, dict) else bool(dlres)
                download_status = dlres.get("status") if isinstance(dlres, dict) else ("ok" if ok else None)
                if ok:
                    downloaded = legacy
                    out.append(" ↪️ Deep search: nessuna qualità migliore confermata; accettato candidato legacy/back-catalog.\n")
    
        # 3) Se il top era scadente e non abbiamo trovato/accettato nulla, blocca esplicitamente.
        if bad_release and not ok:
            out.append(f" ⛔AUTO-DL BLOCCATO — top result scadente: {best.get('name','')}\n")
        if ok and downloaded:
            if download_status == "already_have":
                out.append(f" **AUTO-DL:** GIA PRESENTE - {downloaded.get('name','')}\n")
            else:
                out.append(f" **AUTO-DL:** ✅ OK — {downloaded.get('name','')}\n")
            if downloaded.get("id") != best.get("id"):
                out.append(f" -> Primo candidato non confermato in coda; usato candidato successivo.\n")
        else:
            out.append(f" **AUTO-DL:** ⚠ FALLITO — nessun candidato confermato in coda tra i primi {len(attempted)}\n")

    out.append("| N | File | MB | Fonti | Score |")
    out.append("|---|------|----|-------|-------|")
    for i, r in enumerate(res[:10], 1):
        out.append(
            f"| {i} | {r.get('name','')} | {r.get('size','?')} | {r.get('fonti','?')} | {r.get('_rank', r.get('score','?'))} |"
        )

    return "\n".join(out)

def pipeline_series(cat, raw_query: str, season: int, start_episode: int = 1, max_episodes: int | None = None, language: str = "ita") -> str:
    series_anchor_tokens: Set[str] = set()
    clean_input = normalize_string(raw_query)
    lang = _normalize_requested_language(language)
    lang_tag = lang

    # title + episode count (TVMaze first, then LLM fallback inside services.py)
    official_title, ep_count, episode_titles = ask_llm_series_info(cat, clean_input, season)

    titles: List[str] = []
    for t in (official_title, clean_input):
        t = (t or "").strip()
        if t and t not in titles:
            titles.append(t)

    # Alias automatici da catalogo TMDb: titolo originale / italiano / internazionale / alternative titles.
    # Non bastano per scaricare: i candidati devono comunque passare i filtri ITA/stagione/spin-off.
    alias_seed = titles[:]
    for base_title in alias_seed:
        for alias in _catalog_series_aliases(base_title):
            if alias and alias not in titles:
                titles.append(alias)

    # Limita alias prima delle ricerche aMule.
    # Evita timeout: ogni alias moltiplica query per episodio.
    # Tiene solo titoli latini e massimo 5 alias.
    def _has_blocked_script(value: str) -> bool:
        for ch in str(value or ""):
            o = ord(ch)
            if (
                0x4E00 <= o <= 0x9FFF   # CJK
                or 0x3400 <= o <= 0x4DBF
                or 0x3040 <= o <= 0x30FF # kana
                or 0xAC00 <= o <= 0xD7AF # hangul
                or 0x0400 <= o <= 0x04FF # cyrillic
            ):
                return True
        return False

    filtered_titles = []
    for t in titles:
        if not t:
            continue
        if _has_blocked_script(t):
            continue
        if t not in filtered_titles:
            filtered_titles.append(t)

    titles = filtered_titles[:5]

    log_console(f"Serie titles/aliases usati: {titles}")

    if not titles:
        return "📭 Query vuota."
    series_anchor_tokens = set()
    for _t in titles:
        series_anchor_tokens |= _anchor_tokens_from_name(_t)


    # Numero episodi da catalogo TMDb: evita fallback 12 quando la stagione reale ne ha meno.
    catalog_ep_count = 0
    for _t in titles:
        catalog_ep_count = _catalog_series_episode_count(_t, season)
        if catalog_ep_count > 0:
            break

    if catalog_ep_count > 0:
        ep_count = catalog_ep_count

    if not isinstance(ep_count, int) or ep_count <= 0:
        ep_count = 12  # last-resort fallback

    if max_episodes is not None:
        try:
            max_ep = int(max_episodes)
            if max_ep > 0:
                original_ep_count = ep_count
                ep_count = min(ep_count, max_ep)
                log_console(f"Limite episodi richiesto: primi {ep_count}/{original_ep_count}")
        except Exception:
            pass

    log_console(f"Serie: {titles} S{season} ({ep_count} eps)")

    # 1) dual season search (X first, then S), merge results
    season_results: List[dict] = []
    for t in titles:
        season_results.extend(execute_search_amule(f"{t} {season}x {lang_tag}", preferred_language=lang))
        season_results.extend(execute_search_amule(f"{t} S{season:02d} {lang_tag}", preferred_language=lang))

    season_results = _merge_and_sort(season_results)
    season_results = [r for r in season_results if _candidate_belongs_to_requested_series(r.get("name", ""), titles, season)]

    # 2) build per-episode ranked lists
    per_episode: Dict[int, List[dict]] = {ep: [] for ep in range(1, ep_count + 1)}
    packs: List[dict] = []

    for r in season_results:
        name = r.get("name", "")
        if not _candidate_belongs_to_requested_series(name, titles, season):
            continue
        eps = _extract_episodes_from_name(name, season)
        if eps:
            for ep in eps:
                if 1 <= ep <= ep_count:
                    per_episode[ep].append(r)
        elif _looks_like_season_pack(name, season):
            packs.append(r)

    # dedupe per episode, filtro stretto sul titolo serie e bonus titolo episodio
    for ep in per_episode:
        ranked = _merge_and_sort(per_episode[ep])
        filtered = []

        for r in ranked:
            name = r.get("name", "")

            series_ok = _candidate_belongs_to_requested_series(name, titles, season)

            if not series_ok:
                continue

            bonus = _episode_title_match_score(episode_titles.get(ep, ""), name)
            r2 = dict(r)
            r2["episode_title_match"] = bonus
            r2["score"] = r2.get("score", 0) + (200 if bonus == 2 else 60 if bonus == 1 else 0)
            filtered.append(r2)

        filtered = _merge_and_sort(filtered)
        per_episode[ep] = filtered

    packs = _merge_and_sort(packs)

    # Se il catalogo TMDb sovrastima la stagione rispetto alla numerazione ITA,
    # usa il massimo episodio contiguo con candidati ITA validi.
    # Prima però prova query mirate per gli episodi subito successivi:
    # altrimenti taglia troppo presto, es. Capitaine Marleau S1 fermata a 6 invece di 7.
    def _valid_ita_rows_for_ep(_rows, _ep):
        out = []
        for r in _rows or []:
            name = r.get("name", "") or r.get("filename", "")
            if not _has_requested_audio_marker(name, lang):
                continue
            if not _candidate_belongs_to_requested_series(name, titles, season):
                continue
            if _ep not in _extract_episodes_from_name(name, season):
                continue
            out.append(r)
        return _merge_and_sort(out)

    ita_contiguous_max = 0
    for _ep in range(1, ep_count + 1):
        good_rows = _valid_ita_rows_for_ep(per_episode.get(_ep) or [], _ep)
        if good_rows:
            per_episode[_ep] = good_rows
            ita_contiguous_max = _ep
        else:
            break

    # Prova a recuperare gli episodi successivi con ricerca mirata.
    # Limite prudente: massimo 5 episodi oltre il primo buco, per non esplodere in timeout.
    probe_until = min(ep_count, max(ita_contiguous_max + 5, 1))
    for _ep in range(ita_contiguous_max + 1, probe_until + 1):
        tmp = []
        for _t in titles[:3]:
            tmp.extend(execute_search_amule(f"{_t} {season}x{_ep:02d} {lang_tag}", preferred_language=lang))
            tmp.extend(execute_search_amule(f"{_t} S{season:02d}E{_ep:02d} {lang_tag}", preferred_language=lang))

        tmp = _merge_and_sort(tmp)
        good_rows = _valid_ita_rows_for_ep(tmp, _ep)

        if good_rows:
            per_episode[_ep] = good_rows
            ita_contiguous_max = _ep
            log_console(f"Epcount probe: trovato Ep {_ep:02} {lang.upper()}")
        else:
            log_console(f"Epcount probe: stop a Ep {_ep:02}, nessun {lang.upper()} valido")
            break

    if ita_contiguous_max >= 3 and ita_contiguous_max < ep_count:
        log_console(
            f"Epcount catalogo mantenuto: S{season} resta {ep_count} episodi; "
            f"match {lang.upper()} contigui trovati fino a {ita_contiguous_max}."
        )

    # 3) iterate episodes, skip Jellyfin, fallback per-episode only when needed
    report: List[str] = [f"📺 **Serie:** {titles[0]} S{season} ({ep_count} eps) — audio {lang.upper()}\n"]
    downloaded_ids: Set[int] = set()

    already_present = 0
    downloaded_ok = 0
    downloaded_fail = 0
    missing = 0
    consecutive_misses = 0

    MAX_FALLBACK_EPISODES = None
    fallback_used = 0


    start_ep = max(int(start_episode or 1), 1)
    end_ep = ep_count if max_episodes in (None, 0) else min(ep_count, start_ep + int(max_episodes) - 1)

    for ep in range(start_ep, end_ep + 1):
        # Jellyfin check
        if check_jellyfin_episode(titles, season, ep, language=lang):
            report.append(f"🔹 Ep {ep:02}: 🛡️ Già su Jellyfin")
            already_present += 1
            consecutive_misses = 0
            continue

        candidates = per_episode.get(ep) or []

        # if empty, try season packs as candidates (but avoid re-downloading same pack)
        if not candidates and packs:
            candidates = packs

        # fallback per-episode dual search only if still nothing at all in season-wide search
        if not candidates and (MAX_FALLBACK_EPISODES is None or fallback_used < MAX_FALLBACK_EPISODES):
            tmp: List[dict] = []
            for t in titles:
                tmp.extend(execute_search_amule(f"{t} {season}x{ep:02d} {lang_tag}", preferred_language=lang))
                tmp.extend(execute_search_amule(f"{t} S{season:02d}E{ep:02d} {lang_tag}", preferred_language=lang))

            tmp = _merge_and_sort(tmp)

            strict_tmp = [r for r in tmp if _candidate_belongs_to_requested_series(r.get("name", ""), titles, season)]

            if strict_tmp:
                candidates = strict_tmp
                per_episode[ep] = strict_tmp  # store ranking
            elif tmp:
                candidates = tmp
                per_episode[ep] = tmp  # store ranking

            fallback_used += 1

        # EMPTY_CANDIDATES_DEEP_FALLBACK_V2
        # Fallback profondo per naming vecchi/fiction:
        # es. "[Titolo 1] 1.01..." trovato con "Titolo 01 ITA".
        if not candidates:
            deep_tmp = []
            for _t in titles[:3]:
                deep_tmp.extend(_deep_amule_job_search(f"{_t} {season}x{ep:02d} {lang_tag}"))
                deep_tmp.extend(_deep_amule_job_search(f"{_t} S{season:02d}E{ep:02d} {lang_tag}"))
                deep_tmp.extend(_deep_amule_job_search(f"{_t} {ep:02d} {lang_tag}"))
                deep_tmp.extend(_deep_amule_job_search(f"{_t} episodio {ep} {lang_tag}"))

            deep_tmp = _merge_and_sort(deep_tmp)

            deep_ok = []
            for cand in deep_tmp or []:
                cname = (
                    cand.get("name")
                    or cand.get("filename")
                    or cand.get("matched_name")
                    or cand.get("file_name")
                    or cand.get("title")
                    or ""
                )

                try:
                    c_sources = int(cand.get("sources") or cand.get("fonti") or 0)
                except Exception:
                    c_sources = 0

                if c_sources <= 0:
                    continue
                if not _is_video_filename(cname):
                    continue
                if not _has_requested_audio_marker(cname, lang):
                    continue
                if not _candidate_belongs_to_requested_series(cname, titles, season):
                    continue

                ceps = _extract_episodes_from_name(cname, season)
                if ceps and ep not in ceps:
                    continue

                deep_ok.append(cand)

            if deep_ok:
                candidates = _merge_and_sort(deep_ok)
                per_episode[ep] = candidates


        if not candidates:
            # Fallback profondo per naming vecchi/fiction Rai:
            # es. "[Donna detective 1] 1.01..." trovato con "Donna Detective 01 ITA".
            deep_tmp = []
            for _t in titles[:3]:
                deep_tmp.extend(_deep_amule_job_search(f"{_t} {season}x{ep:02d} {lang_tag}"))
                deep_tmp.extend(_deep_amule_job_search(f"{_t} S{season:02d}E{ep:02d} {lang_tag}"))
                deep_tmp.extend(_deep_amule_job_search(f"{_t} {ep:02d} {lang_tag}"))
                deep_tmp.extend(_deep_amule_job_search(f"{_t} episodio {ep} {lang_tag}"))

            deep_tmp = _merge_and_sort(deep_tmp)

            deep_ok = []
            for cand in deep_tmp or []:
                cname = (
                    cand.get("name")
                    or cand.get("filename")
                    or cand.get("matched_name")
                    or cand.get("file_name")
                    or cand.get("title")
                    or ""
                )

                try:
                    c_sources = int(cand.get("sources") or cand.get("fonti") or 0)
                except Exception:
                    c_sources = 0

                if c_sources <= 0:
                    continue
                if not _is_video_filename(cname):
                    continue
                if not _has_requested_audio_marker(cname, lang):
                    continue
                if not _candidate_belongs_to_requested_series(cname, titles, season):
                    continue
                ceps = _extract_episodes_from_name(cname, season)
                if ceps and ep not in ceps:
                    continue

                deep_ok.append(cand)

            if deep_ok:
                candidates = _merge_and_sort(deep_ok)
                per_episode[ep] = candidates

        if not candidates:
            exp = episode_titles.get(ep, "")
            if exp:
                report.append(f"🔸 Ep {ep:02}: ⛔ nessun match valido per titolo serie/episodio atteso ({exp})")
            else:
                report.append(f"🔸 Ep {ep:02}: ⛔ nessun match stretto sul titolo serie")
            missing += 1
            consecutive_misses += 1
            continue
        # Filtro finale alias-aware.
        # Non usare solo titles[0]: "Capitan Marleau" deve accettare "Capitaine Marleau".
        alias_matched = [
            c for c in candidates
            if _candidate_belongs_to_requested_series(
                c.get("name", "") or c.get("filename", ""),
                titles,
                season
            )
        ]

        if alias_matched:
            candidates = alias_matched
        else:
            exp = episode_titles.get(ep, "")
            if exp:
                report.append(f" Ep {ep:02}: ⛔ nessun match valido per titolo serie/episodio atteso ({exp})")
            else:
                report.append(f" Ep {ep:02}: ⛔ nessun match stretto sul titolo serie")
            missing += 1
            consecutive_misses += 1
            continue

        # Cancello finale anti-spin-off / stagione sbagliata / lingua.
        # Accetta solo:
        # - episodio richiesto
        # - marker ITA reale
        # - appartenenza alla serie richiesta o a uno degli alias catalogo
        gated_candidates = []
        for cand in candidates or []:
            cname = (
                cand.get("name")
                or cand.get("filename")
                or cand.get("matched_name")
                or cand.get("file_name")
                or cand.get("title")
                or ""
            )
            ceps = _extract_episodes_from_name(cname, season)
            c_ita = _has_requested_audio_marker(cname, lang)
            c_belongs = _candidate_belongs_to_requested_series(cname, titles, season)

            # Debug solo sui primi episodi, così non esplode il log.
            if ep <= 3:
                pass  # debug log removed

            if ceps and ep not in ceps:
                continue

            if not c_ita:
                continue

            if not c_belongs:
                continue

            gated_candidates.append(cand)

        candidates = gated_candidates

        # Rescue fallback: se c'erano candidati ma erano solo FR/catala/non-ITA,
        # fai una ricerca mirata ITA per questo episodio.
        # Serve per casi tipo Capitaine Marleau S2E01:
        # season-wide trova solo FR, ma "Capitaine Marleau 2x01 ita" trova Camera Con Vista ITA.
        if not candidates:
            rescue = []
            for _t in titles[:3]:
                rescue.extend(execute_search_amule(f"{_t} {season}x{ep:02d} {lang_tag}", preferred_language=lang))
                rescue.extend(execute_search_amule(f"{_t} S{season:02d}E{ep:02d} {lang_tag}", preferred_language=lang))
                rescue.extend(execute_search_amule(f"{_t} {season}x{ep:02d} {lang_tag} DLMux", preferred_language=lang))
                rescue.extend(execute_search_amule(f"{_t} {season}x{ep:02d} {lang_tag}", preferred_language=lang))

            # Rescue profondo: execute_search_amule può restituire solo una vista parziale.
            # Il wrapper /jobs invece mostra risultati più completi.
            for _t in titles[:3]:
                rescue.extend(_deep_amule_job_search(f"{_t} {season}x{ep:02d} {lang_tag}"))
                rescue.extend(_deep_amule_job_search(f"{_t} S{season:02d}E{ep:02d} {lang_tag}"))
                rescue.extend(_deep_amule_job_search(f"{_t} {season}x{ep:02d} {lang_tag} DLMux"))
                rescue.extend(_deep_amule_job_search(f"{_t} {season}x{ep:02d} {lang_tag}"))
                # Pattern generico fiction vecchie/Rai: "Titolo 01 ITA", "[Titolo 1] 1.01..."
                rescue.extend(_deep_amule_job_search(f"{_t} {ep:02d} {lang_tag}"))
                rescue.extend(_deep_amule_job_search(f"{_t} episodio {ep} {lang_tag}"))

            rescue = _merge_and_sort(rescue)

            pass  # debug log removed

            rescue_gated = []
            for cand in rescue or []:
                cname = (
                    cand.get("name")
                    or cand.get("filename")
                    or cand.get("matched_name")
                    or cand.get("file_name")
                    or cand.get("title")
                    or ""
                )
                ceps = _extract_episodes_from_name(cname, season)
                c_ita = _has_requested_audio_marker(cname, lang)
                c_belongs = _candidate_belongs_to_requested_series(cname, titles, season)

                if ep <= 3:
                    pass  # debug log removed

                try:
                    c_sources = int(cand.get("sources") or cand.get("fonti") or 0)
                except Exception:
                    c_sources = 0

                if ceps and ep not in ceps:
                    continue
                if not c_ita:
                    continue
                if not c_belongs:
                    continue
                if c_sources <= 0:
                    continue

                rescue_gated.append(cand)

            if rescue_gated:
                candidates = _merge_and_sort(rescue_gated)
                per_episode[ep] = candidates
                pass  # debug log removed

        if not candidates:
            report.append(f"🔸 Ep {ep:02}: ⛔ nessun match stretto sul titolo serie")
            missing += 1
            consecutive_misses += 1
            continue

        # Try best candidates in order. Some top-scored results can fail to enter aMule queue.
        best = None
        ok = False
        dlres = None
        download_status = None

        for cand in candidates[:3]:
            rid = cand.get("id")
            best = cand

            # avoid duplicate download attempts
            if isinstance(rid, int) and rid in downloaded_ids:
                ok = True
                download_status = "already_in_queue"
                break

            dlres = execute_download_amule(
                rid,
                selected_result=cand,
                context={
                    "kind": "Episode",
                    "title": (titles[0] if titles else clean_input),
                    "season": season,
                    "episode": ep,
                    "query": cand.get("query"),
                },
            )
            ok = bool(dlres.get("ok")) if isinstance(dlres, dict) else bool(dlres)
            download_status = dlres.get("status") if isinstance(dlres, dict) else ("ok" if ok else None)

            if ok and isinstance(rid, int):
                downloaded_ids.add(rid)
                break

        if best is None:
            report.append(f"🔸 Ep {ep:02}: 📭")
            missing += 1
            consecutive_misses += 1
            continue

        if ok:
            if download_status == "already_have":
                report.append(f"Ep {ep:02}: GIA PRESENTE - {best.get('name','')}")
            else:
                report.append(f"🔹 Ep {ep:02}: ✅ {best.get('name','')}")
            downloaded_ok += 1
            consecutive_misses = 0
        else:
            report.append(f"🔹 Ep {ep:02}: ⚠️ FALLITO — {best.get('name','')}")
            downloaded_fail += 1
            consecutive_misses += 1

    # cache: store rankings (top 10 per episode) + top season results
    cache_per_ep: Dict[str, List[dict]] = {}
    for ep in range(1, ep_count + 1):
        lst = per_episode.get(ep) or []
        if lst:
            cache_per_ep[f"{ep:02d}"] = lst[:10]

    write_cache({
        "kind": "series",
        "query": clean_input,
        "title": (titles[0] if titles else clean_input),
        "season": season,
        "episodes": ep_count,
        "per_episode": cache_per_ep,
        "season_results_top": season_results[:50],
        "packs_top": packs[:10],
    })

    report.append("")
    report.append(
        f"📌 **Riepilogo:** già su Jellyfin {already_present} • download OK {downloaded_ok} • falliti {downloaded_fail} • mancanti {missing}"
    )
    report.append("🗂️ Classifiche per episodio salvate in cache (last_search_results.json).")

    return "\n".join(report)
