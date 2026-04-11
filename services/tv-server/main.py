import os
import importlib.util
import threading
import time
import json
import json
import asyncio
import subprocess
import time
import requests
import re
from fastapi import FastAPI, HTTPException, Request, Response, BackgroundTasks
from fastapi.responses import StreamingResponse, PlainTextResponse

# Tenta di importare il resolver, altrimenti usa fallback
try:
    from resolver import StreamResolver
except ImportError:
    print("⚠️ WARNING: resolver.py non trovato.")
    class StreamResolver:
        def get_real_url(self, u, h=None): return u

print("\n\n ---> SERVER V100.1 (APP FIX + GOLDEN EDITION) <--- \n\n")

CHANNELS_FILE = "/app/data/channels_out.json"
SRC_FILE = "/app/data/channels_src.json"
CAT_API_URL = "http://127.0.0.1:1865/scraper/update/"

# --- PUNTO CRITICO: INIZIALIZZAZIONE APP ---
app = FastAPI()

RAI_STREAM_START_TIMEOUT = 15.0
RAI_STREAM_READ_CHUNK = 4096



resolver = StreamResolver()
# -------------------------------------------

def get_channel_from_file(channel_id):
    if os.path.exists(CHANNELS_FILE):
        try:
            with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    data = json.loads(content)
                    for ch in data.get("channels", []):
                        if ch["id"].lower() == channel_id.lower(): return ch
        except: pass
    if os.path.exists(SRC_FILE):
        try:
            with open(SRC_FILE, "r", encoding="utf-8") as f:
                for ch in json.load(f).get("channels", []):
                    if ch["id"].lower() == channel_id.lower(): 
                        ch["stream_url"] = None
                        return ch
        except: pass
    return None

def is_stream_valid(final_url):
    if not final_url:
        return False
    u = str(final_url).lower()

    # manifest diretti già risolti
    if ".m3u8" in u or ".mpd" in u:
        return True

    # alcuni provider noti
    if "uplynk" in u or "akamaized" in u:
        return True

    # msvdn non manifest o non tokenizzato: da solo non basta
    if "msvdn" in u:
        return False

    return False

def run_cat_update_task(channel_id):
    try: 
        requests.post(f"{CAT_API_URL}{channel_id}", timeout=1)
    except: 
        pass

async def loading_gen():
    cmd = ["ffmpeg", "-re", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25", "-f", "lavfi", "-i", "sine=f=440:b=4", "-vf", "drawtext=text='AGGIORNAMENTO...':fontcolor=white:fontsize=40:x=(w-text_w)/2:y=h-100:box=1:boxcolor=black@0.6", "-c:v", "mpeg2video", "-b:v", "1M", "-c:a", "mp2", "-ac", "2", "-f", "mpegts", "pipe:1"]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        while True:
            chunk = await proc.stdout.read(64*1024)
            if not chunk: break
            yield chunk
    finally:
        if proc.returncode is None:
            try:
                proc.kill()
            except:
                pass

async def ffmpeg_generator(url, headers):
    final_url = url
    
    if not final_url:
        print("❌ URL Finale vuoto.")
        return

    ua = headers.get("User-Agent", "Mozilla/5.0")
    fl = str(final_url).lower()

    if "rai" in fl or "akamaized" in fl or "msvdn" in fl:
        headers["Referer"] = "https://www.raiplay.it/"
    if "uplynk" in fl:
        headers["Referer"] = "https://www.discoveryplus.com/"
    
    h_str = "".join([f"{k}: {v}\r\n" for k,v in headers.items() if k != "User-Agent"])
    
    print(f"▶️ FFMPEG START: {final_url[:100]}...")
    
    cmd = [
        "ffmpeg", 
        "-hide_banner", "-loglevel", "error",
        "-reconnect", "1", "-reconnect_streamed", "1", 
        "-reconnect_on_http_error", "4xx,5xx", "-reconnect_delay_max", "5",
        "-http_persistent", "0",
        "-fflags", "+genpts+discardcorrupt+igndts",
        "-analyzeduration", "20000000", 
        "-probesize", "20000000",
        "-protocol_whitelist", "file,http,https,tcp,tls,crypto",
        "-user_agent", ua, "-headers", h_str,
        "-i", final_url, 
        "-map", "0", "-ignore_unknown", "-c", "copy", 
        "-f", "mpegts", "-bufsize", "8192k", "pipe:1"
    ]
    
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        while True:
            chunk = await proc.stdout.read(64 * 1024)
            if not chunk: break
            yield chunk
    finally:
        if proc.returncode is None:
            try:
                proc.kill()
            except:
                pass


async def ffmpeg_probe_first_bytes(url, headers):
    ua = headers.get("User-Agent", "Mozilla/5.0")
    h_str = "".join([f"{k}: {v}\r\n" for k,v in headers.items() if k != "User-Agent"])

    print(f"▶️ FFMPEG START: {url[:100]}...")

    cmd = [
        "ffmpeg",
        "-hide_banner", "-loglevel", "error",
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_on_http_error", "4xx,5xx",
        "-reconnect_delay_max", "2",
        "-http_persistent", "0",
        "-protocol_whitelist", "file,http,https,tcp,tls,crypto",
        "-user_agent", ua,
        "-headers", h_str,
        "-i", url,
        "-map", "0",
        "-ignore_unknown",
        "-c", "copy",
        "-f", "mpegts",
        "pipe:1",
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    try:
        try:
            chunk = await asyncio.wait_for(
                proc.stdout.read(RAI_STREAM_READ_CHUNK),
                timeout=RAI_STREAM_START_TIMEOUT
            )
        except asyncio.TimeoutError:
            chunk = b""

        if chunk:
            return proc, chunk

        if proc.returncode is None:
            try:
                proc.kill()
            except:
                pass

        return None, None

    except:
        try:
            proc.kill()
        except:
            pass
        raise


async def resilient_stream(channel_id, url, headers):
    proc, first = await ffmpeg_probe_first_bytes(url, headers)

    if not proc and channel_id.lower().startswith("rai"):
        print(f"🔁 {channel_id} nessun byte -> refresh")

        try:
            requests.post(f"{CAT_API_URL}{channel_id}", timeout=2)
        except:
            pass

        await asyncio.sleep(1)

        ch = get_channel_from_file(channel_id)
        if ch:
            url = ch.get("stream_url")
            headers = ch.get("headers", {}) or {}
            proc, first = await ffmpeg_probe_first_bytes(url, headers)

    if not proc or not first:
        print(f"❌ stream unavailable for {channel_id}")
        return

    try:
        yield first
        while True:
            chunk = await proc.stdout.read(64 * 1024)
            if not chunk:
                break
            yield chunk
    finally:
        try:
            proc.kill()
        except:
            pass

@app.head("/stream/{channel_id}")
async def head_handler(channel_id: str): return Response(status_code=200)

@app.get("/stream/{channel_id}")
async def stream(channel_id: str, background_tasks: BackgroundTasks):
    ch = get_channel_from_file(channel_id)
    if not ch: raise HTTPException(404, "Not Found")
    
    url = ch.get("stream_url")
    saved_headers = ch.get("headers", {})
    needs_update = False
    
    if not url:
        print(f"⚠️ {channel_id} URL mancante -> Update")
        needs_update = True
    else:
        resolved = resolver.get_real_url(url, saved_headers)
        if not is_stream_valid(resolved):
            print(f"⚠️ {channel_id} Link invalido -> Update")
            needs_update = True

    if needs_update:
        try: requests.post(f"{CAT_API_URL}{channel_id}", timeout=1)
        except: pass
        return StreamingResponse(loading_gen(), media_type="video/mp2t")
    
    resolved = resolver.get_real_url(url, saved_headers)
    print(f"TV_SERVER DEBUG resolved url: {resolved}")

    mediaset_ids = {
        "canale5", "italia1", "rete4", "tgcom24", "topcrime",
        "20", "iris", "la5", "extra", "focus", "cine34", "27"
    }

    if channel_id.lower() in mediaset_ids:
        print(f"TV_SERVER MEDIASET BYPASS {channel_id} -> {resolved}")
        async def _mediaset_gen():
            ua = saved_headers.get("User-Agent", "Mozilla/5.0")
            h_str = "".join([f"{k}: {v}\r\n" for k, v in saved_headers.items() if k != "User-Agent"])
            cmd = [
                "ffmpeg",
                "-hide_banner", "-loglevel", "error",
                "-reconnect", "1",
                "-reconnect_streamed", "1",
                "-reconnect_on_http_error", "4xx,5xx",
                "-reconnect_delay_max", "2",
                "-protocol_whitelist", "file,http,https,tcp,tls,crypto",
                "-user_agent", ua,
                "-headers", h_str,
                "-i", resolved,
                "-map", "0:v:0?",
                "-map", "0:a?",
                "-ignore_unknown",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-tune", "zerolatency",
                "-c:a", "aac",
                "-f", "mpegts",
                "pipe:1",
            ]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            try:
                while True:
                    chunk = await proc.stdout.read(64 * 1024)
                    if not chunk:
                        break
                    yield chunk
            finally:
                try:
                    proc.kill()
                except:
                    pass

        return StreamingResponse(_mediaset_gen(), media_type="video/mp2t")


    discovery_ids = {
        "realtime", "nove", "dmax", "giallo",
        "motortrend", "foodnetwork", "k2", "frisbee"
    }

    if channel_id.lower() in discovery_ids:
        print(f"TV_SERVER DISCOVERY PRECHECK {channel_id} -> {resolved}")
        d_proc, d_first = await ffmpeg_probe_first_bytes(resolved, saved_headers)

        if not d_proc or not d_first:
            print(f"TV_SERVER DISCOVERY REFRESH {channel_id}")
            try:
                requests.post(f"{CAT_API_URL}{channel_id}", timeout=20)
            except Exception as e:
                print(f"TV_SERVER DISCOVERY refresh failed for {channel_id}: {e}")

            await asyncio.sleep(1)

            ch = get_channel_from_file(channel_id)
            if ch:
                url = ch.get("stream_url") or url
                saved_headers = ch.get("headers", {}) or {}
                resolved = resolver.get_real_url(url, saved_headers)
                print(f"TV_SERVER DISCOVERY resolved url (refresh): {resolved}")

            d_proc, d_first = await ffmpeg_probe_first_bytes(resolved, saved_headers)

        if not d_proc or not d_first:
            print(f"❌ stream unavailable for {channel_id} after discovery refresh")
            return PlainTextResponse(f"stream unavailable for {channel_id}", status_code=503)

        async def _discovery_gen():
            try:
                yield d_first
                while True:
                    chunk = await d_proc.stdout.read(64 * 1024)
                    if not chunk:
                        break
                    yield chunk
            finally:
                try:
                    d_proc.kill()
                except:
                    pass

        return StreamingResponse(_discovery_gen(), media_type="video/mp2t")

    proc, first = await ffmpeg_probe_first_bytes(resolved, saved_headers)

    if (not proc or not first) and channel_id.lower().startswith("rai"):
        print(f"🔁 {channel_id} nessun byte -> refresh")
        try:
            requests.post(f"{CAT_API_URL}{channel_id}", timeout=20)
        except Exception as e:
            print(f"TV_SERVER: direct refresh failed for {channel_id}: {e}")

        await asyncio.sleep(1)

        ch = get_channel_from_file(channel_id)
        if ch:
            url = ch.get("stream_url") or url
            resolved = resolver.get_real_url(url, saved_headers)
            print(f"TV_SERVER DEBUG resolved url (refresh): {resolved}")
            url = resolved
            saved_headers = ch.get("headers", {}) or {}
            resolved = resolver.get_real_url(url, saved_headers)
    print(f"TV_SERVER DEBUG resolved url: {resolved}")
    proc, first = await ffmpeg_probe_first_bytes(resolved, saved_headers)

    if not proc or not first:
        print(f"❌ stream unavailable for {channel_id}")
        return PlainTextResponse(f"stream unavailable for {channel_id}", status_code=503)

    async def _gen():
        try:
            yield first
            while True:
                chunk = await proc.stdout.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                proc.kill()
            except:
                pass

    return StreamingResponse(_gen(), media_type="video/mp2t")

@app.get("/playlist.m3u")
def playlist(request: Request):
    base = "http://172.17.0.1:8000"
    lines = ["#EXTM3U"]
    if os.path.exists(SRC_FILE):
        try:
            with open(SRC_FILE, "r", encoding="utf-8") as f:
                for c in json.load(f).get("channels", []):
                    lines.append(f'#EXTINF:-1 tvg-id="{c["id"]}" tvg-logo="{c.get("logo","")}",{c["name"]}')
                    lines.append(f"{base}/stream/{c['id']}")
        except: pass
    return PlainTextResponse("\n".join(lines))

@app.head("/playlist.m3u")
def playlist_m3u_head():
    return Response(status_code=200, media_type="audio/x-mpegurl")
