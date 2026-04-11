import os, re, json, time, uuid, sqlite3, subprocess, sys
from select import select
from fastapi import FastAPI, Depends, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# --- CONFIG ---
CONFIG_PATH = os.environ.get("AMULE_API_CONFIG", "config.json")

def load_config():
    default = {
        "amule_password":"",
        "amule_host":"127.0.0.1",
        "amule_port":"4712",
        "amulecmd_path":"/usr/bin/amulecmd",
        "api_key":"testkey",
        "db_name":"amule_api.sqlite3",
        "search_order":["kad","global","local"],
        "min_results":1
    }
    if not os.path.exists(CONFIG_PATH):
        return default

    with open(CONFIG_PATH, encoding="utf-8") as f:
        c = json.load(f)
        for k, v in default.items():
            c.setdefault(k, v)
        return c

CFG = load_config()


# --- DATABASE ---

def db_init():
    with sqlite3.connect(CFG["db_name"]) as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            query TEXT,
            status TEXT
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT,
            filename TEXT,
            size_bytes INTEGER,
            sources INTEGER,
            network TEXT
        )
        """)

        cols = {r[1] for r in conn.execute("PRAGMA table_info(results)").fetchall()}
        if "job_id" not in cols:
            conn.execute("ALTER TABLE results ADD COLUMN job_id TEXT")
        if "network" not in cols:
            conn.execute("ALTER TABLE results ADD COLUMN network TEXT")

        conn.commit()

db_init()


# --- AMULE SESSION ---

class AmuleSession:
    def __init__(self):
        args = [
            CFG["amulecmd_path"],
            "-h", CFG["amule_host"],
            "-p", str(CFG["amule_port"])
        ]

        if CFG["amule_password"]:
            args += ["-P", CFG["amule_password"]]

        self.proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=0
        )
        self._read_until_prompt()

    def _read_until_prompt(self, timeout=8):
        buf = ""
        end = time.time() + timeout
        while time.time() < end:
            r, _, _ = select([self.proc.stdout], [], [], 0.2)
            if r:
                line = self.proc.stdout.readline()
                if not line:
                    break
                buf += line
                if "aMulecmd$" in line:
                    break
        return buf

    def send(self, cmd, timeout=8):
        self.proc.stdin.write(cmd + "\n")
        self.proc.stdin.flush()
        return self._read_until_prompt(timeout)

    def close(self):
        try:
            self.proc.terminate()
        except Exception:
            pass


# --- HELPERS ---

def parse_results(out):
    res = []
    p = re.compile(r"^\s*(\d+)\.\s+(.*?)\s+([0-9.]+)\s+(\d+)\s*$")
    for line in out.splitlines():
        m = p.match(line)
        if m:
            res.append({
                "idx": int(m.group(1)),
                "name": m.group(2).strip(),
                "mb": float(m.group(3)),
                "src": int(m.group(4))
            })
    return res

def clean_text(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()

def token_set(s: str):
    return {t for t in clean_text(s).split() if len(t) >= 2}

def extract_season_episode(s: str):
    s = (s or "").lower()
    m = re.search(r"s(\d{1,2})e(\d{1,2})", s)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d{1,2})x(\d{1,2})", s)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None

def extract_year(s: str):
    m = re.search(r"\b(19\d{2}|20\d{2})\b", s or "")
    return int(m.group(1)) if m else None

def significant_tokens(name: str):
    stop = {
        "ita","eng","sub","subs","ac3","aac","ddp5","ddp51","webrip","webdl","web","bdrip",
        "bluray","x264","x265","h264","h265","hdr","repack","proper","nf","amzn","atvp",
        "mkv","mp4","avi","720p","1080p","2160p","fullhd","uhd","rip"
    }
    out = []
    for t in clean_text(name).split():
        if t in stop:
            continue
        if len(t) == 1:
            continue
        if re.fullmatch(r"(19\d{2}|20\d{2})", t):
            continue
        if re.fullmatch(r"s\d{1,2}e\d{1,2}", t):
            continue
        if re.fullmatch(r"\d{1,2}x\d{1,2}", t):
            continue
        out.append(t)
    return out

def is_plausible_match(target_name: str, candidate_name: str, original_query: str = "") -> bool:
    tn = target_name or ""
    cn = candidate_name or ""

    if clean_text(tn) == clean_text(cn):
        return True

    ts, te = extract_season_episode(tn)
    cs, ce = extract_season_episode(cn)

    if ts is not None and te is not None:
        if (cs, ce) != (ts, te):
            return False

    ty = extract_year(tn)
    cy = extract_year(cn)
    if ts is None and te is None and ty is not None and cy is not None and ty != cy:
        return False

    required = significant_tokens(original_query or tn)
    candidate_tokens = token_set(cn)

    if required:
        min_needed = 1 if len(required) == 1 else 2
        overlap = sum(1 for t in required if t in candidate_tokens)
        if overlap < min(min_needed, len(required)):
            return False

    target_tokens = token_set(" ".join(significant_tokens(tn)))
    if target_tokens and len(target_tokens & candidate_tokens) == 0:
        return False

    return True


# --- APP ---

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

def auth(x_api_key: str = Header(None, alias="X-API-Key")):
    if x_api_key != CFG["api_key"]:
        raise HTTPException(status_code=401, detail="Invalid Key")
    return True


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/jobs")
def create_job(p: dict, api_key: bool = Depends(auth)):
    q = (p or {}).get("query", "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="Missing query")

    jid = uuid.uuid4().hex
    with sqlite3.connect(CFG["db_name"]) as conn:
        conn.execute("INSERT INTO jobs (id, query, status) VALUES (?,?,?)", (jid, q, "idle"))
        conn.commit()
    return {"id": jid}


@app.post("/jobs/{jid}/run")
def run_job(jid: str, api_key: bool = Depends(auth)):
    with sqlite3.connect(CFG["db_name"]) as conn:
        row = conn.execute("SELECT query FROM jobs WHERE id=?", (jid,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Job not found")
        q = row[0]

    s = AmuleSession()
    s.send("reset")

    data = []
    chosen_network = None

    for network in CFG.get("search_order", ["kad", "global", "local"]):
        s.send(f'search {network} "{q}"')
        for _ in range(12):
            time.sleep(3)
            data = parse_results(s.send("results"))
            if len(data) >= int(CFG.get("min_results", 1)):
                chosen_network = network
                break
        if data:
            break

    with sqlite3.connect(CFG["db_name"]) as conn:
        for r in data:
            conn.execute(
                "INSERT INTO results (job_id, filename, size_bytes, sources, network) VALUES (?,?,?,?,?)",
                (jid, r["name"], int(r["mb"] * 1024 * 1024), r["src"], chosen_network)
            )
        conn.execute("UPDATE jobs SET status=? WHERE id=?", ("done", jid))
        conn.commit()

    s.close()
    return {"count": len(data), "network": chosen_network}


@app.get("/jobs/{jid}/results")
def get_results(jid: str, api_key: bool = Depends(auth)):
    with sqlite3.connect(CFG["db_name"]) as conn:
        rows = conn.execute(
            "SELECT id, filename, size_bytes, sources, network FROM results WHERE job_id=? ORDER BY id",
            (jid,)
        ).fetchall()

    return [
        {
            "id": r[0],
            "filename": r[1],
            "size_bytes": r[2],
            "sources": r[3],
            "network": r[4]
        }
        for r in rows
    ]



@app.post("/download")
def download(p: dict, api_key: bool = Depends(auth)):
    ids = (p or {}).get("result_ids") or []
    if not ids:
        raise HTTPException(status_code=400, detail="Missing result_ids")

    rid = ids[0]

    with sqlite3.connect(CFG["db_name"]) as conn:
        row = conn.execute("""
            SELECT r.filename, r.job_id, r.network, j.query
            FROM results r
            LEFT JOIN jobs j ON j.id = r.job_id
            WHERE r.id=?
        """, (rid,)).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Result not found")

    target_name, job_id, stored_network, original_query = row
    search_q = (original_query or target_name or "").strip()
    network = stored_network or "kad"

    if not search_q:
        raise HTTPException(status_code=400, detail="Missing search query for selected result")

    def _clean(x: str) -> str:
        return re.sub(r'[^a-z0-9]', '', (x or '').lower())

    s = AmuleSession()
    s.send("reset")
    s.send(f'search {network} "{search_q}"')

    idx = None
    matched_name = None
    best_score = -10**9

    def _tokens(x: str):
        import re
        return {t for t in re.split(r'[^a-z0-9]+', (x or "").lower()) if len(t) >= 3}

    wanted_tokens = _tokens(target_name) | _tokens(original_query or "")

    for _ in range(15):
        time.sleep(2)
        rows = parse_results(s.send("results"))
        for r in rows:
            name = r["name"]
            low = (name or "").lower()

            # scarta roba inutile
            if low.endswith(".torrent") or low.endswith(".srt") or ".torrent" in low:
                continue

            if not any(low.endswith(ext) for ext in [".mkv", ".mp4", ".avi", ".m4v"]):
                continue

            if not is_plausible_match(target_name, name, original_query or ""):
                continue

            cand_tokens = _tokens(name)
            overlap = len(wanted_tokens & cand_tokens)

            score = 0
            score += overlap * 20
            score += min(int(r.get("src", 0)), 50)

            if "1080p" in low:
                score += 120
            if "720p" in low:
                score += 60
            if "2160p" in low or "4k" in low:
                score -= 200

            if ".mkv" in low:
                score += 25
            if ".mp4" in low or ".m4v" in low:
                score += 10
            if ".avi" in low:
                score -= 80

            if "ac3" in low:
                score += 20
            if "ita" in low or "italian" in low:
                score += 50
            if "eng" in low:
                score += 5

            if "sd" in low:
                score -= 150
            if "bdrip.sd" in low:
                score -= 180
            if "cam" in low or "ts" in low or "tc" in low:
                score -= 300

            if score > best_score:
                best_score = score
                idx = r["idx"]
                matched_name = name

    status = "error"
    out = ""
    queue_out = ""

    if idx is not None:
        out = s.send(f"download {idx}")

        try:
            queue_out = s.send("show dl", t=8)
        except Exception:
            queue_out = ""

        target_clean = _clean(target_name)
        matched_clean = _clean(matched_name or "")
        queue_clean = _clean(queue_out)
        low = (out or "").lower()

        if "invalid" not in low and "error" not in low:
            if (target_clean and target_clean[:40] in queue_clean) or (matched_clean and matched_clean[:40] in queue_clean):
                status = "ok"
            else:
                status = "error"

    s.close()
    return {
        "results": [{
            "status": status,
            "matched_name": matched_name,
            "search_query": search_q,
            "network": network,
            "download_output": out.strip(),
            "queue_checked": True
        }]
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8081)
