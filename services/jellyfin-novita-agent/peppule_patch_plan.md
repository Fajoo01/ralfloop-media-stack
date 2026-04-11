# Patch plan concreto

## 1) `settings.py`
Allineare il modello ai campi reali di `settings.json`.

Aggiunte minime:
- `auto_download: bool = True`
- `auto_download_movies: bool = True`
- `novita_agent_enabled: bool = False`
- `novita_observation_days: int = 90`
- `novita_auto_threshold: int = 15`
- `novita_suggest_threshold: int = 8`
- `novita_max_downloads_per_day: int = 3`

## 2) `services.py`
Estrarre funzioni pure riusabili fuori dal contesto chat:
- lasciare `execute_search_amule`, `execute_download_amule`, `check_jellyfin_movie`, `check_jellyfin_episode`
- aggiungere `check_jellyfin_series_season(title, season)` che controlla se esiste almeno un episodio della stagione
- eliminare nel medio termine l'istanza interna diretta di `CheshireCat` in `get_peppule_settings()` e passare settings dal chiamante

## 3) `main_plugin.py`
Non usare più `agent_fast_reply` come unico entrypoint.

Aggiungere funzioni:
- `run_movie_query(cat, query) -> str`
- `run_series_query(cat, title, season) -> str`

`agent_fast_reply` deve solo fare parsing del messaggio e delegare a queste funzioni.
Così lo stesso motore resta invocabile da chat e da job esterni.

## 4) nuovo modulo consigliato: `runtime_api.py`
Creare un file interno al plugin che espone:
- `run_movie_query(cat, query)`
- `run_series_query(cat, title, season)`
- `search_movie(query)`
- `search_series(title, season)`

Le varianti `search_*` fanno solo ricerca/classifica senza download.

## 5) `jellyfin_novita_agent.py`
Questo script resta fuori dal plugin e fa:
1. ingest statistiche
2. scoring
3. filtro anti-duplicato Jellyfin
4. generazione queue
5. download opzionale via wrapper aMule

## 6) limite reale attuale
Con i file oggi disponibili manca ancora il collector da Playback Reporting.
Quindi il primo test reale va fatto con un export JSON (`views_90d.json`) e non direttamente dal plugin Jellyfin.
