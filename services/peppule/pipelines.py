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

def _merge_and_sort(*lists: List[dict]) -> List[dict]:
    merged: List[dict] = []
    for lst in lists:
        if lst:
            merged.extend(lst)
    merged = _dedupe_by_id(merged)
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

        # titolo a una parola: deve comparire molto presto nel filename
        if len(ttoks) == 1:
            tok = ttoks[0]
            if tok in ntoks[:4]:
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

    nt = re.findall(r"[a-z0-9]+", normalize_string(base))
    nf = re.findall(r"[a-z0-9]+", normalize_string(filename))

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


def pipeline_movie(cat, raw_query: str) -> str:
    clean = normalize_string(raw_query)

    title, year = ask_llm_movie_info(cat, clean)

    if check_jellyfin_movie(title, year):
        return f"🛡️ **{title} ({year})** è già presente su Jellyfin."

    queries = []
    for t in movie_search_variants(title):
        if year:
            queries.append(f"{t} {year} ita")
        queries.append(f"{t} ita")

    seen = set()
    queries = [q for q in queries if not (q in seen or seen.add(q))]

    def _pick_name(x):
        return (
            x.get("filename")
            or x.get("file")
            or x.get("name")
            or x.get("title")
            or ""
        )

    res = []
    used_query = None
    for q in queries:
        raw_res = execute_search_amule(q)
        if not raw_res:
            continue

        filtered = [r for r in raw_res if _movie_match_ok(title, year, _pick_name(r))]
        if not filtered:
            continue

        filtered = sorted(filtered, key=_movie_rank_value, reverse=True)

        res = filtered
        used_query = q
        break

    if not res:
        return f"📭 Nulla per: **{title}**"

    write_cache({
        "kind": "movie",
        "query": clean,
        "title": title,
        "year": year,
        "used_query": used_query,
        "queries_tried": queries,
        "results": res[:50],
    })

    best = res[0]
    ok = execute_download_amule(best["id"])

    out: List[str] = []
    out.append(f"🔎 Risultati per **{title} ({year})**:\n")
    out.append(f"🧪 Query usata: `{used_query}`\n")
    out.append(f"🚀 **AUTO-DL:** {'✅ OK' if ok else '⚠️ FALLITO'} — {best.get('name','')}\n")

    out.append("| N | File | MB | Fonti | Score |")
    out.append("|---|------|----|-------|-------|")
    for i, r in enumerate(res[:10], 1):
        out.append(
            f"| {i} | {r.get('name','')} | {r.get('size_mb','?')} | {r.get('sources','?')} | {r.get('score','?')} |"
        )
    return "\n".join(out)
def pipeline_series(cat, raw_query: str, season: int, start_episode: int = 1, max_episodes: int | None = None) -> str:
    series_anchor_tokens: Set[str] = set()
    clean_input = normalize_string(raw_query)

    # title + episode count (TVMaze first, then LLM fallback inside services.py)
    official_title, ep_count, episode_titles = ask_llm_series_info(cat, clean_input, season)

    titles: List[str] = []
    for t in (official_title, clean_input):
        t = (t or "").strip()
        if t and t not in titles:
            titles.append(t)

    if not titles:
        return "📭 Query vuota."
    series_anchor_tokens = set()
    for _t in titles:
        series_anchor_tokens |= _anchor_tokens_from_name(_t)


    if not isinstance(ep_count, int) or ep_count <= 0:
        ep_count = 12  # last-resort fallback

    log_console(f"Serie: {titles} S{season} ({ep_count} eps)")

    # 1) dual season search (X first, then S), merge results
    season_results: List[dict] = []
    for t in titles:
        season_results.extend(execute_search_amule(f"{t} {season}x ita"))
        season_results.extend(execute_search_amule(f"{t} S{season:02d} ita"))

    season_results = _merge_and_sort(season_results)
    season_results = [r for r in season_results if _series_title_match_ok(r.get("name", ""), titles)]

    # 2) build per-episode ranked lists
    per_episode: Dict[int, List[dict]] = {ep: [] for ep in range(1, ep_count + 1)}
    packs: List[dict] = []

    for r in season_results:
        name = r.get("name", "")
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

            if not _series_match_ok(titles[0], name):
                continue

            bonus = _episode_title_match_score(episode_titles.get(ep, ""), name)
            r2 = dict(r)
            r2["episode_title_match"] = bonus
            r2["score"] = r2.get("score", 0) + (200 if bonus == 2 else 60 if bonus == 1 else 0)
            filtered.append(r2)

        filtered = _merge_and_sort(filtered)
        per_episode[ep] = filtered

    packs = _merge_and_sort(packs)

    # 3) iterate episodes, skip Jellyfin, fallback per-episode only when needed
    report: List[str] = [f"📺 **Serie:** {titles[0]} S{season} ({ep_count} eps)\n"]
    downloaded_ids: Set[int] = set()

    already_present = 0
    downloaded_ok = 0
    downloaded_fail = 0
    missing = 0
    consecutive_misses = 0

    MAX_CONSEC_MISSES = 6
    MAX_FALLBACK_EPISODES = 8
    fallback_used = 0


    start_ep = max(int(start_episode or 1), 1)
    end_ep = ep_count if max_episodes in (None, 0) else min(ep_count, start_ep + int(max_episodes) - 1)

    for ep in range(start_ep, end_ep + 1):
        if consecutive_misses >= MAX_CONSEC_MISSES:
            report.append("🛑 Stop: troppi episodi mancanti consecutivi.")
            break

        # Jellyfin check
        if check_jellyfin_episode(titles, season, ep):
            report.append(f"🔹 Ep {ep:02}: 🛡️ Già su Jellyfin")
            already_present += 1
            consecutive_misses = 0
            continue

        candidates = per_episode.get(ep) or []

        # if empty, try season packs as candidates (but avoid re-downloading same pack)
        if not candidates and packs:
            candidates = packs

        # fallback per-episode dual search only if still nothing at all in season-wide search
        if not candidates and not season_results and fallback_used < MAX_FALLBACK_EPISODES:
            tmp: List[dict] = []
            for t in titles:
                tmp.extend(execute_search_amule(f"{t} {season}x{ep:02d} ita"))
                tmp.extend(execute_search_amule(f"{t} S{season:02d}E{ep:02d} ita"))
            tmp = _merge_and_sort(tmp)
            tmp = [r for r in tmp if _series_title_match_ok(r.get("name", ""), titles)]
            if tmp:
                candidates = tmp
                per_episode[ep] = tmp  # store ranking
            fallback_used += 1

        if not candidates:
            exp = episode_titles.get(ep, "")
            if exp:
                report.append(f"🔸 Ep {ep:02}: ⛔ nessun match valido per titolo serie/episodio atteso ({exp})")
            else:
                report.append(f"🔸 Ep {ep:02}: ⛔ nessun match stretto sul titolo serie")
            missing += 1
            consecutive_misses += 1
            continue
        # filtro finale coerente con quello nuovo
        strict = [c for c in candidates if _series_match_ok(titles[0], c.get("name", ""))]
        if strict:
            candidates = strict
        else:
            exp = episode_titles.get(ep, "")
            if exp:
                report.append(f" Ep {ep:02}: ⛔  nessun match valido per titolo serie/episodio atteso ({exp})")
            else:
                report.append(f" Ep {ep:02}: ⛔  nessun match stretto sul titolo serie")
            missing += 1
            consecutive_misses += 1
            continue

        best = candidates[0]
        rid = best.get("id")

        # avoid duplicate download attempts
        if isinstance(rid, int) and rid in downloaded_ids:
            report.append(f"🔹 Ep {ep:02}: 🧾 Già in coda — {best.get('name','')}")
            downloaded_ok += 1
            consecutive_misses = 0
            continue

        ok = execute_download_amule(rid)
        if isinstance(rid, int):
            downloaded_ids.add(rid)

        if ok:
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
        "title": titles[0],
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
