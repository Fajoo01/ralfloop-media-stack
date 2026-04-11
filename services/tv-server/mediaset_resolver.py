import json
import time
import re
import base64

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
}

def _norm(x):
    return re.sub(r"[^a-z0-9]+", "", str(x or "").lower())

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
        f"https://live3-mediaset-it.akamaized.net/Content/hls_h0_clr_vos/live/channel({code})/index.m3u8",
        f"https://live2.msf.cdn.mediaset.net/content/hls_h0_clr_vos/live/channel({code})/index.m3u8",
        f"https://live2.msf.cdn.mediaset.net/content/hls_h0_cls_vos/live/channel({code})/index.m3u8",
        f"https://live03-col.msf.cdn.mediaset.net/live/ch-{code}/{code}-clr.isml/manifest.mpd",
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
    if "live3-mediaset-it.akamaized.net" in lu: s += 100
    if "live2.msf.cdn.mediaset.net" in lu: s += 90
    if "channel(" in lu: s += 50
    if "/index.m3u8" in lu: s += 40
    if "manifest.mpd" in lu: s += 20
    if ".m3u8" in lu: s += 10
    if ".mpd" in lu: s += 5
    if "drm" in lu: s -= 200
    if "audio" in lu or "_ao." in lu: s -= 100
    if "api-graph" in lu: s -= 300
    return s

def _pick_best(urls):
    urls = [u for u in urls if u]
    if not urls:
        return None
    return sorted(set(urls), key=_score_url, reverse=True)[0]

def resolve_mediaset_direct_impl(page_url, channel_name, get_sniffer_driver, get_cookies_string, get_user_agent, channel_id=None):
    print("SCRAPER: mediaset direct resolver")
    driver = get_sniffer_driver()
    try:
        driver.set_page_load_timeout(60)
        driver.execute_cdp_cmd("Network.enable", {})
        driver.get(page_url)
        time.sleep(8)

        try:
            driver.execute_script("document.querySelector('#cookie_accept_btn') && document.querySelector('#cookie_accept_btn').click();")
        except Exception:
            pass

        time.sleep(3)

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
        picked = _pick_best(manifest_candidates)
        if picked:
            print("SCRAPER: mediaset picked manifest", picked)
            return picked, get_cookies_string(driver), get_user_agent(driver)

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

                picked = _pick_best(found)
                if picked:
                    print("SCRAPER: mediaset graphql picked", picked)
                    return picked, get_cookies_string(driver), get_user_agent(driver)

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
                        picked = _pick_best(found2)
                        if picked:
                            print("SCRAPER: mediaset video page picked", picked)
                            return picked, get_cookies_string(driver), get_user_agent(driver)
                    except Exception as e:
                        print("SCRAPER: mediaset video-page error:", e)

            except Exception as e:
                print("SCRAPER: mediaset CDP read error:", e)

        for fallback in mediaset_fallback_candidates(channel_id, channel_name):
            print("SCRAPER: mediaset fallback", fallback)
            return fallback, get_cookies_string(driver), get_user_agent(driver)

    except Exception as e:
        print("SCRAPER mediaset resolver error:", e)
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    return None, None, None
