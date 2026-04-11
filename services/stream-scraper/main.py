import os
import json
import xml.etree.ElementTree as ET
import xml.etree.ElementTree as ET
import time
import base64
import requests
import re
from io import BytesIO
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
import sys as _sys
from pathlib import Path as _Path
_PLUGIN_DIR = _Path(__file__).resolve().parent
if str(_PLUGIN_DIR) not in _sys.path:
    _sys.path.insert(0, str(_PLUGIN_DIR))

try:
    from .mediaset_resolver import resolve_mediaset_direct_impl, mediaset_headers
except Exception:
    from mediaset_resolver import resolve_mediaset_direct_impl, mediaset_headers
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from cat.mad_hatter.decorators import tool, hook

print("\n\n ---> SCRAPER V80.7 (RETURN TUPLE FIX) <--- \n\n")

PLUGIN_DIR = os.path.dirname(__file__)
INPUT_FILE = os.path.join(PLUGIN_DIR, 'channels_src.json')
OUTPUT_FILE = os.path.join(PLUGIN_DIR, 'channels_out.json')
DEBUG_DIR = os.path.join(PLUGIN_DIR, 'debug_screenshots')

if not os.path.exists(DEBUG_DIR):
    os.makedirs(DEBUG_DIR)
GLOBAL_CAT = None

def get_settings():
    if GLOBAL_CAT:
        try:
            return GLOBAL_CAT.mad_hatter.get_plugin().load_settings()
        except:
            pass
    return {}

def get_sniffer_driver():
    import shutil

    options = Options()

    chrome_bin = (
        shutil.which("google-chrome")
        or shutil.which("chromium")
        or shutil.which("chromium-browser")
    )
    if chrome_bin:
        options.binary_location = chrome_bin

    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument("--mute-audio")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])

    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    options.add_experimental_option("perfLoggingPrefs", {"enableNetwork": True})

    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    options.add_argument(f'--user-agent={ua}')

    driver_path = shutil.which("chromedriver")
    if driver_path:
        service = Service(driver_path)
        driver = webdriver.Chrome(service=service, options=options)
    else:
        driver = webdriver.Chrome(options=options)

    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return driver
def save_full_screenshot(driver, prefix):
    try:
        timestamp = int(time.time())
        filename = f"{prefix}_{timestamp}.png"
        filepath = os.path.join(DEBUG_DIR, filename)
        driver.save_screenshot(filepath)
    except: pass

def get_cookies_string(driver):
    """Estrae i cookie in formato stringa"""
    try:
        cookies = driver.get_cookies()
        cookie_str = ""
        for c in cookies:
            cookie_str += f"{c['name']}={c['value']}; "
        return cookie_str
    except: return ""

def get_user_agent(driver):
    try: return driver.execute_script("return navigator.userAgent;")
    except: return "Mozilla/5.0"

def _extract_first_stream_url_from_text(txt):
    import re
    import json

    if not txt:
        return None

    s = str(txt).strip()

    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            u = obj.get("stream_url")
            if isinstance(u, str) and u.strip():
                return u.strip()
    except Exception:
        pass

    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            fa = obj.get("final_answer")
            if isinstance(fa, str) and fa.strip():
                try:
                    inner = json.loads(fa)
                    if isinstance(inner, dict):
                        u = inner.get("stream_url")
                        if isinstance(u, str) and u.strip():
                            return u.strip()
                except Exception:
                    pass
    except Exception:
        pass

    m = re.search(r'https?://[^\s"\']+\.(?:m3u8|mpd)[^\s"\']*', s, re.I)
    if m:
        return m.group(0)

    m = re.search(r'file:///[^\s"\']+\.mpd[^\s"\']*', s, re.I)
    if m:
        return m.group(0)

    return None

def _resolve_local_mpd_file_to_remote_manifest(url):
    import xml.etree.ElementTree as ET
    from pathlib import Path

    if not isinstance(url, str) or not url.startswith("file:///app/data/mediaset_best/"):
        return url

    filename = url.rsplit("/", 1)[-1]

    candidate_dirs = [
        Path("/app/cat/plugins/stream_scraper/mediaset_best"),
        Path("${HOME}/gatto/cat/plugins/stream_scraper/mediaset_best"),
    ]

    host_file = None

    for cand_dir in candidate_dirs:
        cand_file = cand_dir / filename
        if cand_file.exists():
            host_file = cand_file
            break

    if host_file is None and filename == "mediaset.mpd":
        for cand_dir in candidate_dirs:
            exact = cand_dir / "Canale5.mpd"
            if exact.exists():
                host_file = exact
                break

            generic = cand_dir / "mediaset.mpd"
            if generic.exists():
                host_file = generic
                break

            mpds = sorted(cand_dir.glob("*.mpd")) if cand_dir.exists() else []
            if len(mpds) == 1:
                host_file = mpds[0]
                break

            for cand in mpds:
                low = cand.name.lower()
                if "canale5" in low or "canale_5" in low or cand.stem.lower() in {"c5", "canale5"}:
                    host_file = cand
                    break
            if host_file is not None:
                break

    if host_file is None or not host_file.exists():
        print(f"SCRAPER: local mpd host file not found in candidates: {candidate_dirs}")
        return url

    try:
        print(f"SCRAPER: resolving local mpd from file: {host_file}")
        root = ET.fromstring(host_file.read_text(encoding="utf-8"))
        ns = {"mpd": "urn:mpeg:dash:schema:mpd:2011"}
        base = root.findtext("mpd:BaseURL", default="", namespaces=ns).strip()
        if base.startswith("http://") or base.startswith("https://"):
            remote = base.rstrip("/") + "/manifest.mpd"
            print(f"SCRAPER: resolved local mpd to remote manifest: {remote}")
            return remote
    except Exception as e:
        print(f"SCRAPER: resolve local mpd error: {e}")

    return url

def _call_ralfloop_stream_probe(channel_id, target):
    import json
    import requests

    source_page = target.get("source_page") or ""
    channel_name = target.get("name") or channel_id

    payload = {
        "user_goal": f"stream probe {channel_id}",
        "mode": "planner_coder_judge",
        "planner_model_profile": "generalist",
        "coder_model_profile": "coder",
        "judge_model_profile": "generalist",
        "planner_model_name": "qwen2.5:7b",
        "coder_model_name": "qwen2.5:7b",
        "judge_model_name": "qwen2.5:7b",
        "planner_rag_collection": "ralfloop_planner",
        "coder_rag_collection": "ralfloop_coder",
        "judge_rag_collection": "ralfloop_judge",
        "skill_context": json.dumps({
            "channel_id": channel_id,
            "channel_name": channel_name,
            "source_page": source_page,
            "type": target.get("type"),
            "group": target.get("group"),
        }, ensure_ascii=False),
        "extra_context": {},
    }

    try:
        print("SCRAPER: using Ralfloop HTTP /tasks/run")
        r = requests.post("http://127.0.0.1:19090/tasks/run", json=payload, timeout=180)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print("SCRAPER: Ralfloop HTTP error:", e)
        return None, None, None

    final_text = None
    try:
        fa = data.get("final_answer")
        if isinstance(fa, str) and fa.strip():
            final_text = fa.strip()
        else:
            final_text = json.dumps(data, ensure_ascii=False)
    except Exception:
        final_text = str(data)

    print("SCRAPER: Ralfloop raw output:", repr(final_text)[:1200])

    stream_url = _extract_first_stream_url_from_text(final_text)
    print(f"SCRAPER: stream_url before local resolve = {stream_url!r}")
    stream_url = _resolve_local_mpd_file_to_remote_manifest(stream_url)
    print(f"SCRAPER: stream_url after local resolve = {stream_url!r}")
    if not stream_url:
        print("SCRAPER: Ralfloop returned no parsable stream url")
        return None, None, None

    cookies = None
    ua = None
    try:
        if isinstance(final_text, str):
            inner = json.loads(final_text)
            if isinstance(inner, dict):
                headers = inner.get("headers") or {}
                if isinstance(headers, dict):
                    cookies = headers.get("Cookie")
                    ua = headers.get("User-Agent")
    except Exception as e:
        print("SCRAPER: Ralfloop headers parse error:", e)

    return stream_url, cookies, ua


# --- VISIONE E GRIGLIA ---
def draw_grid_overlay(driver, rows=6, cols=6):
    js = """
        var rows = %d; var cols = %d;
        if (document.getElementById('cat-vision-grid')) document.getElementById('cat-vision-grid').remove();
        var grid = document.createElement('div'); grid.id = 'cat-vision-grid';
        grid.style.position = 'fixed'; grid.style.top = '0'; grid.style.left = '0';
        grid.style.width = '100vw'; grid.style.height = '100vh'; grid.style.zIndex = '999999';
        grid.style.pointerEvents = 'none'; grid.style.display = 'grid';
        grid.style.gridTemplateColumns = 'repeat(' + cols + ', 1fr)';
        grid.style.gridTemplateRows = 'repeat(' + rows + ', 1fr)';
        var count = 1;
        for (var i = 0; i < rows * cols; i++) {
            var cell = document.createElement('div');
            cell.style.border = '2px dashed red';
            cell.style.color = 'yellow'; cell.style.fontSize = '30px'; cell.style.fontWeight = 'bold';
            cell.style.textShadow = '2px 2px 0px black'; cell.innerText = count++;
            grid.appendChild(cell);
        }
        document.body.appendChild(grid);
    """ % (rows, cols)
    driver.execute_script(js)
    time.sleep(1)

def remove_grid_overlay(driver):
    try:
        driver.execute_script("var g=document.getElementById('cat-vision-grid'); if(g) g.remove();")
    except: pass

def get_grid_coordinates(grid_number, rows=6, cols=6, width=1920, height=1080):
    if grid_number < 1 or grid_number > rows * cols: return None
    idx = grid_number - 1
    row = idx // cols
    col = idx % cols
    cell_w = width / cols; cell_h = height / rows
    return int((col * cell_w) + (cell_w / 2)), int((row * cell_h) + (cell_h / 2))




def discovery_accept_consent(driver):
    import time

    print("SCRAPER: discovery consent handler start")

    for _ in range(6):
        cur = ""
        try:
            cur = driver.current_url
        except:
            pass

        if "consent-wall" not in cur:
            print("SCRAPER: discovery no consent-wall")
            return True

        try:
            clicked = driver.execute_script("""
                const wanted = ["accetta tutti", "accept all"];
                const nodes = Array.from(document.querySelectorAll('button, a, [role="button"]'));
                for (const el of nodes) {
                    const txt = ((el.innerText || el.textContent || el.getAttribute('aria-label') || '')).trim().toLowerCase();
                    if (wanted.includes(txt)) {
                        el.click();
                        return txt;
                    }
                }
                return "";
            """)
            if clicked:
                print("SCRAPER: discovery consent clicked:", clicked)
                time.sleep(5)
            else:
                print("SCRAPER: discovery consent button not found yet")
                time.sleep(2)
        except Exception as e:
            print("SCRAPER: discovery consent click error:", e)
            time.sleep(2)

    return False

def discovery_extract_manifest_from_logs(driver):
    import json, re

    try:
        driver.execute_cdp_cmd("Network.enable", {})
    except:
        pass

    logs = driver.get_log("performance")
    reqs = []

    for entry in logs:
        try:
            msg = json.loads(entry["message"])["message"]
            if msg.get("method") != "Network.responseReceived":
                continue
            params = msg.get("params", {})
            resp = params.get("response", {})
            u = resp.get("url", "")
            rid = params.get("requestId")
            lu = u.lower()

            if any(x in lu for x in [
                "playbackinfo", "playback", "video", "stream", "manifest", "playlist",
                "uplynk", ".m3u8", ".mpd", "bolt", "cms/routes/channel/watch"
            ]):
                reqs.append((rid, u))
        except:
            pass

    print("SCRAPER: discovery manifest candidates:", len(reqs))

    for rid, u in reqs:
        print("SCRAPER: discovery candidate url:", u)
        try:
            body = driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": rid})
            txt = body.get("body","")

            m = re.search(r'https?://[^\s"\\]+\.m3u8[^\s"\\]*', txt)
            if m:
                hls = m.group(0)
                print("SCRAPER: discovery HLS from body", hls)
                return hls

            m = re.search(r'https?://[^\s"\\]+\.mpd[^\s"\\]*', txt)
            if m:
                mpd = m.group(0)
                print("SCRAPER: discovery MPD from body", mpd)
                return mpd

            m = re.search(r'https?://[^\s"\\]*uplynk\.com/[^\s"\\]*', txt)
            if m:
                up = m.group(0)
                print("SCRAPER: discovery UPLYNK from body", up)
                return up

        except Exception as e:
            print("SCRAPER: discovery body read error:", e)

    return None


def discovery_extract_watch_url(driver, channel_name):
    import json

    try:
        el = driver.find_element("xpath", "//script[@id='__NEXT_DATA__']")
        data = json.loads(el.get_attribute("innerHTML"))
    except Exception as e:
        print("SCRAPER: discovery next_data error:", e)
        return None

    wanted = channel_name.lower().strip()

    def walk(obj):
        if isinstance(obj, dict):
            alt = str(obj.get("imageAltText", "")).lower()
            link = obj.get("imageUrlLink")
            if wanted in alt and link:
                return link
            for v in obj.values():
                r = walk(v)
                if r:
                    return r
        elif isinstance(obj, list):
            for v in obj:
                r = walk(v)
                if r:
                    return r
        return None

    out = walk(data)
    if out:
        print("SCRAPER: discovery watch url from next_data:", out)
    else:
        print("SCRAPER: discovery watch url not found in next_data")
    return out


def discovery_human_click(driver, channel_name):
    import time
    from selenium.webdriver.common.action_chains import ActionChains

    target = channel_name.lower().strip()

    js_find = """
    const target = arguments[0].toLowerCase();
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    function visibleEnough(r) {
        return r.width >= 40 && r.height >= 25 &&
               r.width <= vw * 0.80 &&
               r.height <= vh * 0.35 &&
               r.bottom > 0 && r.right > 0 &&
               r.top < vh && r.left < vw;
    }

    const nodes = Array.from(document.querySelectorAll('a, button, [role="button"], article, [data-testid], .card, .tile, .item, .channel'));
    const out = [];

    for (const el of nodes) {
        const txt = (
            (el.innerText || '') + ' ' +
            (el.getAttribute('aria-label') || '') + ' ' +
            (el.getAttribute('title') || '') + ' ' +
            (el.getAttribute('href') || '') + ' ' +
            (el.getAttribute('alt') || '')
        ).toLowerCase().trim();

        if (!txt.includes(target)) continue;

        const r = el.getBoundingClientRect();
        if (!visibleEnough(r)) continue;

        let clickable = el;
        const childLink = el.querySelector('a[href], button');
        if (childLink) clickable = childLink;

        const rc = clickable.getBoundingClientRect();
        if (!visibleEnough(rc)) continue;

        out.push({
            text: txt.slice(0, 180),
            x: rc.left + rc.width / 2,
            y: rc.top + rc.height / 2,
            width: rc.width,
            height: rc.height,
            href: clickable.getAttribute('href') || el.getAttribute('href') || '',
            tag: clickable.tagName
        });
    }

    return out;
    """

    for frac in [0.20, 0.35, 0.50, 0.65, 0.80]:
        try:
            driver.execute_script(f"window.scrollTo({{top: document.body.scrollHeight*{frac}, behavior: 'smooth'}});")
        except:
            driver.execute_script(f"window.scrollTo(0, document.body.scrollHeight*{frac});")
        time.sleep(2.5)

        try:
            matches = driver.execute_script(js_find, target)
        except Exception as e:
            print("SCRAPER: discovery js find error:", e)
            matches = []

        print(f"SCRAPER: discovery human candidates at scroll {frac}: {len(matches)}")

        if not matches:
            continue

        def score(m):
            s = 0
            text = (m.get("text") or "").lower()
            href = (m.get("href") or "").lower()
            w = m.get("width", 0)
            h = m.get("height", 0)

            s += min(w * h, 50000)
            if target in text: s += 30000
            if "/channel/" in href or "/watch/" in href: s += 50000
            if "nove" in href: s += 40000
            if len(text) < 120: s += 15000
            if len(text) > 250: s -= 30000
            return s

        ranked = sorted(matches, key=score, reverse=True)
        best = ranked[0]
        print("SCRAPER: discovery best candidate:", best)

        try:
            x = int(best["x"])
            y = int(best["y"])

            driver.execute_script("""
                const x = arguments[0], y = arguments[1];
                const el = document.elementFromPoint(x, y);
                if (el) {
                    el.dispatchEvent(new MouseEvent('mousemove', {bubbles:true, clientX:x, clientY:y}));
                    el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, clientX:x, clientY:y}));
                    el.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, clientX:x, clientY:y}));
                    el.click();
                    return true;
                }
                return false;
            """, x, y)

            print(f"SCRAPER: discovery js precise click at ({x},{y})")
            time.sleep(8)
            return True

        except Exception as e:
            print("SCRAPER: discovery precise click failed:", e)

    return False

def ask_ollama_grid(screenshot_b64, channel_name):
    settings = get_settings()
    ollama_url = settings.get("ollama_url", "http://10.252.14.7:11434")
    model = settings.get("vision_model", "deepseek-ocr:latest").replace("ollama pull ", "").strip()
    
    print(f"   🧠 Vision AI: Chiedo a '{model}' il numero per '{channel_name}'...")
    prompt = f"""Look at the screenshot with the grid. Find the logo for '{channel_name}'. Return JSON: {{"grid_number": 15}}"""
    
    try:
        r = requests.post(f"{ollama_url}/api/chat", json={
            "model": model, 
            "messages": [{"role": "user", "content": prompt, "images": [screenshot_b64]}], 
            "stream": False, 
            "format": "json"
        }, timeout=40)
        if r.status_code == 200:
            return json.loads(r.json().get("message", {}).get("content", "{}")).get("grid_number", 0)
    except: pass
    return 0

def magnetic_click(driver, x, y):
    print(f"      🧲 Magnetic Click su ({x}, {y})...")
    js = """
        var x = arguments[0]; var y = arguments[1];
        var el = document.elementFromPoint(x, y);
        if(el) { 
            el.click(); 
            if(el.parentElement && el.parentElement.tagName === 'A') el.parentElement.click(); 
            return true; 
        }
        return false;
    """
    return driver.execute_script(js, x, y)

def check_logs_for_stream(driver):
    try:
        logs = driver.get_log('performance')
        for entry in logs:
            m = json.loads(entry['message'])['message']
            if m['method'] == 'Network.requestWillBeSent':
                u = m['params']['request']['url']
                if "uplynk" in u and ".m3u8" in u: return u
                if ".m3u8" in u:
                    if "msf.cdn" in u and "audio" not in u: return u
                    if "relinker" in u: return u
    except: pass
    return None

def grid_clicker_and_get_url(driver, channel_name):
    driver.execute_script("window.scrollTo(0, 600);")
    time.sleep(2)
    
    draw_grid_overlay(driver)
    b64 = driver.get_screenshot_as_base64()
    grid_num = ask_ollama_grid(b64, channel_name)
    remove_grid_overlay(driver)
    
    if grid_num > 0:
        coords = get_grid_coordinates(grid_num)
        if coords:
            x, y = coords
            magnetic_click(driver, x, y)
            print("      ⏳ Attesa cambio pagina (10s)...")
            time.sleep(10)
            
            # PRENDIAMO L'URL DELLA PAGINA
            current_url = driver.current_url
            print(f"      🌍 Pagina raggiunta: {current_url}")
            
            if "watch" in current_url:
                print("      ✅ URL Watch trovato! Salvo questo per il server.")
                # FIX: Ritorniamo la tripla (URL, Cookies, UA)
                return current_url, get_cookies_string(driver), get_user_agent(driver)
                
    return None, None, None

def extract_rai_id_from_page(driver, channel_name):
    # Logica Rai
    try:
        html = driver.page_source
        match = re.search(r'"contentId"\s*:\s*"?(\d+)"?', html)
        if match: return f"https://mediapolis.rai.it/relinker/relinkerServlet.htm?cont={match.group(1)}&output=54", None, None
    except: pass
    
    mapping = {"rai1": "308718", "rai2": "308702", "rai3": "308709", "rai4": "746966", "rai5": "395276", "raimovie": "746974", "raipremium": "746976", "raigulp": "746992", "raiyoyo": "746899", "raistoria": "746963", "rainews24": "746953", "raisport": "746990", "raiscuola": "12181"}
    cn = channel_name.lower().replace(" ", "")
    for k, v in mapping.items():
        if k in cn: return f"https://mediapolis.rai.it/relinker/relinkerServlet.htm?cont={v}&output=54", None, None
    
    return None, None, None



def resolve_mediaset_direct(page_url, channel_name, channel_id=None):
    print("SCRAPER: main.resolve_mediaset_direct", {"page_url": page_url, "channel_name": channel_name, "channel_id": channel_id}, flush=True)
    return resolve_mediaset_direct_impl(
        page_url,
        channel_name,
        get_sniffer_driver,
        get_cookies_string,
        get_user_agent,
        channel_id,
    )


def _rai_http_headers():
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Referer": "https://www.raiplay.it/",
        "Origin": "https://www.raiplay.it",
    }

def _rai_extract_m3u8_from_text(txt):
    if not txt:
        return None

    patterns = [
        r"https://[^\"' ]+\.m3u8[^\"' ]*",
        r"https:\\/\\/[^\"' ]+\.m3u8[^\"' ]*",
    ]

    for pat in patterns:
        m = re.search(pat, txt)
        if m:
            u = m.group(0).replace("\\/", "/")
            if ".m3u8" in u:
                return u
    return None


def sniff_video_url(driver, page_url, channel_name, channel_id=None):
    print(f"🔍 Scansione: {page_url}")
    try:
        if "mediaset" in (page_url or "").lower() and driver is None:
            return resolve_mediaset_direct(page_url, channel_name, channel_id=channel_id)
        driver.set_page_load_timeout(60)
        driver.get(page_url)
        time.sleep(3)
        
        # DISCOVERY
        if "discovery" in page_url or "realtime" in page_url or "nove" in page_url or "play.discoveryplus.com" in page_url:
            print("SCRAPER: provider discovery")

            try:
                driver.execute_script("document.querySelector('#onetrust-accept-btn-handler') && document.querySelector('#onetrust-accept-btn-handler').click();")
            except:
                pass

            time.sleep(5)

            print("SCRAPER: discovery title:", driver.title)
            print("SCRAPER: discovery current url:", driver.current_url)

            # Se siamo già su channel/watch, NON cercare __NEXT_DATA__ sulla consent-wall
            watch_url = None
            cur = ""
            try:
                cur = driver.current_url
            except:
                pass

            if "play.discoveryplus.com/channel/watch/" in page_url:
                watch_url = page_url
                print("SCRAPER: discovery using direct watch url from source_page:", watch_url)
            elif "play.discoveryplus.com/channel/watch/" in cur:
                watch_url = cur
                print("SCRAPER: discovery using direct watch url from current_url:", watch_url)
            else:
                try:
                    watch_url = discovery_extract_watch_url(driver, channel_name)
                except Exception as e:
                    print("SCRAPER: discovery watch extraction error:", e)

            if watch_url and "channel/watch" in watch_url and driver.current_url != watch_url:
                try:
                    driver.get(watch_url)
                    time.sleep(5)
                except Exception as e:
                    print("SCRAPER: discovery driver.get watch url error:", e)

            consent_ok = discovery_accept_consent(driver)
            print("SCRAPER: discovery consent result:", consent_ok)
            time.sleep(6)

            print("SCRAPER: discovery after consent title:", driver.title)
            print("SCRAPER: discovery after consent url:", driver.current_url)

            manifest = discovery_extract_manifest_from_logs(driver)
            if manifest:
                print("SCRAPER: discovery manifest extracted directly")
                return manifest, get_cookies_string(driver), get_user_agent(driver)

            fallback_url = driver.current_url
            print("SCRAPER: discovery fallback url:", fallback_url)
            return fallback_url, get_cookies_string(driver), get_user_agent(driver)

        # RAI
        elif "rai" in page_url:
            try:
                driver.execute_script("document.querySelector('#onetrust-accept-btn-handler').click();")
            except: pass
            time.sleep(5)

            # prova prima i log
            url = check_logs_for_stream(driver)
            if url:
                return url, None, None

            # fallback: cerca cont=... direttamente nell'html
            import re
            html = driver.page_source
            conts = re.findall(r'cont=([A-Za-z0-9_-]+)', html)
            if conts:
                cont_id = conts[0]
                relinker = f"https://mediapolis.rai.it/relinker/relinkerServlet.htm?cont={cont_id}&output=54"
                print(f"SCRAPER: rai cont fallback {cont_id} -> {relinker}")
                return relinker, None, None

            return extract_rai_id_from_page(driver, channel_name)

        # MEDIASET
        elif "mediaset" in page_url:
            return resolve_mediaset_direct(page_url, channel_name, channel_id=channel_id)
            print("SCRAPER: provider mediaset")
            try:
                try:
                    driver.execute_script("document.querySelector('#cookie_accept_btn') && document.querySelector('#cookie_accept_btn').click();")
                except:
                    pass

                # Mediaset: attendi davvero che il player e le richieste di rete partano
                time.sleep(10)

                print("SCRAPER: page title =", driver.title)
                print("SCRAPER: current url =", driver.current_url)
                logs = driver.get_log("performance")
                urls = []

                for entry in logs:
                    try:
                        msg = json.loads(entry["message"])["message"]
                        if msg.get("method") == "Network.requestWillBeSent":
                            url = msg["params"]["request"]["url"]
                            urls.append(url)
                    except:
                        pass

                print(f"SCRAPER: urls intercettati {len(urls)}")
                for dbg_u in urls:
                    dbg_l = dbg_u.lower()
                    if any(x in dbg_l for x in [".m3u8", ".mpd", "manifest", "playlist", "mediaset", "theplatform", "video", "play", "live", "stream"]):
                        print(f"SCRAPER URL: {dbg_u}")

                for u in urls:
                    if ".m3u8" in u or ".mpd" in u:
                        print(f"SCRAPER: stream trovato {u}")
                        return u, None, None

                # fallback mediaset: cerca GraphQL con episodeId e leggi la response via CDP
                for u in urls:
                    if "mediasetplay.api-graph.mediaset.it" in u and "episodeId" in u:
                        print(f"SCRAPER: graphql trovato {u}")

                        import json, re

                        for entry2 in logs:
                            try:
                                msg2 = json.loads(entry2["message"])["message"]
                            except Exception:
                                continue

                            if msg2.get("method") != "Network.responseReceived":
                                continue

                            params2 = msg2.get("params", {})
                            resp2 = params2.get("response", {})
                            url_resp = resp2.get("url", "")

                            if "mediasetplay.api-graph.mediaset.it" not in url_resp:
                                continue

                            req_id = params2.get("requestId")
                            if not req_id:
                                continue

                            try:
                                body = driver.execute_cdp_cmd(
                                    "Network.getResponseBody",
                                    {"requestId": req_id}
                                )
                                txt = body.get("body","")
                                print("SCRAPER: graphql body start")
                                print(txt[:2000])

                                m = re.search(r'https?://[^\s"\\]+\.m3u8[^\s"\\]*', txt)
                                if m:
                                    hls = m.group(0)
                                    print(f"SCRAPER: HLS da graphql {hls}")
                                    return hls, None, None

                                m = re.search(r'https?://[^\s"\\]+\.mpd[^\s"\\]*', txt)
                                if m:
                                    mpd = m.group(0)
                                    print(f"SCRAPER: MPD da graphql {mpd}")
                                    return mpd, None, None

                            except Exception as e:
                                print(f"SCRAPER: CDP read error {e}")

                        return u, None, None

                url = check_logs_for_stream(driver)
                if url:
                    return url, None, None

            except Exception as e:
                print(f"SCRAPER mediaset errore: {e}")

    except Exception as e:
        print(f"❌ Errore Scraper: {e}")
    return None, None, None



# === RAI AUTO HELPERS ===
def _rai_norm(x):
    import re
    return re.sub(r"[^a-z0-9]+", "", str(x or "").lower())

def _rai_load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"channels": []}

def _rai_save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _rai_guess_page_url(channel_id):
    n = _rai_norm(channel_id)
    direct = {
        "rai1": "https://www.raiplay.it/dirette/rai1",
        "rai2": "https://www.raiplay.it/dirette/rai2",
        "rai3": "https://www.raiplay.it/dirette/rai3",
        "rai4": "https://www.raiplay.it/dirette/rai4",
        "rai 4": "https://www.raiplay.it/dirette/rai4",
        "rainews24": "https://www.raiplay.it/dirette/rainews24",
        "rainew24": "https://www.raiplay.it/dirette/rainews24",
        "rainews": "https://www.raiplay.it/dirette/rainews24",
    }
    page = direct.get(n)
    if page:
        return page

    for _path in (OUTPUT_FILE, INPUT_FILE):
        try:
            _data = _rai_load_json(_path)
            for _ch in _data.get("channels", []):
                _cid = _rai_norm(_ch.get("id"))
                _src = str(_ch.get("source_page") or "").strip()
                if _cid == n and "raiplay.it/dirette/" in _src:
                    print(f"SCRAPER: rai auto page from source_page for {channel_id} -> {_src}")
                    return _src
        except Exception:
            pass

    return None

def _rai_choose_best_chunk(candidates):
    def score(item):
        url = str(item.get("url", "")).lower()
        mime = str(item.get("mimeType", "")).lower()
        s = 0

        if "chunklist.m3u8" in url:
            s += 100
        elif "playlist_ma.m3u8" in url:
            s += 70
        elif "playlist.m3u8" in url:
            s += 60
        elif ".m3u8" in url:
            s += 20

        if "_ao.m3u8" in url or "audio" in url:
            s -= 200

        if "_2400/" in url or "_2500/" in url or "_3000/" in url:
            s += 30

        if "rainews" in url or "news24" in url:
            s += 30

        if "akamaized.net" in url:
            s += 20

        if item.get("status") == 200:
            s += 20

        if mime in ("application/vnd.apple.mpegurl", "application/x-mpegurl"):
            s += 20

        return s

    if not candidates:
        return None
    return sorted(candidates, key=score, reverse=True)[0]

def _rai_sanitize_headers(h):
    out = {}
    preferred = [
        "User-Agent",
        "Referer",
        "Origin",
        "CMCD-Object",
        "CMCD-Request",
        "CMCD-Session",
        "CMCD-Status",
        "sec-ch-ua",
        "sec-ch-ua-mobile",
        "sec-ch-ua-platform",
    ]
    for k in preferred:
        v = h.get(k)
        if v is not None:
            out[k] = v

    if "User-Agent" not in out:
        out["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    if "Referer" not in out:
        out["Referer"] = "https://www.raiplay.it/"
    return out

def _rai_refresh_channel(channel_id):
    import time
    page_url = _rai_guess_page_url(channel_id)
    if not page_url:
        print(f"SCRAPER: rai auto no page for {channel_id}")
        return False

    slug = page_url.rstrip("/").split("/")[-1]
    json_link = f"/dirette/{slug}.json"

    print(f"SCRAPER: rai auto refresh start: {channel_id} -> {page_url}")

    driver = get_sniffer_driver()
    try:
        driver.set_script_timeout(180)
        driver.execute_cdp_cmd("Network.enable", {})
        driver.get(page_url)
        time.sleep(8)

        js_result = driver.execute_async_script("""
const jsonLink = arguments[0];
const done = arguments[arguments.length - 1];

(async () => {
  const rp = document.querySelector('rai-player');
  if (!rp) return done({ok:false, error:"rai-player not found"});

  const r = await fetch(jsonLink, {credentials: "include"});
  if (!r.ok) return done({ok:false, error:"json fetch failed", status:r.status});

  const videoJson = await r.json();

  rp.hasOptin = true;
  rp.privacyConsent = true;
  rp.tcString = "forced_tc_string";
  window.WtInstance = window.WtInstance || {};

  const opts = await rp._getPlayerOptionsFromVideoJson(videoJson, null);
  try { opts.advertisingUri = ""; } catch(e) {}
  try { opts.autoplay = true; } catch(e) {}
  try { opts.startFrom = 0; } catch(e) {}

  rp._getPlayerOptions = async function() { return opts; };
  rp.openPlayer({jsonLink: jsonLink});

  await new Promise(r => setTimeout(r, 25000));

  done({
    ok: true,
    mediaUri: opts && opts.mediaUri ? opts.mediaUri : null,
    videos: Array.from(document.querySelectorAll("video")).map(v => ({
      src: v.src,
      currentSrc: v.currentSrc,
      paused: v.paused
    }))
  });
})().catch(e => done({ok:false, error:String(e)}));
""", json_link)

        print("SCRAPER: rai auto js result:", js_result)

        logs = driver.get_log("performance")
        print("SCRAPER: rai auto raw performance logs:", len(logs))
        requests = {}
        responses = {}

        for entry in logs:
            try:
                msg = json.loads(entry["message"])["message"]
                method = msg.get("method")
                params = msg.get("params", {})

                if method == "Network.requestWillBeSent":
                    rid = params.get("requestId")
                    req = params.get("request", {})
                    requests[rid] = {
                        "url": req.get("url", ""),
                        "method": req.get("method", ""),
                        "headers": req.get("headers", {}),
                    }

                elif method == "Network.responseReceived":
                    rid = params.get("requestId")
                    resp = params.get("response", {})
                    responses[rid] = {
                        "url": resp.get("url", ""),
                        "status": resp.get("status"),
                        "mimeType": resp.get("mimeType"),
                        "headers": resp.get("headers", {}),
                    }
            except Exception:
                pass

        print("SCRAPER: rai auto request map size:", len(requests))
        print("SCRAPER: rai auto response map size:", len(responses))

        for rid, req in list(requests.items())[:500]:
            u = str(req.get("url", ""))
            lu = u.lower()
            if any(x in lu for x in ["rai.it", "raiplay.it", "mediapolis", "msvdn.net", ".m3u8", ".ts", "serve.ts"]):
                print("SCRAPER: rai raw req:", req.get("method"), u[:260])

        for rid, resp in list(responses.items())[:500]:
            u = str(resp.get("url", ""))
            lu = u.lower()
            if any(x in lu for x in ["rai.it", "raiplay.it", "mediapolis", "msvdn.net", ".m3u8", ".ts", "serve.ts"]):
                print("SCRAPER: rai raw resp:", resp.get("status"), resp.get("mimeType"), u[:260])

        candidates = []
        for rid, req in requests.items():
            url = req.get("url", "")
            lu = url.lower()
            if req.get("method") != "GET":
                continue
            if ("msvdn.net" not in lu) and ("akamaized.net" not in lu):
                continue
            if ".m3u8" not in lu:
                continue
            if not any(x in lu for x in ["chunklist", "playlist", "/hls/"]):
                continue

            row = {
                "requestId": rid,
                "url": url,
                "req_headers": req.get("headers", {}),
                "status": responses.get(rid, {}).get("status"),
                "mimeType": responses.get(rid, {}).get("mimeType"),
            }
            candidates.append(row)

        print("SCRAPER: rai auto candidates count:", len(candidates))
        for c in candidates[:20]:
            print("SCRAPER: rai candidate:", c["status"], c["mimeType"], c["url"][:220])

        best = _rai_choose_best_chunk(candidates)
        if not best:
            print("SCRAPER: rai auto no chunklist candidate found")

            page_txt = ""
            try:
                page_txt = driver.page_source or ""
            except Exception as e:
                print(f"SCRAPER: rai fallback driver page_source error: {e}")

            resolved = _rai_extract_m3u8_from_text(page_txt)

            if not resolved:
                try:
                    r = requests.get(
                        target["source_page"],
                        headers=_rai_http_headers(),
                        timeout=15,
                        allow_redirects=True,
                    )
                    resolved = _rai_extract_m3u8_from_text(r.text)
                except Exception as e:
                    print(f"SCRAPER: rai fallback page fetch error: {e}")

            if resolved and ("relinker" in resolved or "mediapolis" in resolved):
                try:
                    rr = requests.get(
                        resolved,
                        headers=_rai_http_headers(),
                        timeout=15,
                        allow_redirects=True,
                    )
                    resolved2 = _rai_extract_m3u8_from_text(rr.text)
                    if resolved2:
                        resolved = resolved2
                except Exception as e:
                    print(f"SCRAPER: rai relinker resolve error: {e}")

            if not resolved:
                print("SCRAPER: rai fallback failed")
                return False

            print(f"SCRAPER: rai fallback final m3u8: {resolved}")
            fresh_url = resolved
            fresh_headers = _rai_http_headers()
        else:
            fresh_url = best["url"]
            fresh_headers = _rai_sanitize_headers(best["req_headers"])

        out = _rai_load_json(OUTPUT_FILE)
        found = False
        for ch in out.get("channels", []):
            if str(ch.get("id", "")).lower() == str(channel_id).lower():
                ch["stream_url"] = fresh_url
                ch["headers"] = fresh_headers
                found = True
                break

        if not found:
            src = _rai_load_json(INPUT_FILE).get("channels", [])
            target = next((c for c in src if str(c.get("id", "")).lower() == str(channel_id).lower()), None)
            if target:
                row = dict(target)
            else:
                row = {"id": channel_id, "name": channel_id}
            row["stream_url"] = fresh_url
            row["headers"] = fresh_headers
            out.setdefault("channels", []).append(row)

        _rai_save_json(OUTPUT_FILE, out)
        print(f"💾 SALVATO RAI AUTO: {channel_id} -> {fresh_url[:140]}...")
        return True

    except Exception as e:
        print(f"SCRAPER: rai auto refresh error for {channel_id}: {e}")
        return False
    finally:
        try:
            driver.quit()
        except Exception:
            pass



def _mediaset_file_url_to_remote_base(url: str):
    try:
        prefix = "file:///app/data/mediaset_best/"
        if not isinstance(url, str) or not url.startswith(prefix):
            return url

        rel = url[len(prefix):]
        candidates = [
            "/app/data/mediaset_best/" + rel,
            "${HOME}/gatto/cat/plugins/stream_scraper/mediaset_best/" + rel,
        ]

        path = None
        for c in candidates:
            if _Path(c).exists():
                path = c
                break
        if not path:
            return url

        root = ET.parse(path).getroot()
        ns = {"mpd": "urn:mpeg:dash:schema:mpd:2011"}
        b = root.find("mpd:BaseURL", ns)
        if b is not None and b.text:
            return b.text.strip()
        return url
    except Exception:
        return url


def _mediaset_file_url_to_manifest(url: str):
    try:
        prefix = "file:///app/data/mediaset_best/"
        if not isinstance(url, str) or not url.startswith(prefix):
            return url

        rel = url[len(prefix):]
        candidates = [
            "/app/data/mediaset_best/" + rel,
            "${HOME}/gatto/cat/plugins/stream_scraper/mediaset_best/" + rel,
        ]

        path = None
        for c in candidates:
            if _Path(c).exists():
                path = c
                break
        if not path:
            return url

        root = ET.parse(path).getroot()
        ns = {"mpd": "urn:mpeg:dash:schema:mpd:2011"}
        b = root.find("mpd:BaseURL", ns)
        if b is None or not b.text:
            return url
        base = b.text.strip().rstrip("/")
        return base + "/manifest.mpd"
    except Exception:
        return url


def update_single_channel(channel_id):
    print(f"⚡ UPDATE: {channel_id}")
    if not os.path.exists(INPUT_FILE):
        return False

    with open(INPUT_FILE, 'r') as f:
        src = json.load(f).get("channels", [])

    target = next((c for c in src if c["id"].lower() == channel_id.lower()), None)
    if not target:
        return False

    source_page = str(target.get("source_page", "")).lower()
    channel_norm = _rai_norm(channel_id)

    if "raiplay.it/dirette/" in source_page or channel_norm in {"rai1", "rai2", "rai3", "rai4", "rai5", "raimovie", "raipremium", "raigulp", "raiyoyo", "raistoria", "rainews24", "rainew24", "rainews", "raisport", "raiscuola"}:
        print(f"SCRAPER: forced rai auto path for {channel_id}")
        ok = _rai_refresh_channel(channel_id)
        if ok:
            return True
        print(f"SCRAPER: rai auto failed, fallback old path for {channel_id}")

    new_url, cookies, ua = _call_ralfloop_stream_probe(channel_id, target)
    print(f"SCRAPER: RALFLOOP_RESULT new_url={new_url!r}")

    if not new_url:
        print("SCRAPER: FALLBACK sniff_video_url")
        driver = get_sniffer_driver()
        sniff_name = target["name"]
        if "mediaset" in source_page:
            sniff_name = target["id"]
            print(f"SCRAPER: mediaset using id for resolver: {sniff_name}")
        new_url, cookies, ua = sniff_video_url(driver, target["source_page"], sniff_name)
        driver.quit()
    else:
        print("SCRAPER: RALFLOOP_PATH_CONFIRMED")

    if new_url:
        new_url = _mediaset_file_url_to_manifest(new_url)
        new_url = _mediaset_file_url_to_remote_base(new_url)

        save_url = new_url
        try:
            _sp = str(target.get("source_page", "") or "")
            _cid = str(channel_id or "").strip().lower()
            _is_discovery = _cid in {
                "realtime", "real time", "nove", "dmax", "giallo",
                "motortrend", "motor trend", "foodnetwork", "food network",
                "k2", "frisbee"
            }
            if _is_discovery and "discoveryplus.com" in _sp:
                save_url = _sp
                print(f"SCRAPER: DISCOVERY save source_page instead of final url -> {save_url!r}")
        except Exception as e:
            print(f"SCRAPER: DISCOVERY save_url fallback due to error: {e}")

        print(f"💾 SALVATO: {channel_id} -> {save_url[:40]}...")
        out = {"channels": [dict(c) for c in src]}

        if os.path.exists(OUTPUT_FILE):
            try:
                with open(OUTPUT_FILE, 'r') as f:
                    old_out = json.load(f).get("channels", [])
                old_map = {c["id"].lower(): c for c in old_out if c.get("id")}
                for i, c in enumerate(out["channels"]):
                    cid = c.get("id", "").lower()
                    if cid in old_map:
                        merged = dict(c)
                        merged.update(old_map[cid])
                        out["channels"][i] = merged
            except:
                pass

        headers = {}
        if ua:
            headers["User-Agent"] = ua
        else:
            headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

        if cookies:
            headers["Cookie"] = cookies

        if "mediaset" in save_url or "akamaized" in save_url or "msf.cdn" in save_url:
            headers.update(mediaset_headers(cookies, ua))
        elif "discovery" in target["source_page"]:
            headers["Referer"] = "https://www.discoveryplus.com/"
            headers["Origin"] = "https://www.discoveryplus.com"
        elif "rai" in target["source_page"]:
            headers["Referer"] = "https://www.raiplay.it/"

        found = False
        for i, c in enumerate(out["channels"]):
            if c["id"].lower() == channel_id.lower():
                out["channels"][i].update({"stream_url": save_url, "headers": headers})
                found = True
                break
        if not found:
            target.update({"stream_url": save_url, "headers": headers})
            out["channels"].append(target)

        with open(OUTPUT_FILE, 'w') as f:
            json.dump(out, f, indent=2)
        return True

    return False

router = APIRouter()

@router.post("/probe/{channel_id}")
async def api_probe_channel(channel_id: str):
    if not os.path.exists(INPUT_FILE):
        return {"ok": False, "error": "input_file_missing"}

    with open(INPUT_FILE, 'r') as f:
        src = json.load(f).get("channels", [])

    target = next((c for c in src if c["id"].lower() == channel_id.lower()), None)
    if not target:
        return {"ok": False, "error": "channel_not_found", "channel_id": channel_id}

    source_page = str(target.get("source_page", "")).lower()
    sniff_name = target["name"]
    if "mediaset" in source_page:
        sniff_name = target["id"]

    driver = None
    try:
        driver = get_sniffer_driver()
        new_url, cookies, ua = sniff_video_url(driver, target["source_page"], sniff_name)
    finally:
        try:
            if driver:
                driver.quit()
        except Exception:
            pass

    if not new_url:
        return {
            "ok": False,
            "channel_id": channel_id,
            "source_page": target.get("source_page"),
            "stream_url": None,
            "headers": {},
        }

    try:
        new_url = _mediaset_file_url_to_manifest(new_url)
    except Exception:
        pass

    try:
        new_url = _mediaset_file_url_to_remote_base(new_url)
    except Exception:
        pass

    headers = {}
    if ua:
        headers["User-Agent"] = ua
    if cookies:
        headers["Cookie"] = cookies

    if "mediaset" in save_url or "akamaized" in save_url or "msf.cdn" in save_url:
        headers.update(mediaset_headers(cookies, ua))
    elif "discovery" in target["source_page"]:
        headers["Referer"] = "https://www.discoveryplus.com/"
        headers["Origin"] = "https://www.discoveryplus.com"
    elif "rai" in target["source_page"]:
        headers["Referer"] = "https://www.raiplay.it/"

    return {
        "ok": True,
        "channel_id": channel_id,
        "source_page": target.get("source_page"),
        "stream_url": new_url,
        "headers": headers,
    }


@router.post("/update/{channel_id}")
async def api_trigger_update(channel_id: str):
    update_single_channel(channel_id)
    return {"status": "ok"}

@router.get("/debug/{filename}")
async def get_debug_image(filename: str):
    path = os.path.join(DEBUG_DIR, filename)
    if os.path.exists(path): return FileResponse(path)
    return {"error": "File not found"}

@hook
def after_cat_bootstrap(cat):
    global GLOBAL_CAT
    GLOBAL_CAT = cat
    cat.fastapi_app.include_router(router, prefix="/scraper", tags=["Scraper"])
