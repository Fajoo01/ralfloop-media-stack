#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import requests

try:
    import numpy as np
except Exception:  # pragma: no cover - runtime dependency fallback
    np = None


DEFAULT_CONFIG = "/home/sibilla-cumana/jellyfin-novita-agent/config.json"


def run(cmd: list[str], timeout: int = 30) -> dict[str, Any]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {
            "ok": p.returncode == 0,
            "returncode": p.returncode,
            "stdout": p.stdout,
            "stderr": p.stderr,
            "cmd": cmd,
        }
    except Exception as exc:
        return {"ok": False, "returncode": 999, "stdout": "", "stderr": str(exc), "cmd": cmd}


def load_config(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def jellyfin_headers(cfg: dict[str, Any]) -> dict[str, str]:
    token = str(cfg.get("jellyfin_token") or "")
    return {"X-Emby-Token": token} if token else {}


def jellyfin_search(cfg: dict[str, Any], query: str, item_type: str) -> list[dict[str, Any]]:
    base = str(cfg.get("jellyfin_url") or "").rstrip("/")
    if not base:
        return []
    r = requests.get(
        f"{base}/Items",
        params={
            "Recursive": "true",
            "IncludeItemTypes": item_type,
            "SearchTerm": query,
            "Limit": 20,
            "Fields": "Path,MediaSources,MediaStreams",
        },
        headers=jellyfin_headers(cfg),
        timeout=12,
    )
    r.raise_for_status()
    return list((r.json() or {}).get("Items") or [])


def ffprobe(path: Path) -> dict[str, Any]:
    res = run([
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration,format_name:stream=index,codec_type,codec_name,profile,level,pix_fmt,width,height,channels:stream_tags=language,title",
        "-of",
        "json",
        str(path),
    ], timeout=25)
    if not res["ok"]:
        return {"ok": False, "error": res["stderr"][:4000]}
    try:
        data = json.loads(res["stdout"] or "{}")
    except Exception as exc:
        return {"ok": False, "error": f"bad_ffprobe_json: {exc}"}
    streams = data.get("streams") or []
    has_video = any(s.get("codec_type") == "video" for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    try:
        duration = float((data.get("format") or {}).get("duration") or 0)
    except Exception:
        duration = 0.0
    return {
        "ok": bool(has_video and has_audio and duration > 30),
        "duration": duration,
        "format_name": str((data.get("format") or {}).get("format_name") or ""),
        "streams": streams,
    }



def _audio_stream_label(stream: dict[str, Any]) -> str:
    tags = stream.get("tags") or {}
    return " ".join(str(x or "") for x in (tags.get("language"), tags.get("title"))).lower()


def _pick_audio_pair(streams: list[dict[str, Any]]) -> tuple[int | None, int | None]:
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if len(audio) < 2:
        return None, None
    ita = None
    other = None
    for s in audio:
        label = _audio_stream_label(s)
        idx = int(s.get("index"))
        if ita is None and any(x in label for x in ("ita", "italian", "italiano")):
            ita = idx
        elif other is None:
            other = idx
    if ita is None:
        ita = int(audio[0].get("index"))
    if other is None or other == ita:
        for s in audio:
            idx = int(s.get("index"))
            if idx != ita:
                other = idx
                break
    return ita, other


def _extract_audio_raw(path: Path, stream_index: int, start: float, seconds: int, out_path: Path) -> bool:
    res = run([
        "ffmpeg", "-nostdin", "-v", "error", "-ss", str(max(0, float(start))),
        "-i", str(path), "-t", str(int(seconds)), "-map", f"0:{int(stream_index)}",
        "-ac", "1", "-ar", "8000", "-f", "s16le", str(out_path), "-y",
    ], timeout=max(30, seconds + 25))
    return bool(res.get("ok") and out_path.exists() and out_path.stat().st_size > 0)


def _audio_envelope(raw_path: Path, win: int = 800) -> np.ndarray:
    data = np.fromfile(raw_path, dtype=np.int16).astype(np.float32)
    if data.size < win * 4:
        return np.array([], dtype=np.float32)
    peak = float(np.max(np.abs(data)) or 1.0)
    data = data / peak
    n = data.size // win
    frames = data[: n * win].reshape(n, win)
    env = np.sqrt(np.mean(frames * frames, axis=1))
    std = float(env.std())
    if std <= 1e-9:
        return np.array([], dtype=np.float32)
    return (env - float(env.mean())) / std


def _estimate_lag_seconds(a: np.ndarray, b: np.ndarray, max_lag_s: float = 6.0, hop_s: float = 0.1) -> tuple[float | None, float]:
    if a.size < 20 or b.size < 20:
        return None, 0.0
    max_lag = int(max_lag_s / hop_s)
    best_lag = 0
    best_score = -999.0
    for lag in range(-max_lag, max_lag + 1):
        if lag < 0:
            x = a[-lag:]
            y = b[: x.size]
        elif lag > 0:
            x = a[:-lag]
            y = b[lag:]
        else:
            x = a
            y = b
        n = min(x.size, y.size)
        if n < 20:
            continue
        score = float(np.dot(x[:n], y[:n]) / n)
        if score > best_score:
            best_score = score
            best_lag = lag
    return best_lag * hop_s, best_score


def audio_sync_check(path: Path, probe: dict[str, Any], sample_seconds: int = 20) -> dict[str, Any]:
    duration = float(probe.get("duration") or 0)
    ita_idx, ref_idx = _pick_audio_pair(list(probe.get("streams") or []))
    out: dict[str, Any] = {
        "ok": True,
        "status": "not_applicable",
        "ita_stream": ita_idx,
        "reference_stream": ref_idx,
        "method": "audio_envelope_cross_correlation",
    }
    if np is None:
        out.update({
            "ok": True,
            "status": "numpy_unavailable_audio_sync_skipped",
            "dependency_missing": "numpy",
        })
        return out
    if ita_idx is None or ref_idx is None or duration < 180:
        return out
    starts = [max(0.0, duration * 0.10), max(0.0, duration * 0.50), max(0.0, duration - max(90.0, sample_seconds + 20.0))]
    points = []
    with tempfile.TemporaryDirectory(prefix="jellyfin_audio_sync_") as td:
        tdir = Path(td)
        for start in starts:
            ita_raw = tdir / f"ita_{int(start)}.raw"
            ref_raw = tdir / f"ref_{int(start)}.raw"
            if not _extract_audio_raw(path, ita_idx, start, sample_seconds, ita_raw):
                continue
            if not _extract_audio_raw(path, ref_idx, start, sample_seconds, ref_raw):
                continue
            lag, score = _estimate_lag_seconds(_audio_envelope(ita_raw), _audio_envelope(ref_raw))
            if lag is not None:
                points.append({"start": round(float(start), 3), "lag_seconds": round(float(lag), 3), "score": round(float(score), 3)})
    out["points"] = points
    if len(points) < 2:
        out.update({"ok": True, "status": "insufficient_signal"})
        return out
    reliable = [p for p in points if float(p["score"]) >= 0.25]
    if len(reliable) < 2:
        out.update({"ok": True, "status": "low_correlation"})
        return out
    lags = [float(p["lag_seconds"]) for p in reliable]
    max_abs = max(abs(x) for x in lags)
    drift = max(lags) - min(lags)
    out["max_abs_lag_seconds"] = round(max_abs, 3)
    out["drift_seconds"] = round(drift, 3)
    if max_abs >= 0.45 and drift <= 0.35:
        out.update({"ok": False, "status": "audio_desync_suspected_constant_offset", "suggested_action": "remux_audio_offset", "suggested_offset_seconds": round(float(sum(lags) / len(lags)), 3)})
    elif max_abs >= 0.45 and drift > 0.35:
        out.update({"ok": False, "status": "audio_desync_suspected_drift", "suggested_action": "redownload_or_manual_resync"})
    else:
        out.update({"ok": True, "status": "audio_sync_ok"})
    return out


def decode_sample(path: Path, start: float, seconds: int, timeout_seconds: int = 20) -> dict[str, Any]:
    res = run([
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-ss",
        str(max(0, int(start))),
        "-i",
        str(path),
        "-t",
        str(seconds),
        "-map",
        "0:v:0",
        "-f",
        "null",
        "-",
    ], timeout=max(int(timeout_seconds), seconds + 15))
    return {"ok": res["ok"], "stderr": res["stderr"][:4000], "start": int(start), "seconds": seconds}


def verify_path(path: str, sample_seconds: int = 12, playback_only: bool = False) -> dict[str, Any]:
    requested = Path(path)
    p = requested
    exists = False
    path_error = None
    try:
        exists = p.exists()
    except OSError as exc:
        path_error = str(exc)

    if not exists and str(requested).startswith("/media/"):
        rel = Path(str(requested)[len("/media/"):])
        for root in (Path("/mnt/media_cachefirst"), Path("/mnt/origin_media")):
            candidate = root / rel
            try:
                if candidate.exists():
                    p = candidate
                    exists = True
                    break
            except OSError as exc:
                path_error = str(exc)

    out: dict[str, Any] = {"path": str(p), "requested_path": str(requested), "exists": exists}
    if not exists:
        out.update({"ok": False, "status": "missing"})
        if path_error:
            out["path_error"] = path_error
        return out
    if not playback_only and re.search(r"(^|[^a-z0-9])(3d|sbs|hsbs|half[ ._-]*sbs|tab|htab|over[ ._-]*under)([^a-z0-9]|$)", p.name.lower()):
        out.update({"ok": False, "status": "stereoscopic_3d"})
        return out
    out["size_bytes"] = p.stat().st_size
    probe = ffprobe(p)
    out["probe"] = probe
    if not probe.get("ok"):
        out.update({"ok": False, "status": "ffprobe_failed"})
        return out
    duration = float(probe.get("duration") or 0)
    format_name = str(probe.get("format_name") or "").lower()
    movie_path = any(part.lower() in {"film", "films", "movie", "movies"} for part in p.parts)

    max_duration = (8 if movie_path else 6) * 3600
    if duration > max_duration:
        out.update({
            "ok": False,
            "status": "movie_duration_implausible" if movie_path else "episode_duration_implausible",
            "structure_gate": {"duration_seconds": duration, "max_seconds": max_duration},
        })
        return out

    expected_formats = {
        ".mp4": ("mov", "mp4"),
        ".m4v": ("mov", "mp4"),
        ".mov": ("mov",),
        ".avi": ("avi",),
        ".mkv": ("matroska",),
        ".webm": ("matroska", "webm"),
        ".ts": ("mpegts",),
    }
    expected = expected_formats.get(p.suffix.lower())
    if expected and format_name and not any(token in format_name for token in expected):
        out.update({
            "ok": False,
            "status": "container_extension_mismatch",
            "structure_gate": {
                "extension": p.suffix.lower(),
                "format_name": format_name,
                "expected_any": list(expected),
            },
        })
        return out

    starts = [
        max(0, duration * 0.08),
        max(0, duration * 0.50),
        max(0, duration - max(30, sample_seconds + 5)),
    ]
    decode_timeout = 60 if playback_only else 20
    samples = [decode_sample(p, s, sample_seconds, timeout_seconds=decode_timeout) for s in starts]
    out["decode_samples"] = samples
    decode_ok = all(s.get("ok") for s in samples)
    out["ok"] = decode_ok
    out["status"] = "ok" if decode_ok else "decode_failed"
    video = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), {})
    audio = [s for s in probe.get("streams", []) if s.get("codec_type") == "audio"]

    # Second line of defence: filenames can lie. For movie libraries, reject
    # actual SD frames even if the release name advertises something better.
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if not playback_only and movie_path and width > 0 and width < 1200:
        out["ok"] = False
        out["status"] = "movie_resolution_below_hd"
        out["quality_gate"] = {
            "width": width,
            "height": height,
            "min_width": 1200,
        }
        return out

    out["android_tv_notes"] = []
    if any((a.get("codec_name") or "").lower() == "ac3" for a in audio):
        out["android_tv_notes"].append("AC3 audio: alcuni client Android TV richiedono transcode o passthrough corretto")
    if (video.get("codec_name"), video.get("profile"), video.get("level")) == ("h264", "High", 41):
        out["android_tv_notes"].append("Video H.264 High@4.1: profilo normalmente compatibile")
    if playback_only:
        return out
    sync = audio_sync_check(p, probe, sample_seconds=max(16, sample_seconds))
    out["audio_sync_check"] = sync
    if not sync.get("ok"):
        out["ok"] = False
        out["status"] = sync.get("status") or "audio_desync_suspected"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--path")
    ap.add_argument("--query")
    ap.add_argument("--type", choices=["Movie", "Episode"], default="Episode")
    ap.add_argument("--sample-seconds", type=int, default=12)
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.path:
        print(json.dumps(verify_path(args.path, args.sample_seconds), ensure_ascii=False, indent=2))
        return 0

    if not args.query:
        raise SystemExit("--path or --query required")
    items = jellyfin_search(cfg, args.query, args.type)
    results = []
    for item in items:
        path = item.get("Path")
        if path:
            v = verify_path(path, args.sample_seconds)
        else:
            v = {"ok": False, "status": "no_path"}
        v["jellyfin"] = {
            "id": item.get("Id"),
            "name": item.get("Name"),
            "series": item.get("SeriesName"),
            "season": item.get("ParentIndexNumber"),
            "episode": item.get("IndexNumber"),
        }
        results.append(v)
    print(json.dumps({"query": args.query, "items": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
