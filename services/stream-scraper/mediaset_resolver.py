import json
import time
import re
import base64
import requests

DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

MEDIASET_CHANNEL_ALIASES = {
    "canale5": "c5",
    "canale 5": "c5",
    "c5": "c5",

    "italia1": "i1",
    "italia 1": "i1",
    "i1": "i1",

    "rete4": "r4",
    "rete 4": "r4",
    "r4": "r4",

    "tgcom24": "kf",
    "tgcom 24": "kf",
    "kf": "kf",

    "topcrime": "lt",
    "top crime": "lt",
    "lt": "lt",

    "20": "lb",
    "20mediaset": "lb",
    "20 mediaset": "lb",
    "lb": "lb",

    "iris": "ki",
    "ki": "ki",

    "la5": "ka",
    "la 5": "ka",
    "ka": "ka",

    "mediasetextra": "kq",
    "mediaset extra": "kq",
    "extra": "kq",
    "kq": "kq",

    "focus": "fu",
    "fu": "fu",

    "cine34": "b6",
    "cine 34": "b6",
    "b6": "b6",

    "twentyseven": "ts",
    "twenty seven": "ts",
    "27": "ts",
    "27twentyseven": "ts",
    "27 twentyseven": "ts",
    "ts": "ts",
}

def _norm(x):
    return re.sub(r"[^a-z0-9]+", "", str(x or "").lower())


from urllib.parse import urlencode
import os
import tempfile
import xml.etree.ElementTree as ET

def mediaset_build_selector_url(selector: dict) -> str:
    base = selector.get("url", "")
    params = {
        "formats": selector.get("formats"),
        "assetTypes": selector.get("assetTypes"),
        "format": selector.get("format"),
        "balance": selector.get("balance"),
        "auto": selector.get("auto"),
        "tracking": selector.get("tracking"),
        "delivery": selector.get("delivery"),
    }
    if selector.get("publicUrl"):
        params["publicUrl"] = selector.get("publicUrl")
    params = {k: v for k, v in params.items() if v not in (None, "", False)}
    return f"{base}?{urlencode(params)}" if params else base

def mediaset_derive_clear_manifest(url: str):
    if not url:
        return None
    m = re.search(
        r'https://live\d+p?-col\.msf\.cdn\.mediaset\.net/live/ch-([a-z0-9]+)/([a-z0-9]+)-dash-widevine\.isml/manifest_hr\.mpd',
        url,
        re.I
    )
    if m:
        code = m.group(2).lower()
        return f"https://live03-col.msf.cdn.mediaset.net/live/ch-{code}/{code}-clr.isml/manifest.mpd"
    m = re.search(
        r'https://live\d+p?-col\.msf\.cdn\.mediaset\.net/live/ch-([a-z0-9]+)/([a-z0-9]+)-dash-widevine\.isml/manifest\.mpd',
        url,
        re.I
    )
    if m:
        code = m.group(2).lower()
        return f"https://live03-col.msf.cdn.mediaset.net/live/ch-{code}/{code}-clr.isml/manifest.mpd"
    return None

def mediaset_channel_code(channel_id=None, channel_name=None):
    for raw in (channel_id, channel_name):
        k = _norm(raw)
        for ak, av in MEDIASET_CHANNEL_ALIASES.items():
            if _norm(ak) == k:
                return av
    return None

def mediaset_fallback_candidates(channel_id=None, channel_name=None):
    code = mediaset_channel_code(channel_id, channel_name)
    if not code:
        return []
    return [
        f"https://live03-col.msf.cdn.mediaset.net/live/ch-{code}/{code}-clr.isml/manifest.mpd",
        f"https://live03-col-mediaset-it.akamaized.net/live/ch-{code}/{code}-clr.isml/manifest.mpd",
    ]

def mediaset_headers(cookies=None, ua=None):
    h = {
        "User-Agent": ua or DEFAULT_UA,
        "Referer": "https://mediasetinfinity.mediaset.it/",
        "Origin": "https://mediasetinfinity.mediaset.it",
        "sec-ch-ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }
    if cookies:
        h["Cookie"] = cookies
    return h

def _score_url(u):
    lu = (u or "").lower()
    s = 0

    # HLS sempre preferito
    if ".m3u8" in lu: s += 1000
    if "master.m3u8" in lu: s += 200
    if "playlist.m3u8" in lu: s += 150
    if "/index.m3u8" in lu: s += 120

    # mpd clear ma secondari
    if "-clr" in lu: s += 200
    if "clear" in lu: s += 200
    if ".mpd" in lu: s += 20

    # host buoni
    if "live3-mediaset-it.akamaized.net" in lu: s += 80
    if "live2.msf.cdn.mediaset.net" in lu: s += 70
    if "live03-col.msf.cdn.mediaset.net" in lu: s += 60

    # DRM = morte
    if "widevine" in lu: s -= 5000
    if "dash-widevine" in lu: s -= 5000
    if "playready" in lu: s -= 5000
    if "fairplay" in lu: s -= 5000
    if "drm" in lu: s -= 5000

    # junk
    if "audio" in lu: s -= 500
    if "_ao." in lu: s -= 500
    if "api-graph" in lu: s -= 500

    return s

def _pick_best(urls):
    urls = [u for u in urls if u]
    if not urls:
        return None
    return sorted(set(urls), key=_score_url, reverse=True)[0]



def _mediaset_code_upper(channel_id=None, channel_name=None):
    code = mediaset_channel_code(channel_id, channel_name)
    return code.upper() if code else None

def _extract_urls_from_text(txt):
    found = []
    if not txt:
        return found
    for pat in [
        r'https?://[^\s"\'\\<>]+\.m3u8[^\s"\'\\<>]*',
        r'https?://[^\s"\'\\<>]+\.mpd[^\s"\'\\<>]*',
        r'https?://[^\s"\'\\<>]+manifest[^\s"\'\\<>]*',
        r'https?://[^\s"\'\\<>]+isml[^\s"\'\\<>]*',
    ]:
        found.extend(re.findall(pat, txt))
    return list(dict.fromkeys(found))

def _is_drm_url(u):
    lu = (u or "").lower()
    return any(x in lu for x in [
        "widevine",
        "dash-widevine",
        "playready",
        "fairplay",
        "commonencryption",
        "security=",
    ])

def _pick_best_clear(urls):
    urls = [u for u in (urls or []) if u]
    clear = [u for u in urls if not _is_drm_url(u)]
    if clear:
        return _pick_best(clear)
    return None

def _mediaset_login_requests(session, ua):
    url = "https://api-ott-prod-fe.mediaset.net/PROD/play/idm/anonymous/login/v2.0"
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "Referer": "https://mediasetinfinity.mediaset.it/",
        "User-Agent": ua or DEFAULT_UA,
        "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Linux"',
    }
    payload = {
        "appName": "web//mediasetplay-web/1.1.0-509a584"
    }
    r = session.post(url, headers=headers, json=payload, timeout=30)
    print("SCRAPER: mediaset login status", r.status_code)
    print("SCRAPER: mediaset login body", r.text[:1000])
    r.raise_for_status()
    j = r.json()
    token = (j.get("response") or {}).get("beToken")
    sid = (j.get("response") or {}).get("sid")
    if not token or not sid:
        raise RuntimeError(f"Mediaset login incompleto: {j}")
    return token, sid

def _mediaset_playback_check(session, code_upper, token, sid, ua):
    url = f"https://api-ott-prod-fe.mediaset.net/PROD/play/playback/check/v2.0?sid={sid}"
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Origin": "https://mediasetinfinity.mediaset.it",
        "Referer": "https://mediasetinfinity.mediaset.it/",
        "User-Agent": ua or DEFAULT_UA,
        "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Linux"',
    }
    payload = {
        "channelCode": code_upper,
        "streamType": "LIVE",
        "delivery": "Streaming",
        "createDevice": True,
        "overrideAppName": "web//mediasetplay-web/1.1.0-509a584",
    }
    r = session.post(url, headers=headers, json=payload, timeout=30)
    print("SCRAPER: mediaset playback status", r.status_code, code_upper, flush=True)
    print("SCRAPER: mediaset playback body", r.text[:1500])
    r.raise_for_status()
    j = r.json()
    ms = (j.get("response") or {}).get("mediaSelector") or {}
    if not ms or not ms.get("url"):
        raise RuntimeError(f"Mediaset playbackCheck senza mediaSelector: {j}")
    return ms

def _mediaset_expand_media_selector(session, ms, ua):
    params = {}
    for k in ["formats", "assetTypes", "format", "balance", "auto", "tracking", "delivery"]:
        v = ms.get(k)
        if v is not None:
            params[k] = v

    headers = {
        "Accept": "*/*",
        "Referer": "https://mediasetinfinity.mediaset.it/",
        "Origin": "https://mediasetinfinity.mediaset.it",
        "User-Agent": ua or DEFAULT_UA,
    }

    r = session.get(ms["url"], params=params, headers=headers, timeout=30)
    txt = r.text or ""
    print("SCRAPER: selector body len", len(txt), flush=True)

    found = []
    found.extend(_extract_urls_from_text(txt))
    # a volte il manifest è già nella final URL
    if any(x in (r.url or "").lower() for x in [".mpd", ".m3u8", "manifest", "isml"]):
        found.append(r.url)

    found = list(dict.fromkeys(found))
    print("SCRAPER: mediaset SMIL urls found:")
    for u in found:
        print("  ", u)

    picked = _pick_best_clear(found)
    if picked:
        local_best = mediaset_build_local_best_mpd(picked)
        if local_best:
            return local_best, r.url, txt
        return picked, r.url, txt

    derived_clear = None
    smil_urls = found
    for _u in smil_urls:
        _cand = mediaset_derive_clear_manifest(_u)
        if _cand and not derived_clear:
            derived_clear = _cand
    if derived_clear:
        print("SCRAPER: mediaset derived clear fallback", derived_clear)
        local_best = mediaset_build_local_best_mpd(derived_clear)
        if local_best:
            return local_best, r.url, txt
        return derived_clear, r.url, txt
    print("SCRAPER: mediaset no clear manifest found in SMIL")
    return None, r.url, txt



def mediaset_build_local_best_mpd(url, channel_id=None, channel_name=None):
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        root = ET.fromstring(r.text)

        ns = ""
        if root.tag.startswith("{"):
            ns = root.tag.split("}")[0][1:]

        def tag(x):
            return f"{{{ns}}}{x}" if ns else x

        base_url = url.rsplit("/", 1)[0] + "/"
        base_tag = tag("BaseURL")
        if root.find(base_tag) is None:
            base_el = ET.Element(base_tag)
            base_el.text = base_url
            insert_pos = 0
            for i, child in enumerate(list(root)):
                if child.tag == tag("Period"):
                    insert_pos = i
                    break
                insert_pos = i + 1
            root.insert(insert_pos, base_el)

        changed = False
        for period in root.findall(tag("Period")):
            for aset in period.findall(tag("AdaptationSet")):
                reps = aset.findall(tag("Representation"))
                if not reps:
                    continue

                best = None
                best_score = (-1, -1, -1)

                for rep in reps:
                    bw = int(rep.get("bandwidth") or 0)
                    w = int(rep.get("width") or 0)
                    h = int(rep.get("height") or 0)
                    score = (w * h, bw, w + h)
                    if score > best_score:
                        best_score = score
                        best = rep

                for rep in list(reps):
                    if rep is not best:
                        aset.remove(rep)
                        changed = True

        out_dir = os.path.join(os.path.dirname(__file__), "mediaset_best")
        os.makedirs(out_dir, exist_ok=True)

        cid = channel_id or channel_name or "mediaset"
        safe = "".join(c if c.isalnum() or c in ("-","_") else "_" for c in str(cid))
        out_path = os.path.join(out_dir, f"{safe}.mpd")

        ET.ElementTree(root).write(out_path, encoding="utf-8", xml_declaration=True)

        file_url = f"file:///app/data/mediaset_best/{safe}.mpd"
        print("SCRAPER: mediaset local best mpd", out_path, "->", file_url, "changed=", changed)
        return file_url
    except Exception as e:
        print("SCRAPER: mediaset_build_local_best_mpd error:", e)
        return None


def _resolve_mediaset_via_api(channel_id=None, channel_name=None, cookies=None, ua=None):
    code_upper = _mediaset_code_upper(channel_id, channel_name)
    if not code_upper:
        return None
    s = requests.Session()
    if cookies:
        s.headers.update({"Cookie": cookies})
    token, sid = _mediaset_login_requests(s, ua or DEFAULT_UA)
    ms = _mediaset_playback_check(s, code_upper, token, sid, ua or DEFAULT_UA)
    print("SCRAPER: after playback_check", ms, flush=True)
    print("SCRAPER: mediaset selector", ms)
    print("SCRAPER: before expand_media_selector", flush=True)
    picked, final_url, txt = _mediaset_expand_media_selector(s, ms, ua or DEFAULT_UA)
    print("SCRAPER: after expand_media_selector", {"picked": picked, "final_url": final_url}, flush=True)
    print("SCRAPER: mediaset selector final_url", final_url)
    print("SCRAPER: mediaset selector text", (txt or "")[:2000])
    if picked:
        print("SCRAPER: mediaset api picked", picked)
        print("SCRAPER: before mediaset_build_local_best_mpd", picked, flush=True)
        local_best = mediaset_build_local_best_mpd(picked, channel_id, channel_name)
        print("SCRAPER: after mediaset_build_local_best_mpd", local_best, flush=True)
        if local_best:
            return local_best
        return picked
    print("SCRAPER: mediaset api no manifest, selector url:", ms.get("url"))
    print("SCRAPER: mediaset api final url:", final_url)
    return None



def _extract_token_sid_from_driver(driver, timeout=15):
    import json, base64, time

    deadline = time.time() + timeout
    seen = set()

    while time.time() < deadline:
        try:
            logs = driver.get_log("performance")
        except Exception as e:
            print("SCRAPER: mediaset performance log error:", e)
            logs = []

        for entry in logs:
            try:
                msg = json.loads(entry["message"])["message"]
            except Exception:
                continue

            if msg.get("method") != "Network.responseReceived":
                continue

            params = msg.get("params", {})
            resp = params.get("response", {})
            u = resp.get("url", "")
            rid = params.get("requestId")

            if not u or not rid or "anonymous/login" not in u:
                continue

            key = (rid, u)
            if key in seen:
                continue
            seen.add(key)

            try:
                body = driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": rid})
                txt = body.get("body", "")
                if body.get("base64Encoded"):
                    txt = base64.b64decode(txt).decode("utf-8", "ignore")

                j = json.loads(txt)
                token = (j.get("response") or {}).get("beToken")
                sid = (j.get("response") or {}).get("sid")
                if token and sid:
                    print("SCRAPER: mediaset token/sid from browser OK")
                    return token, sid
            except Exception as e:
                print("SCRAPER: mediaset login body read error:", e)

        time.sleep(1)

    return None, None

def _mediaset_playback_check_with_token(session, code_upper, token, sid, ua):
    url = f"https://api-ott-prod-fe.mediaset.net/PROD/play/playback/check/v2.0?sid={sid}"
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Origin": "https://mediasetinfinity.mediaset.it",
        "Referer": "https://mediasetinfinity.mediaset.it/",
        "User-Agent": ua or DEFAULT_UA,
        "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Linux"',
    }
    payload = {
        "channelCode": code_upper,
        "streamType": "LIVE",
        "delivery": "Streaming",
        "createDevice": True,
        "overrideAppName": "web//mediasetplay-web/1.1.0-509a584",
    }
    r = session.post(url, headers=headers, json=payload, timeout=30)
    print("SCRAPER: mediaset playback status", r.status_code, code_upper, flush=True)
    print("SCRAPER: mediaset playback body", r.text[:1500])
    r.raise_for_status()
    j = r.json()
    ms = (j.get("response") or {}).get("mediaSelector") or {}
    if not ms or not ms.get("url"):
        raise RuntimeError(f"Mediaset playbackCheck senza mediaSelector: {j}")
    return ms

def resolve_mediaset_direct_impl(page_url, channel_name, get_sniffer_driver, get_cookies_string, get_user_agent, channel_id=None):
    print("SCRAPER: resolver.entry", {"page_url": page_url, "channel_name": channel_name, "channel_id": channel_id}, flush=True)
    print("SCRAPER: mediaset direct resolver")
    print("SCRAPER: mediaset inputs", {"page_url": page_url, "channel_name": channel_name, "channel_id": channel_id}, flush=True)
    deadline = time.time() + 35
    driver = get_sniffer_driver()
    try:
        try:
            driver.set_page_load_timeout(25)
        except Exception:
            pass

        driver.execute_cdp_cmd("Network.enable", {})

        try:
            driver.get(page_url)
        except Exception as e:
            print("SCRAPER: mediaset initial driver.get error:", e)
            return None, None, None

        time.sleep(2)

        try:
            driver.execute_script("document.querySelector('#cookie_accept_btn') && document.querySelector('#cookie_accept_btn').click();")
        except Exception:
            pass

        time.sleep(1)

        # forza l'avvio del player/live tile SOLO per il canale richiesto
        try:
            target_map = {
                "c5": '/diretta/canale5_cC5',
                "i1": '/diretta/italia1_cI1',
                "r4": '/diretta/rete4_cR4',
                "kf": '/diretta/tgcom24_cKF',
                "lt": '/diretta/topcrime_cLT',
                "lb": '/diretta/20mediaset_cLB',
                "ki": '/diretta/iris_cKI',
                "ka": '/diretta/la5_cKA',
                "kq": '/diretta/mediasetextra_cKQ',
                "fu": '/diretta/focus_cFU',
                "b6": '/diretta/cine34_cB6',
                "ts": '/diretta/twentyseven_cTS',
            }
            target_code = mediaset_channel_code(channel_id, channel_name)
            target_href = target_map.get(target_code)
            print("SCRAPER: mediaset target", {"target_code": target_code, "target_href": target_href, "channel_id": channel_id, "channel_name": channel_name}, flush=True)

            driver.execute_script("""
            const targetHref = arguments[0];

            const clickIf = (sel) => {
              const el = document.querySelector(sel);
              if (el) { el.click(); return true; }
              return false;
            };

            let done = false;

            if (targetHref) {
              done = done || clickIf(`a[href*="${targetHref}"]`);
            }

            // overlay/player container
            done = done || clickIf('div[class*="play"]');
            done = done || clickIf('.persistent-video-player');
            done = done || clickIf('.videoplayer-wrapper');
            done = done || clickIf('img[class*="object-fill"]');

            // fallback: click centro viewport
            if (!done) {
              const el = document.elementFromPoint(window.innerWidth/2, window.innerHeight/2);
              if (el) el.click();
            }
            """, target_href)
        except Exception as e:
            print("SCRAPER: mediaset click trigger error:", e)

        time.sleep(2)

        # Primo tentativo: usa token/sid presi dal browser reale
        try:
            if time.time() >= deadline:
                print("SCRAPER: mediaset deadline reached before token extraction")
                return None, None, None
            token, sid = _extract_token_sid_from_driver(driver)
            cookies = get_cookies_string(driver)
            ua = get_user_agent(driver)
            code_upper = _mediaset_code_upper(channel_id, channel_name)
            print("SCRAPER: resolver.code_upper", code_upper, {"channel_id": channel_id, "channel_name": channel_name}, flush=True)
            if token and sid and code_upper:
                try:
                    s = requests.Session()
                    if cookies:
                        s.headers.update({"Cookie": cookies})
                    ms = _mediaset_playback_check_with_token(s, code_upper, token, sid, ua)
                    print("SCRAPER: mediaset selector", ms)
                    code = mediaset_channel_code(channel_id, channel_name)
                    if code:
                        templ = f"https://live03-col.msf.cdn.mediaset.net/live/ch-{code}/{code}-clr.isml/manifest.mpd"
                        print("SCRAPER: mediaset direct clear template", templ)
                        return templ, cookies, ua
                    picked, final_url, txt = _mediaset_expand_media_selector(s, ms, ua)
                    print("SCRAPER: mediaset selector final_url", final_url)
                    print("SCRAPER: mediaset selector text", (txt or "")[:2000])
                    if picked:
                        print("SCRAPER: mediaset api picked", picked)
                        local_best = mediaset_build_local_best_mpd(picked, channel_id, channel_name)
                        if local_best:
                            print("SCRAPER: mediaset api picked local_best", local_best)
                            return local_best, cookies, ua
                        return picked, cookies, ua
                    else:
                        print("SCRAPER: mediaset browser-token flow found no clear manifest")
                except Exception as e:
                    print("SCRAPER: mediaset browser-token flow error:", e)
        except Exception as e:
            print("SCRAPER: mediaset browser preflight error:", e)

        urls = []
        graph_response_ids = []

        logs = driver.get_log("performance")
        for entry in logs:
            try:
                msg = json.loads(entry["message"])["message"]
            except Exception:
                continue

            method = msg.get("method")
            params = msg.get("params", {})

            if method == "Network.requestWillBeSent":
                req = params.get("request", {})
                u = req.get("url", "")
                if u:
                    urls.append(u)

            elif method == "Network.responseReceived":
                resp = params.get("response", {})
                u = resp.get("url", "")
                if u:
                    urls.append(u)
                if "mediasetplay.api-graph.mediaset.it" in u and "episodeId" in u:
                    rid = params.get("requestId")
                    if rid:
                        graph_response_ids.append(rid)

        manifest_candidates = [u for u in urls if ".m3u8" in u.lower() or ".mpd" in u.lower()]
        picked = _pick_best_clear(manifest_candidates)
        if picked:
            print("SCRAPER: mediaset picked manifest", picked)
            return picked, get_cookies_string(driver), get_user_agent(driver)
        elif manifest_candidates:
            print("SCRAPER: mediaset raw manifests only DRM/junk", manifest_candidates[:20])

        for req_id in graph_response_ids:
            try:
                body = driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": req_id})
                txt = body.get("body", "")
                if body.get("base64Encoded"):
                    txt = base64.b64decode(txt).decode("utf-8", "ignore")

                found = []
                for pat in [
                    r'https?://[^\s"\\]+\.m3u8[^\s"\\]*',
                    r'https?://[^\s"\\]+\.mpd[^\s"\\]*',
                ]:
                    found.extend(re.findall(pat, txt))

                picked = _pick_best_clear(found)
                if picked:
                    print("SCRAPER: mediaset graphql picked", picked)
                    return picked, get_cookies_string(driver), get_user_agent(driver)
                elif found:
                    print("SCRAPER: mediaset graphql only DRM/junk", found[:20])

                m = re.search(r'"value":"(https://mediasetinfinity\.mediaset\.it/video/[^"]+)"', txt)
                if m:
                    video_url = m.group(1).replace("\\/", "/")
                    print("SCRAPER: mediaset video page from graphql", video_url)
                    try:
                        driver.get(video_url)
                        time.sleep(8)
                        logs2 = driver.get_log("performance")
                        found2 = []
                        for ev in logs2:
                            try:
                                msgv = json.loads(ev["message"])["message"]
                                if msgv.get("method") == "Network.requestWillBeSent":
                                    uv = msgv["params"]["request"]["url"]
                                    if ".m3u8" in uv.lower() or ".mpd" in uv.lower():
                                        found2.append(uv)
                            except Exception:
                                pass
                        picked = _pick_best_clear(found2)
                        if picked:
                            print("SCRAPER: mediaset video page picked", picked)
                            return picked, get_cookies_string(driver), get_user_agent(driver)
                        elif found2:
                            print("SCRAPER: mediaset video page only DRM/junk", found2[:20])
                    except Exception as e:
                        print("SCRAPER: mediaset video-page error:", e)

            except Exception as e:
                print("SCRAPER: mediaset CDP read error:", e)

        code = mediaset_channel_code(channel_name)
        if code:
            templ = f"https://live03-col.msf.cdn.mediaset.net/live/ch-{code}/{code}-clr.isml/manifest.mpd"
            print("SCRAPER: mediaset template clear fallback", templ)
            local_best = mediaset_build_local_best_mpd(templ, channel_id, channel_name)
            if local_best:
                return local_best, None, None
            return templ, None, None
        print("SCRAPER: mediaset no usable clear fallback")
        print("SCRAPER: mediaset final decision -> None")
        return None, None, None

    except Exception as e:
        print("SCRAPER mediaset resolver error:", e)
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    return None, None, None
