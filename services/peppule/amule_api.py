import os, re, json, time, uuid, sqlite3, subprocess
from select import select
from fastapi import FastAPI, Depends, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

CONFIG_PATH = os.environ.get("AMULE_API_CONFIG", "config.json")

def load_config():
    default = {
        "amule_password": "",
        "amule_host": "127.0.0.1",
        "amule_port": "4712",
        "amulecmd_path": "/usr/bin/amulecmd",
        "api_key": "testkey",
        "db_name": "amule_api.sqlite3",
    }
    if not os.path.exists(CONFIG_PATH):
        return default
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        c = json.load(f)
    for k, v in default.items():
        c.setdefault(k, v)
    return c

CFG = load_config()

def db_init():
    with sqlite3.connect(CFG["db_name"]) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, query TEXT, status TEXT)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT,
                filename TEXT,
                size_bytes INTEGER,
                sources INTEGER
            )
        """)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(results)").fetchall()}
        if "job_id" not in cols:
            conn.execute("ALTER TABLE results ADD COLUMN job_id TEXT")
        conn.commit()

db_init()

class AmuleSession:
    def __init__(self):
        args = [
            CFG["amulecmd_path"],
            "-h", str(CFG["amule_host"]),
            "-p", str(CFG["amule_port"]),
        ]
        if CFG.get("amule_password"):
            args += ["-P", CFG["amule_password"]]
        self.proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=0,
        )
        self._read()

    def _read(self, t=5):
        buf = ""
        end = time.time() + t
        while time.time() < end:
            r, _, _ = select([self.proc.stdout], [], [], 0.1)
            if r:
                line = self.proc.stdout.readline()
                if not line:
                    break
                buf += line
                if "aMulecmd$" in line:
                    break
        return buf

    def send(self, cmd, t=5):
        self.proc.stdin.write(cmd + "\n")
        self.proc.stdin.flush()
        return self._read(t)

    def close(self):
        try:
            self.proc.terminate()
        except Exception:
            pass

def parse(out):
    res = []
    p = re.compile(r"^\s*(\d+)\.\s+(.*?)\s+([0-9.]+)\s+(\d+)\s*$")
    for line in out.splitlines():
        m = p.match(line)
        if m:
            res.append({
                "idx": int(m.group(1)),
                "name": m.group(2).strip(),
                "mb": float(m.group(3)),
                "src": int(m.group(4)),
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

    tclean = clean_text(tn)
    cclean = clean_text(cn)

    if tclean == cclean:
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
    if target_tokens:
        overlap2 = len(target_tokens & candidate_tokens)
        if overlap2 == 0:
            return False

    return True

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"])

def auth(x_api_key: str = Header(None, alias="X-API-Key")):
    if x_api_key != CFG["api_key"]:
        raise HTTPException(401, "Invalid Key")

@app.post("/jobs")
def create_job(p: dict, api_key: str = Depends(auth)):
    jid = uuid.uuid4().hex
    with sqlite3.connect(CFG["db_name"]) as conn:
        conn.execute(
            "INSERT INTO jobs (id, query, status) VALUES (?,?,?)",
            (jid, p["query"], "idle")
        )
        conn.commit()
    return {"id": jid}

@app.post("/jobs/{jid}/run")
def run_job(jid: str, api_key: str = Depends(auth)):
    with sqlite3.connect(CFG["db_name"]) as conn:
        row = conn.execute("SELECT query FROM jobs WHERE id=?", (jid,)).fetchone()
        if not row:
            raise HTTPException(404, "Job not found")
        q = row[0]

    s = AmuleSession()
    s.send("reset")
    s.send(f'search kad "{q}"')

    data = []
    for _ in range(12):
        time.sleep(3)
        data = parse(s.send("results"))
        if data:
            break

    with sqlite3.connect(CFG["db_name"]) as conn:
        for r in data:
            conn.execute(
                "INSERT INTO results (job_id, filename, size_bytes, sources) VALUES (?,?,?,?)",
                (jid, r["name"], int(r["mb"] * 1024 * 1024), r["src"])
            )
        conn.commit()

    s.close()
    return {"count": len(data)}

@app.get("/jobs/{jid}/results")
def get_res(jid: str, api_key: str = Depends(auth)):
    with sqlite3.connect(CFG["db_name"]) as conn:
        rows = conn.execute(
            "SELECT id, filename, size_bytes, sources FROM results WHERE job_id=?",
            (jid,)
        ).fetchall()
    return [
        {"id": r[0], "filename": r[1], "size_bytes": r[2], "sources": r[3]}
        for r in rows
    ]

@app.post("/download")
def dl(p: dict, api_key: str = Depends(auth)):
    rid = p["result_ids"][0]

    with sqlite3.connect(CFG["db_name"]) as conn:
        row = conn.execute("""
            SELECT r.filename, r.job_id, j.query
            FROM results r
            LEFT JOIN jobs j ON j.id = r.job_id
            WHERE r.id=?
        """, (rid,)).fetchone()

    if not row:
        raise HTTPException(404, "Result not found")

    target_name, job_id, original_query = row
    search_q = (original_query or target_name or "").strip()

    if not search_q:
        raise HTTPException(400, "Missing search query for selected result")

    s = AmuleSession()
    s.send("reset")
    s.send(f'search kad "{search_q}"')

    idx = None
    matched_name = None

    for _ in range(15):
        time.sleep(2)
        rows = parse(s.send("results"))
        for r in rows:
            if is_plausible_match(target_name, r["name"], original_query or ""):
                idx = r["idx"]
                matched_name = r["name"]
                break
        if idx is not None:
            break

    status = "error"
    if idx is not None:
        out = s.send(f"download {idx}")
        low = out.lower()
        if "invalid" not in low and "error" not in low:
            status = "ok"

    s.close()
    return {
        "results": [{
            "status": status,
            "matched_name": matched_name,
            "search_query": search_q
        }]
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8081)
