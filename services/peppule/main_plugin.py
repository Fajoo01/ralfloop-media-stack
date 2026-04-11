import re
from cat.mad_hatter.decorators import hook
from .pipelines import pipeline_series, pipeline_movie
from .utils import normalize_string

@hook
def agent_fast_reply(fast_reply, cat):
    msg = cat.working_memory.user_message_json.text.strip()
    low = msg.lower()
    
    if low.startswith("scarica "):
        query = normalize_string(low.replace("scarica ", ""))
        
        # Identifica Serie TV
        m = re.search(r'(stagione|season)\s*(\d+)', query)
        if m:
            title = query.replace(m.group(0), "").strip()
            res = pipeline_series(cat, title, int(m.group(2)))
            return {"output": res}
        
        # Altrimenti Film
        res = pipeline_movie(cat, query)
        return {"output": res}
        
    return fast_reply