#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path

VALID_BUCKETS = {
    "continuation",
    "new_release",
    "recent_catalog",
    "back_catalog",
    "library_gap",
}

BUCKET_WEIGHT = {
    "continuation": 50,
    "library_gap": 40,
    "new_release": 30,
    "recent_catalog": 20,
    "back_catalog": 10,
}


def norm_text(s):
    if s is None:
        return None
    s = str(s).strip()
    s = re.sub(r"\s+", " ", s)
    return s or None


def norm_kind(s):
    s = norm_text(s)
    if not s:
        return None
    s = s.lower()
    if s not in {"movie", "series"}:
        return None
    return s


def norm_genres(genres):
    if not genres:
        return []
    out = []
    seen = set()
    for g in genres:
        g = norm_text(g)
        if not g:
            continue
        key = g.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(g)
    return out


def norm_bucket(bucket):
    bucket = norm_text(bucket)
    if not bucket:
        return "recent_catalog"
    bucket = bucket.lower()
    if bucket not in VALID_BUCKETS:
        return "recent_catalog"
    return bucket


def norm_int(value, default=None):
    if value is None:
        return default
    try:
        return int(value)
    except Exception:
        return default


def dedupe_key(item):
    return (
        item["kind"],
        (item["title"] or "").lower(),
        item.get("season_number"),
    )


def score_seed_item(item):
    bucket = item.get("bucket", "recent_catalog")
    priority_boost = norm_int(item.get("priority_boost"), 0) or 0
    year = norm_int(item.get("year"), 0) or 0
    has_franchise = 5 if item.get("franchise") else 0
    has_season = 3 if item.get("season_number") else 0
    return BUCKET_WEIGHT.get(bucket, 0) + priority_boost * 10 + has_franchise + has_season + year / 10000.0


def normalize_item(raw):
    title = norm_text(raw.get("title"))
    kind = norm_kind(raw.get("kind"))
    if not title or not kind:
        return None

    item = {
        "title": title,
        "kind": kind,
        "genres": norm_genres(raw.get("genres")),
        "franchise": norm_text(raw.get("franchise")),
        "bucket": norm_bucket(raw.get("bucket")),
        "source": norm_text(raw.get("source")) or "manual_seed",
        "release_date": norm_text(raw.get("release_date")),
        "year": norm_int(raw.get("year")),
        "season_number": norm_int(raw.get("season_number")),
        "priority_boost": norm_int(raw.get("priority_boost"), 0) or 0,
        "notes": norm_text(raw.get("notes")) or "",
    }
    return item


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", default="catalog_seed.json", help="Input seed JSON")
    ap.add_argument("--out", default="external_catalog.json", help="Output catalog JSON")
    args = ap.parse_args()

    seed_path = Path(args.seed)
    out_path = Path(args.out)

    if not seed_path.exists():
        raise SystemExit(f"seed file non trovato: {seed_path}")

    payload = json.loads(seed_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit("il seed deve essere una lista JSON")

    normalized = []
    for raw in payload:
        item = normalize_item(raw)
        if item:
            normalized.append(item)

    deduped = {}
    for item in normalized:
        key = dedupe_key(item)
        old = deduped.get(key)
        if old is None or score_seed_item(item) > score_seed_item(old):
            deduped[key] = item

    final_items = list(deduped.values())
    final_items.sort(key=lambda x: score_seed_item(x), reverse=True)

    out_path.write_text(
        json.dumps(final_items, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(json.dumps({
        "seed": str(seed_path),
        "out": str(out_path),
        "input_items": len(payload),
        "normalized_items": len(normalized),
        "final_items": len(final_items),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
