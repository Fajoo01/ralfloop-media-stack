import re, unicodedata, os, json

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_JSON_PATH = os.path.join(PLUGIN_DIR, "last_search_results.json")

def normalize_string(text):
    if not text: return ""
    text = re.sub(r'\s*\(.*?\)', '', text)
    nfkd = unicodedata.normalize('NFKD', text)
    return "".join([c for c in nfkd if not unicodedata.combining(c)]).strip()


def movie_search_variants(text):
    if not text:
        return []
    base = normalize_string(text).strip()
    variants = [base]

    for sep in [" - ", ": "]:
        if sep in base:
            head = base.split(sep)[0].strip()
            if head and head not in variants:
                variants.append(head)

    return variants

def log_console(msg):
    print(f"🏴‍☠️ [PEPPULE] {msg}")

def write_cache(data):
    with open(RESULTS_JSON_PATH, "w") as f: json.dump(data, f)

def read_cache():
    try:
        with open(RESULTS_JSON_PATH, "r") as f: return json.load(f)
    except: return []