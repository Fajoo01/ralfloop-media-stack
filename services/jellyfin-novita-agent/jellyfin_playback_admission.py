from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from jellyfin_media_verify import jellyfin_headers, load_config, verify_path
from jellyfin_bad_media_autofix import (
    CONFIG,
    is_tomb_or_placeholder,
    jellyfin_items,
    same_media,
    trigger_alternative,
)

BASE = Path("/home/sibilla-cumana/jellyfin-novita-agent")
STATE = BASE / "data" / "playback_admission_state.json"
REPORT = BASE / "out" / "playback_admission_latest.json"
QUARANTINE_ROOT = Path("/mnt/origin_media/.ralf/quarantine/playback")
STRUCTURAL_FAILURES = {
    "ffprobe_failed",
    "decode_failed",
    "movie_duration_implausible",
    "episode_duration_implausible",
    "container_extension_mismatch",
}


def config_bool(cfg: dict[str, Any], name: str, default: bool = False) -> bool:
    return str(cfg.get(name, default)).strip().lower() in {"1", "true", "yes", "on"}


def load_state() -> dict[str, Any]:
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def parse_created(value: Any) -> float:
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def latest_items(cfg: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    base = str(cfg.get("jellyfin_url") or "").rstrip("/")
    response = requests.get(
        f"{base}/Items",
        params={
            "Recursive": "true",
            "IncludeItemTypes": "Movie,Episode",
            "SortBy": "DateCreated",
            "SortOrder": "Descending",
            "Limit": max(1, int(limit)),
            "Fields": "Path,DateCreated,MediaSources,MediaStreams,ProductionYear,SeriesName",
        },
        headers=jellyfin_headers(cfg),
        timeout=20,
    )
    response.raise_for_status()
    return list((response.json() or {}).get("Items") or [])


def refresh_jellyfin(cfg: dict[str, Any]) -> int | None:
    try:
        base = str(cfg.get("jellyfin_url") or "").rstrip("/")
        response = requests.post(f"{base}/Library/Refresh", headers=jellyfin_headers(cfg), timeout=10)
        return response.status_code
    except Exception:
        return None


def fingerprint(verified: dict[str, Any]) -> str:
    probe = verified.get("probe") or {}
    return "|".join(
        str(x or "")
        for x in (
            verified.get("path"),
            verified.get("size_bytes"),
            probe.get("duration"),
            probe.get("format_name"),
        )
    )


def quick_fingerprint(path: str) -> str:
    requested = Path(path)
    candidates = [requested]
    if str(requested).startswith("/media/"):
        rel = Path(str(requested)[len("/media/"):])
        candidates = [Path("/mnt/origin_media") / rel, Path("/mnt/media_cachefirst") / rel, requested]
    for candidate in candidates:
        try:
            stat = candidate.stat()
            return f"{candidate}|{stat.st_size}|{stat.st_mtime_ns}"
        except OSError:
            continue
    return f"missing|{requested}"


def authoritative_source(requested_path: str, verified_path: str) -> Path | None:
    requested = Path(requested_path)
    if str(requested).startswith("/media/"):
        rel = Path(str(requested)[len("/media/"):])
        candidate = Path("/mnt/origin_media") / rel
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            pass
    candidate = Path(verified_path)
    try:
        if candidate.is_file():
            return candidate
    except OSError:
        pass
    return None


def quarantine_media(item: dict[str, Any], verified: dict[str, Any]) -> dict[str, Any]:
    source = authoritative_source(str(item.get("Path") or ""), str(verified.get("path") or ""))
    if not source:
        return {"ok": False, "status": "source_missing"}
    allowed = (Path("/mnt/origin_media"), Path("/mnt/media_cachefirst"))
    if not any(str(source).startswith(str(root) + os.sep) for root in allowed):
        return {"ok": False, "status": "source_outside_media_roots", "source": str(source)}

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    item_id = str(item.get("Id") or "unknown")[:24]
    target_dir = QUARANTINE_ROOT / day
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{item_id}--{source.name}"
    counter = 1
    while target.exists():
        target = target_dir / f"{item_id}-{counter}--{source.name}"
        counter += 1

    try:
        source.replace(target)
    except OSError:
        shutil.move(str(source), str(target))
    return {"ok": True, "status": "quarantined", "source": str(source), "target": str(target)}


def healthy_sibling(cfg: dict[str, Any], bad_item: dict[str, Any], sample_seconds: int) -> str | None:
    item_type = str(bad_item.get("Type") or "")
    if item_type not in {"Movie", "Episode"}:
        return None
    bad_path = str(bad_item.get("Path") or "")
    for other in jellyfin_items(cfg, item_type, 10000):
        path = str(other.get("Path") or "")
        if not path or path == bad_path or not same_media(bad_item, other):
            continue
        checked = verify_path(path, sample_seconds=sample_seconds, playback_only=True)
        if checked.get("ok"):
            return path
    return None


def state_record(item: dict[str, Any], checked: dict[str, Any], status: str, now: float) -> dict[str, Any]:
    path = str(item.get("Path") or "")
    return {
        "path": path,
        "date_created": item.get("DateCreated"),
        "quick_fingerprint": quick_fingerprint(path),
        "fingerprint": fingerprint(checked),
        "status": status,
        "checked_at": now,
    }


def run_once(cfg: dict[str, Any], limit: int, lookback_hours: float, stable_seconds: int, sample_seconds: int) -> dict[str, Any]:
    now = time.time()
    cutoff = now - max(1.0, float(lookback_hours)) * 3600.0
    quarantine_enabled = config_bool(cfg, "jellyfin_playback_admission_quarantine_enabled", False)
    repair_enabled = config_bool(cfg, "jellyfin_playback_admission_repair_enabled", False)
    state = load_state()
    records = dict(state.get("items") or {})
    checked_count = 0
    deferred_count = 0
    actions: list[dict[str, Any]] = []

    for item in latest_items(cfg, limit):
        item_id = str(item.get("Id") or item.get("Path") or "")
        path = str(item.get("Path") or "")
        if not item_id or not path or is_tomb_or_placeholder(path):
            continue
        created = parse_created(item.get("DateCreated"))
        if created and created < cutoff and item_id not in records:
            continue
        if created and now - created < max(0, int(stable_seconds)):
            deferred_count += 1
            continue

        previous = records.get(item_id) or {}
        quick_fp = quick_fingerprint(path)
        if previous.get("quick_fingerprint") == quick_fp and previous.get("status") in {"ok", "ignored_nonstructural"}:
            continue

        checked = verify_path(path, sample_seconds=sample_seconds, playback_only=True)
        checked_count += 1

        status = str(checked.get("status") or "unknown")
        if checked.get("ok"):
            records[item_id] = state_record(item, checked, "ok", now)
            continue
        if status not in STRUCTURAL_FAILURES:
            records[item_id] = state_record(item, checked, "ignored_nonstructural", now)
            continue

        action: dict[str, Any] = {
            "item_id": item_id,
            "type": item.get("Type"),
            "name": item.get("Name"),
            "series_name": item.get("SeriesName"),
            "path": path,
            "failure": status,
            "verify": checked,
            "quarantine_enabled": quarantine_enabled,
            "repair_enabled": repair_enabled,
        }
        sibling = healthy_sibling(cfg, item, sample_seconds)
        action["healthy_sibling"] = sibling

        if quarantine_enabled:
            action["quarantine"] = quarantine_media(item, checked)
            if action["quarantine"].get("ok"):
                action["jellyfin_refresh_status"] = refresh_jellyfin(cfg)
        else:
            action["quarantine"] = {"ok": False, "status": "dry_run"}

        if sibling:
            action["repair"] = {"status": "healthy_sibling_present", "path": sibling}
        elif repair_enabled:
            repair_status, repair_detail = trigger_alternative(item, cfg, True)
            action["repair"] = {"status": repair_status, "detail": repair_detail}
        else:
            action["repair"] = {"status": "dry_run"}

        records[item_id] = state_record(item, checked, "quarantined" if action["quarantine"].get("ok") else "structural_bad", now)
        actions.append(action)

    kept = sorted(records.items(), key=lambda kv: float((kv[1] or {}).get("checked_at") or 0), reverse=True)[:1000]
    save_json_atomic(STATE, {"updated_at": now, "items": dict(kept)})
    report = {
        "ok": True,
        "timestamp": now,
        "checked_count": checked_count,
        "deferred_count": deferred_count,
        "actions": actions,
        "quarantine_enabled": quarantine_enabled,
        "repair_enabled": repair_enabled,
    }
    save_json_atomic(REPORT, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(CONFIG))
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--lookback-hours", type=float, default=24.0)
    parser.add_argument("--stable-seconds", type=int, default=45)
    parser.add_argument("--sample-seconds", type=int, default=3)
    args = parser.parse_args()
    cfg = load_config(args.config)
    report = run_once(cfg, args.limit, args.lookback_hours, args.stable_seconds, args.sample_seconds)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
