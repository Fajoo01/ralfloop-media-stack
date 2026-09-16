from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class MediaRequest:
    kind: str
    title: str
    language: str = "ita"
    season: Optional[int] = None
    all_seasons: bool = False


def _clean_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).strip(" .,:;- ")


def parse_media_request(query: str) -> MediaRequest:
    """Parse a natural Peppule request after the leading download verb was removed.

    Italian is deliberately the default. A different language is used only when
    the request ends with an explicit language qualifier.
    """
    text = _clean_spaces(query)
    if not text:
        raise ValueError("empty_media_request")

    language = "ita"
    lang_patterns = (
        ("eng", r"(?i)\s+(?:in\s+|lingua\s+)?(?:inglese|english|eng)\s*$"),
        ("ita", r"(?i)\s+(?:in\s+|lingua\s+)?(?:italiano|italiana|italian|ita)\s*$"),
    )
    for lang, pattern in lang_patterns:
        if re.search(pattern, text):
            text = _clean_spaces(re.sub(pattern, "", text))
            language = lang
            break

    all_patterns = (
        r"(?i)\s+(?:tutte\s+le\s+stagioni|tutte\s+stagioni)\s*$",
        r"(?i)\s+(?:all\s+seasons|every\s+season)\s*$",
        r"(?i)\s+(?:stagioni\s+complete|serie\s+completa|complete\s+series)\s*$",
    )
    for pattern in all_patterns:
        if re.search(pattern, text):
            title = _clean_spaces(re.sub(pattern, "", text))
            if not title:
                raise ValueError("missing_series_title")
            return MediaRequest(kind="series_all", title=title, language=language, all_seasons=True)

    season_patterns = (
        r"(?i)^(?P<title>.+?)\s+(?:stagione|season)\s*0*(?P<season>\d{1,2})$",
        r"(?i)^(?P<title>.+?)\s+s0*(?P<season>\d{1,2})$",
    )
    for pattern in season_patterns:
        match = re.match(pattern, text)
        if not match:
            continue
        season = int(match.group("season"))
        title = _clean_spaces(match.group("title"))
        if title and 1 <= season <= 99:
            return MediaRequest(kind="series", title=title, language=language, season=season)

    return MediaRequest(kind="movie", title=text, language=language)
