function JellyfinNovitaGUIConnected() {
  const API_BASE_DEFAULT = "http://127.0.0.1:9079";

  const defaultState = {
    backendBase: API_BASE_DEFAULT,
    enabled: true,
    mode: "scheduled",
    frequency: "daily",
    time: "03:30",
    timezone: "Europe/Rome",
    maxPerDay: 3,
    observationDays: 90,
    suggestThreshold: 12,
    autoDownloadThreshold: 30,
    movieSuggestThreshold: 8,
    movieAutoDownloadThreshold: 15,
    directorWeight: 1.2,
    writerWeight: 0.8,
    castWeight: 0.45,
    directorMatchBonus: 3.0,
    writerMatchBonus: 2.0,
    castMatchBonus: 1.0,
    castTopN: 5,
    executionPath: "peppule",
    useTmdb: true,
    useSeedCatalog: true,
    usePlaybackReporting: false,
    useSampleViews: false,
    moviePages: 1,
    tvPages: 1,
    language: "it-IT",
    region: "IT",
    dryRun: true,
    dbPath: "",
    outputDir: "",
    notifyOnRun: true,
    notifyOnError: true,
    saveHistory: true,
    preferredGenresSelected: [],
    manualFavoritesList: [],
    manualFavoriteDirectors: [],
    manualFavoriteWriters: [],
    manualFavoriteActors: [],
    api: {
      jellyfinUrl: "http://10.252.14.7:8096",
      jellyfinToken: "",
      wrapperUrl: "http://10.252.14.7:8081",
      wrapperApiKey: "",
      cheshireWsUrl: "ws://10.252.14.7:1865/ws",
      cheshireApiKey: "",
      tmdbBearer: "",
      tmdbApiKey: "",
    },
  };

  const [state, setState] = React.useState(defaultState);
  const [savedAt, setSavedAt] = React.useState(null);
  const [loading, setLoading] = React.useState(false);
  const [logs, setLogs] = React.useState([
    "GUI collegata al backend FastAPI.",
    "Azioni disponibili: salva config, test API, build catalog, run agent.",
  ]);

  const pushLog = (line) => {
    setLogs((prev) => [typeof line === "string" ? line : JSON.stringify(line, null, 2), ...prev]);
  };

  const update = (key, value) => setState((s) => ({ ...s, [key]: value }));
  const updateApi = (key, value) =>
    setState((s) => ({ ...s, api: { ...s.api, [key]: value } }));

  const GENRES = [
    "Crime","Thriller","Sci-Fi","Dramma","Commedia","Mistero","Giallo",
    "Azione","Avventura","Storico","Horror","Animazione","Fantasy",
    "Documentario","Western","Romance"
  ];

  const toggleGenre = (g) => {
    setState((s) => ({
      ...s,
      preferredGenresSelected: (s.preferredGenresSelected || []).includes(g)
        ? (s.preferredGenresSelected || []).filter(x => x !== g)
        : [...(s.preferredGenresSelected || []), g]
    }));
  };

  const addFavorite = (title) => {
    const t = (title || "").trim();
    if (!t) return;
    setState((s) => ({
      ...s,
      manualFavoritesList: (s.manualFavoritesList || []).includes(t)
        ? (s.manualFavoritesList || [])
        : [...(s.manualFavoritesList || []), t]
    }));
  };

  const removeFavorite = (title) => {
    setState((s) => ({
      ...s,
      manualFavoritesList: (s.manualFavoritesList || []).filter(x => x !== title)
    }));
  };

  const addManualListItem = (key, value) => {
    const t = (value || "").trim();
    if (!t) return;
    setState((s) => ({
      ...s,
      [key]: (s[key] || []).includes(t) ? (s[key] || []) : [...(s[key] || []), t]
    }));
  };

  const removeManualListItem = (key, value) => {
    setState((s) => ({
      ...s,
      [key]: (s[key] || []).filter(x => x !== value)
    }));
  };

  const masked = (value) => {
    if (!value) return "non impostato";
    if (value.length <= 8) return "••••••••";
    return `${value.slice(0, 4)}••••${value.slice(-4)}`;
  };

  const mapApiConfigToUi = (cfg) => ({
    jellyfinUrl: cfg.jellyfin_url || defaultState.api.jellyfinUrl,
    jellyfinToken: cfg.jellyfin_token || "",
    wrapperUrl: cfg.amule_api_url || defaultState.api.wrapperUrl,
    wrapperApiKey: cfg.amule_api_key || "",
    cheshireWsUrl: cfg.cheshire_ws_url || defaultState.api.cheshireWsUrl,
    cheshireApiKey: cfg.cheshire_api_key || "",
    tmdbBearer: cfg.tmdb_bearer || "",
    tmdbApiKey: cfg.tmdb_api_key || "",
    preferredGenresSelected: Array.isArray(cfg.preferred_genres) ? cfg.preferred_genres : [],
    manualFavoritesList: Array.isArray(cfg.manual_favorites) ? cfg.manual_favorites : [],
    manualFavoriteDirectors: Array.isArray(cfg.manual_favorite_directors) ? cfg.manual_favorite_directors : [],
    manualFavoriteWriters: Array.isArray(cfg.manual_favorite_writers) ? cfg.manual_favorite_writers : [],
    manualFavoriteActors: Array.isArray(cfg.manual_favorite_actors) ? cfg.manual_favorite_actors : [],
    suggestThreshold: cfg.suggest_threshold ?? defaultState.suggestThreshold,
    autoDownloadThreshold: cfg.auto_download_threshold ?? defaultState.autoDownloadThreshold,
    movieSuggestThreshold: cfg.movie_suggest_threshold ?? defaultState.movieSuggestThreshold,
    movieAutoDownloadThreshold: cfg.movie_auto_download_threshold ?? defaultState.movieAutoDownloadThreshold,
    directorWeight: cfg.director_weight ?? defaultState.directorWeight,
    writerWeight: cfg.writer_weight ?? defaultState.writerWeight,
    castWeight: cfg.cast_weight ?? defaultState.castWeight,
    directorMatchBonus: cfg.director_match_bonus ?? defaultState.directorMatchBonus,
    writerMatchBonus: cfg.writer_match_bonus ?? defaultState.writerMatchBonus,
    castMatchBonus: cfg.cast_match_bonus ?? defaultState.castMatchBonus,
    castTopN: cfg.cast_top_n ?? defaultState.castTopN,
    maxPerDay: cfg.max_auto_download_per_day ?? defaultState.maxPerDay,
  });

  const mapUiToApiConfig = () => ({
    jellyfin_url: state.api.jellyfinUrl,
    jellyfin_token: state.api.jellyfinToken,
    amule_api_url: state.api.wrapperUrl,
    amule_api_key: state.api.wrapperApiKey,
    cheshire_ws_url: state.api.cheshireWsUrl,
    cheshire_api_key: state.api.cheshireApiKey,
    tmdb_bearer: state.api.tmdbBearer,
    tmdb_api_key: state.api.tmdbApiKey,
    suggest_threshold: state.suggestThreshold,
    auto_download_threshold: state.autoDownloadThreshold,
    movie_suggest_threshold: state.movieSuggestThreshold,
    movie_auto_download_threshold: state.movieAutoDownloadThreshold,
    director_weight: state.directorWeight,
    writer_weight: state.writerWeight,
    cast_weight: state.castWeight,
    director_match_bonus: state.directorMatchBonus,
    writer_match_bonus: state.writerMatchBonus,
    cast_match_bonus: state.castMatchBonus,
    cast_top_n: state.castTopN,
    max_auto_download_per_day: state.maxPerDay,
    preferred_genres: state.preferredGenresSelected || [],
    manual_favorites: state.manualFavoritesList || [],
    manual_favorite_directors: state.manualFavoriteDirectors || [],
    manual_favorite_writers: state.manualFavoriteWriters || [],
    manual_favorite_actors: state.manualFavoriteActors || [],
    db_path: "/home/bandi/jellyfin-novita-agent/data/novita.db",
    output_dir: "/home/bandi/jellyfin-novita-agent/out",
    observation_days: state.observationDays,
    auto_download_threshold: state.autoDownloadThreshold,
    suggest_threshold: state.suggestThreshold,
    max_auto_download_per_day: state.maxPerDay,
    tmdb_language: state.language,
    tmdb_region: state.region,
    tmdb_movie_pages: state.moviePages,
    tmdb_tv_pages: state.tvPages,
    execution_path: state.executionPath,
    dry_run: state.dryRun,
    db_path: state.dbPath,
    output_dir: state.outputDir,
  });

  const apiFetch = async (path, options = {}) => {
    const url = `${state.backendBase.replace(/\/$/, "")}${path}`;
    const res = await fetch(url, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const text = await res.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = { raw: text };
    }
    if (!res.ok) {
      throw new Error(typeof data === "object" ? JSON.stringify(data, null, 2) : String(data));
    }
    return data;
  };

  const loadConfig = async () => {
    setLoading(true);
    try {
      const cfg = await apiFetch("/api/config", { method: "GET" });
      setState((s) => ({
        ...s,
        observationDays: cfg.observation_days ?? s.observationDays,
        suggestThreshold: cfg.suggest_threshold ?? s.suggestThreshold,
        autoDownloadThreshold: cfg.auto_download_threshold ?? s.autoDownloadThreshold,
        maxPerDay: cfg.max_auto_download_per_day ?? s.maxPerDay,
        executionPath: cfg.execution_path ?? s.executionPath,
        dryRun: cfg.dry_run ?? s.dryRun,
        dbPath: cfg.db_path ?? s.dbPath,
        outputDir: cfg.output_dir ?? s.outputDir,
        language: cfg.tmdb_language ?? s.language,
        region: cfg.tmdb_region ?? s.region,
        moviePages: cfg.tmdb_movie_pages ?? s.moviePages,
        tvPages: cfg.tmdb_tv_pages ?? s.tvPages,
        api: mapApiConfigToUi(cfg),
      }));
      pushLog("Config caricata dal backend.");
    } catch (e) {
      pushLog(`Errore loadConfig: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  React.useEffect(() => {
    loadConfig();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const saveConfig = async () => {
    setLoading(true);
    try {
      const payload = { config: mapUiToApiConfig() };
      const data = await apiFetch("/api/config", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      setSavedAt(new Date().toLocaleString("it-IT"));
      pushLog(data);
    } catch (e) {
      pushLog(`Errore saveConfig: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  
  const loadDownloadQueue = async () => {
    try {
      const res = await fetch(state.backendBase + "/api/results");
      const data = await res.json();
      if (data["download_queue.json"]) {
        pushLog("Download queue:");
        data["download_queue.json"].forEach((x,i)=>{
          pushLog({pos:i+1,title:x.title,score:x.score,reason:x.reason});
        });
      } else {
        pushLog("download_queue.json non trovato");
      }
    } catch(e) {
      pushLog("Errore lettura queue: " + e);
    }
  };

const simulateRun = () => {
    pushLog(`Run simulato: mode=${state.mode}, path=${state.executionPath}, dryRun=${state.dryRun}`);
  };

  const buildCatalog = async () => {
    setLoading(true);
    try {
      const data = await apiFetch("/api/build-catalog", {
        method: "POST",
        body: JSON.stringify({
          seed_file: "/home/bandi/jellyfin-novita-agent/catalog_seed.json",
          out_file: "/home/bandi/jellyfin-novita-agent/external_catalog.json",
          movie_pages: state.moviePages,
          tv_pages: state.tvPages,
          language: state.language,
          region: state.region,
        }),
      });
      pushLog(data);
    } catch (e) {
      pushLog(`Errore buildCatalog: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  const runAgent = async (execute) => {
    setLoading(true);
    try {
      const data = await apiFetch("/api/run-agent", {
        method: "POST",
        body: JSON.stringify({
          config_file: "/home/sibilla-cumana/jellyfin-novita-agent/config.json",
          views_file: "/home/sibilla-cumana/jellyfin-novita-agent/views_90d.clean.json",
          catalog_file: "/home/sibilla-cumana/jellyfin-novita-agent/external_catalog.json",
          execute,
        }),
      });
      pushLog(data);
      if (data && Array.isArray(data.executions)) {
        data.executions.forEach((e, i) => {
          pushLog({
            kind: "execution",
            index: i + 1,
            status: e.status,
            target: e.target,
            payload: e.payload,
            response_summary: e.response?.output || e.response || null,
          });
        });
      }
    } catch (e) {
      pushLog(`Errore runAgent: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  const testConnection = async (kind) => {
    setLoading(true);
    try {
      const routes = {
        jellyfin: {
          path: "/api/test/jellyfin",
          payload: { url: state.api.jellyfinUrl, token: state.api.jellyfinToken },
        },
        wrapper: {
          path: "/api/test/wrapper",
          payload: { url: state.api.wrapperUrl, api_key: state.api.wrapperApiKey },
        },
        cheshire: {
          path: "/api/test/cheshire",
          payload: { url: state.api.cheshireWsUrl },
        },
        tmdb: {
          path: "/api/test/tmdb",
          payload: { bearer: state.api.tmdbBearer, api_key: state.api.tmdbApiKey },
        },
      };
      const target = routes[kind];
      const data = await apiFetch(target.path, {
        method: "POST",
        body: JSON.stringify(target.payload),
      });
      pushLog(data);
    } catch (e) {
      pushLog(`Errore test ${kind}: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };


  const testPeppuleHealth = async () => {
    setLoading(true);
    try {
      const baseWs = (state.api.cheshireWsUrl || "").trim();
      if (!baseWs) {
        throw new Error("cheshireWsUrl mancante");
      }

      const url = new URL(baseWs.replace(/^ws:/, "http:").replace(/^wss:/, "https:"));
      const target = `${url.protocol}//${url.host}/custom/peppule/health`;

      const headers = { "user_id": "novita-agent" };
      if (state.api.cheshireApiKey) {
        headers["Authorization"] = `Bearer ${state.api.cheshireApiKey}`;
      }

      const res = await fetch(target, { headers });
      const txt = await res.text();
      let data;
      try {
        data = txt ? JSON.parse(txt) : {};
      } catch {
        data = { raw: txt };
      }

      if (!res.ok) {
        throw new Error(JSON.stringify(data, null, 2));
      }

      pushLog({
        kind: "peppule_health",
        target,
        response: data
      });
    } catch (e) {
      pushLog(`Errore test Peppule Entrypoint: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  const loadResults = async () => {
    setLoading(true);
    try {
      const data = await apiFetch("/api/results", { method: "GET" });
      pushLog(data);
    } catch (e) {
      pushLog(`Errore loadResults: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  const resetAll = () => {
    setState(defaultState);
    pushLog("Configurazione UI riportata ai valori iniziali.");
  };

  const Section = ({ title, subtitle, children }) => (
    <div className="bg-white rounded-2xl shadow-sm border border-slate-200 p-5">
      <div className="mb-4">
        <h2 className="text-lg font-semibold text-slate-900">{title}</h2>
        {subtitle ? <p className="text-sm text-slate-500 mt-1">{subtitle}</p> : null}
      </div>
      <div className="space-y-4">{children}</div>
    </div>
  );

  const Field = ({ label, hint, children }) => (
    <div>
      <div className="flex items-center justify-between gap-3 mb-1">
        <label className="text-sm font-medium text-slate-800">{label}</label>
        {hint ? <span className="text-xs text-slate-500">{hint}</span> : null}
      </div>
      {children}
    </div>
  );

  const inputClass =
    "w-full rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm focus:outline-none focus:ring-2 focus:ring-slate-400";

  const pill = (active) =>
    active
      ? "inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium bg-emerald-100 text-emerald-700"
      : "inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium bg-slate-100 text-slate-600";

  return (
    <div className="min-h-screen bg-slate-100 p-6">
      <div className="max-w-7xl mx-auto space-y-6">
        <div className="bg-gradient-to-r from-slate-900 to-slate-700 rounded-3xl p-6 text-white shadow-lg">
          <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
            <div>
              <h1 className="text-3xl font-bold tracking-tight">Jellyfin Novità Control Panel</h1>
              <p className="mt-2 text-sm text-slate-200 max-w-3xl">
                GUI collegata al backend: legge e salva config, testa API, costruisce il catalogo esterno e lancia l'agente.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <span className={pill(state.enabled)}>{state.enabled ? "Scheduler attivo" : "Scheduler spento"}</span>
              <span className={pill(state.executionPath === "peppule")}>{state.executionPath === "peppule" ? "Path: Peppule" : "Path: Wrapper"}</span>
              <span className={pill(state.dryRun)}>{state.dryRun ? "Dry run" : "Esecuzione reale"}</span>
              <span className={pill(!loading)}>{loading ? "Operazione in corso" : "Pronta"}</span>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
          <div className="xl:col-span-2 space-y-6">
            <Section title="Backend" subtitle="Indirizzo del backend FastAPI che la GUI deve usare.">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <Field label="Backend Base URL">
                  <input className={inputClass} value={state.backendBase} onChange={(e) => update("backendBase", e.target.value)} />
                </Field>
                <Field label="Azioni backend">
                  <div className="grid grid-cols-2 gap-3">
                    <button onClick={loadConfig} className="rounded-xl bg-slate-900 text-white px-4 py-2.5 text-sm font-medium hover:bg-slate-800">Ricarica config</button>
                    <button onClick={loadResults} className="rounded-xl bg-slate-900 text-white px-4 py-2.5 text-sm font-medium hover:bg-slate-800">Carica risultati</button>
                  </div>
                </Field>
              </div>
            </Section>

            <Section title="Scheduler" subtitle="Accensione, cadenza e fascia oraria del job automatico.">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <Field label="Abilita job automatico">
                  <button onClick={() => update("enabled", !state.enabled)} className={`w-full rounded-xl px-4 py-2.5 text-sm font-medium transition ${state.enabled ? "bg-emerald-600 text-white hover:bg-emerald-700" : "bg-slate-200 text-slate-800 hover:bg-slate-300"}`}>{state.enabled ? "Attivo" : "Disattivo"}</button>
                </Field>
                <Field label="Modalità">
                  <select className={inputClass} value={state.mode} onChange={(e) => update("mode", e.target.value)}>
                    <option value="scheduled">Schedulata</option>
                    <option value="manual">Solo manuale</option>
                    <option value="hybrid">Ibrida</option>
                  </select>
                </Field>
                <Field label="Frequenza">
                  <select className={inputClass} value={state.frequency} onChange={(e) => update("frequency", e.target.value)}>
                    <option value="daily">Ogni giorno</option>
                    <option value="weekdays">Solo feriali</option>
                    <option value="weekly">Settimanale</option>
                    <option value="twice_daily">Due volte al giorno</option>
                  </select>
                </Field>
                <Field label="Orario">
                  <input className={inputClass} type="time" value={state.time} onChange={(e) => update("time", e.target.value)} />
                </Field>
                <Field label="Timezone">
                  <input className={inputClass} value={state.timezone} onChange={(e) => update("timezone", e.target.value)} />
                </Field>
                <Field label="Massimo download/giorno">
                  <input className={inputClass} type="number" min="0" value={state.maxPerDay} onChange={(e) => update("maxPerDay", Number(e.target.value))} />
                </Field>
              </div>
            </Section>

            <Section title="API e credenziali" subtitle="Qui i valori finiscono davvero nel backend quando salvi.">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <Field label="Jellyfin URL">
                  <input className={inputClass} value={state.api.jellyfinUrl} onChange={(e) => updateApi("jellyfinUrl", e.target.value)} placeholder="http://host:8096" />
                </Field>
                <Field label="Jellyfin Token" hint={masked(state.api.jellyfinToken)}>
                  <input className={inputClass} type="password" value={state.api.jellyfinToken} onChange={(e) => updateApi("jellyfinToken", e.target.value)} placeholder="token API Jellyfin" />
                </Field>
                <Field label="Wrapper URL">
                  <input className={inputClass} value={state.api.wrapperUrl} onChange={(e) => updateApi("wrapperUrl", e.target.value)} placeholder="http://host:8081" />
                </Field>
                <Field label="Wrapper API Key" hint={masked(state.api.wrapperApiKey)}>
                  <input className={inputClass} type="password" value={state.api.wrapperApiKey} onChange={(e) => updateApi("wrapperApiKey", e.target.value)} placeholder="chiave wrapper" />
                </Field>
                <Field label="Cheshire Cat WS URL">
                  <input className={inputClass} value={state.api.cheshireWsUrl} onChange={(e) => updateApi("cheshireWsUrl", e.target.value)} placeholder="ws://host:1865/ws" />
                </Field>
                <Field label="Cheshire API Key" hint={masked(state.api.cheshireApiKey)}>
                  <input className={inputClass} type="password" value={state.api.cheshireApiKey} onChange={(e) => updateApi("cheshireApiKey", e.target.value)} placeholder="eventuale chiave API" />
                </Field>
                <Field label="TMDb Bearer" hint={masked(state.api.tmdbBearer)}>
                  <input className={inputClass} type="password" value={state.api.tmdbBearer} onChange={(e) => updateApi("tmdbBearer", e.target.value)} placeholder="TMDB_BEARER" />
                </Field>
                <Field label="TMDb API Key" hint={masked(state.api.tmdbApiKey)}>
                  <input className={inputClass} type="password" value={state.api.tmdbApiKey} onChange={(e) => updateApi("tmdbApiKey", e.target.value)} placeholder="TMDB_API_KEY" />
                </Field>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-4 gap-3 pt-2">
                <button onClick={() => testConnection("jellyfin")} className="rounded-xl bg-slate-900 text-white px-4 py-2.5 text-sm font-medium hover:bg-slate-800">Test Jellyfin</button>
                <button onClick={() => testConnection("wrapper")} className="rounded-xl bg-slate-900 text-white px-4 py-2.5 text-sm font-medium hover:bg-slate-800">Test Wrapper</button>
                <button onClick={() => testConnection("cheshire")} className="rounded-xl bg-slate-900 text-white px-4 py-2.5 text-sm font-medium hover:bg-slate-800">Test Cheshire</button>
                <button onClick={() => testConnection("tmdb")} className="rounded-xl bg-slate-900 text-white px-4 py-2.5 text-sm font-medium hover:bg-slate-800">Test TMDb</button>
                
              
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <label className="block text-sm">
                  <span className="mb-1 block font-medium">Percorso DB</span>
                  <input
                    className="w-full rounded-xl border border-slate-300 px-3 py-2"
                    value={state.dbPath || ""}
                    onChange={(e) => setState({ ...state, dbPath: e.target.value })}
                    placeholder="/home/sibilla-cumana/jellyfin-novita-agent/data/novita.db"
                  />
                </label>
                <label className="block text-sm">
                  <span className="mb-1 block font-medium">Cartella output</span>
                  <input
                    className="w-full rounded-xl border border-slate-300 px-3 py-2"
                    value={state.outputDir || ""}
                    onChange={(e) => setState({ ...state, outputDir: e.target.value })}
                    placeholder="/home/sibilla-cumana/jellyfin-novita-agent/out"
                  />
                </label>
              </div>

<div className="rounded-2xl border border-slate-200 p-4 bg-white">
                <label className="flex items-center gap-3 text-sm font-medium">
                  <input
                    type="checkbox"
                    checked={!!state.dryRun}
                    onChange={(e) => setState({ ...state, dryRun: e.target.checked })}
                  />
                  <span>Dry run (non scaricare davvero)</span>
                </label>
              </div>

<button onClick={testPeppuleHealth} className="rounded-xl bg-indigo-600 text-white px-4 py-2.5 text-sm font-medium hover:bg-indigo-700">Test Peppule Entrypoint</button>
              </div>
            </Section>

            <Section title="Sorgenti e scoring" subtitle="Da dove arrivano i papabili e come il motore decide cosa sale in cima.">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <Field label="Usa TMDb">
                  <button onClick={() => update("useTmdb", !state.useTmdb)} className={`w-full rounded-xl px-4 py-2.5 text-sm font-medium ${state.useTmdb ? "bg-emerald-600 text-white" : "bg-slate-200 text-slate-800"}`}>{state.useTmdb ? "Attivo" : "Spento"}</button>
                </Field>
                <Field label="Usa catalogo seed">
                  <button onClick={() => update("useSeedCatalog", !state.useSeedCatalog)} className={`w-full rounded-xl px-4 py-2.5 text-sm font-medium ${state.useSeedCatalog ? "bg-emerald-600 text-white" : "bg-slate-200 text-slate-800"}`}>{state.useSeedCatalog ? "Attivo" : "Spento"}</button>
                </Field>
                <Field label="Usa Playback Reporting">
                  <button onClick={() => update("usePlaybackReporting", !state.usePlaybackReporting)} className={`w-full rounded-xl px-4 py-2.5 text-sm font-medium ${state.usePlaybackReporting ? "bg-emerald-600 text-white" : "bg-slate-200 text-slate-800"}`}>{state.usePlaybackReporting ? "Attivo" : "Spento"}</button>
                </Field>
                <Field label="Usa views sample">
                  <button onClick={() => update("useSampleViews", !state.useSampleViews)} className={`w-full rounded-xl px-4 py-2.5 text-sm font-medium ${state.useSampleViews ? "bg-emerald-600 text-white" : "bg-slate-200 text-slate-800"}`}>{state.useSampleViews ? "Attivo" : "Spento"}</button>
                </Field>
                <Field label="Views file attivo">
                  <div className="w-full rounded-xl border border-slate-300 bg-slate-50 px-3 py-2 text-sm text-slate-700 break-all">
                    {"/home/sibilla-cumana/jellyfin-novita-agent/views_90d.clean.json"}
                  </div>
                </Field>
                <Field label="Generi da favorire">
                  <div className="grid grid-cols-2 gap-2">
                    {GENRES.map((g) => (
                      <label key={g} className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          checked={(state.preferredGenresSelected || []).includes(g)}
                          onChange={() => toggleGenre(g)}
                        />
                        <span>{g}</span>
                      </label>
                    ))}
                  </div>
                </Field>
                <Field label="Titoli piaciuti molto">
                  <div className="space-y-2">
                    <div className="flex gap-2">
                      <input
                        className={inputClass}
                        placeholder="Titolo..."
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            addFavorite(e.target.value);
                            e.target.value = "";
                          }
                        }}
                      />
                      <button
                        type="button"
                        onClick={(e) => {
                          const input = e.target.previousSibling;
                          addFavorite(input.value);
                          input.value = "";
                        }}
                        className="rounded-xl bg-slate-900 text-white px-3"
                      >
                        Aggiungi
                      </button>
                    </div>
                    <div className="space-y-1">
                      {(state.manualFavoritesList || []).map((t) => (
                        <div key={t} className="flex justify-between bg-slate-100 rounded px-2 py-1 text-sm">
                          <span>{t}</span>
                          <button type="button" onClick={() => removeFavorite(t)}>✕</button>
                        </div>
                      ))}
                    </div>
                  </div>
                </Field>

                <Field label="Registi preferiti">
                  <div className="space-y-2">
                    <div className="flex gap-2">
                      <input
                        className={inputClass}
                        placeholder="Regista..."
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            addManualListItem("manualFavoriteDirectors", e.target.value);
                            e.target.value = "";
                          }
                        }}
                      />
                      <button
                        type="button"
                        onClick={(e) => {
                          const input = e.target.previousSibling;
                          addManualListItem("manualFavoriteDirectors", input.value);
                          input.value = "";
                        }}
                        className="rounded-xl bg-slate-900 text-white px-3"
                      >
                        Aggiungi
                      </button>
                    </div>
                    <div className="space-y-1">
                      {(state.manualFavoriteDirectors || []).map((t) => (
                        <div key={t} className="flex justify-between bg-slate-100 rounded px-2 py-1 text-sm">
                          <span>{t}</span>
                          <button type="button" onClick={() => removeManualListItem("manualFavoriteDirectors", t)}>✕</button>
                        </div>
                      ))}
                    </div>
                  </div>
                </Field>

                <Field label="Sceneggiatori preferiti">
                  <div className="space-y-2">
                    <div className="flex gap-2">
                      <input
                        className={inputClass}
                        placeholder="Sceneggiatore..."
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            addManualListItem("manualFavoriteWriters", e.target.value);
                            e.target.value = "";
                          }
                        }}
                      />
                      <button
                        type="button"
                        onClick={(e) => {
                          const input = e.target.previousSibling;
                          addManualListItem("manualFavoriteWriters", input.value);
                          input.value = "";
                        }}
                        className="rounded-xl bg-slate-900 text-white px-3"
                      >
                        Aggiungi
                      </button>
                    </div>
                    <div className="space-y-1">
                      {(state.manualFavoriteWriters || []).map((t) => (
                        <div key={t} className="flex justify-between bg-slate-100 rounded px-2 py-1 text-sm">
                          <span>{t}</span>
                          <button type="button" onClick={() => removeManualListItem("manualFavoriteWriters", t)}>✕</button>
                        </div>
                      ))}
                    </div>
                  </div>
                </Field>

                <Field label="Attori preferiti">
                  <div className="space-y-2">
                    <div className="flex gap-2">
                      <input
                        className={inputClass}
                        placeholder="Attore..."
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            addManualListItem("manualFavoriteActors", e.target.value);
                            e.target.value = "";
                          }
                        }}
                      />
                      <button
                        type="button"
                        onClick={(e) => {
                          const input = e.target.previousSibling;
                          addManualListItem("manualFavoriteActors", input.value);
                          input.value = "";
                        }}
                        className="rounded-xl bg-slate-900 text-white px-3"
                      >
                        Aggiungi
                      </button>
                    </div>
                    <div className="space-y-1">
                      {(state.manualFavoriteActors || []).map((t) => (
                        <div key={t} className="flex justify-between bg-slate-100 rounded px-2 py-1 text-sm">
                          <span>{t}</span>
                          <button type="button" onClick={() => removeManualListItem("manualFavoriteActors", t)}>✕</button>
                        </div>
                      ))}
                    </div>
                  </div>
                </Field>
                <Field label="Pagine TMDb film">
                  <input className={inputClass} type="number" min="1" value={state.moviePages} onChange={(e) => update("moviePages", Number(e.target.value))} />
                </Field>
                <Field label="Pagine TMDb serie">
                  <input className={inputClass} type="number" min="1" value={state.tvPages} onChange={(e) => update("tvPages", Number(e.target.value))} />
                </Field>
                <Field label="Lingua TMDb">
                  <input className={inputClass} value={state.language} onChange={(e) => update("language", e.target.value)} />
                </Field>
                <Field label="Regione TMDb">
                  <input className={inputClass} value={state.region} onChange={(e) => update("region", e.target.value)} />
                </Field>
                <Field label="Giorni osservazione">
                  <input className={inputClass} type="number" min="1" value={state.observationDays} onChange={(e) => update("observationDays", Number(e.target.value))} />
                </Field>
                <Field label="Soglia suggerimento">
                  <input className={inputClass} type="number" min="0" value={state.suggestThreshold} onChange={(e) => update("suggestThreshold", Number(e.target.value))} />
                </Field>
                <Field label="Soglia auto-download">
                  <input className={inputClass} type="number" min="0" value={state.autoDownloadThreshold} onChange={(e) => update("autoDownloadThreshold", Number(e.target.value))} />
                </Field>
                <Field label="Movie suggest threshold">
                  <input className={inputClass} type="number" min="0" step="0.1" value={state.movieSuggestThreshold} onChange={(e) => update("movieSuggestThreshold", Number(e.target.value))} />
                </Field>
                <Field label="Movie auto-download threshold">
                  <input className={inputClass} type="number" min="0" step="0.1" value={state.movieAutoDownloadThreshold} onChange={(e) => update("movieAutoDownloadThreshold", Number(e.target.value))} />
                </Field>
                <Field label="Director weight">
                  <input className={inputClass} type="number" min="0" step="0.1" value={state.directorWeight} onChange={(e) => update("directorWeight", Number(e.target.value))} />
                </Field>
                <Field label="Writer weight">
                  <input className={inputClass} type="number" min="0" step="0.1" value={state.writerWeight} onChange={(e) => update("writerWeight", Number(e.target.value))} />
                </Field>
                <Field label="Cast weight">
                  <input className={inputClass} type="number" min="0" step="0.1" value={state.castWeight} onChange={(e) => update("castWeight", Number(e.target.value))} />
                </Field>
                <Field label="Director match bonus">
                  <input className={inputClass} type="number" min="0" step="0.1" value={state.directorMatchBonus} onChange={(e) => update("directorMatchBonus", Number(e.target.value))} />
                </Field>
                <Field label="Writer match bonus">
                  <input className={inputClass} type="number" min="0" step="0.1" value={state.writerMatchBonus} onChange={(e) => update("writerMatchBonus", Number(e.target.value))} />
                </Field>
                <Field label="Cast match bonus">
                  <input className={inputClass} type="number" min="0" step="0.1" value={state.castMatchBonus} onChange={(e) => update("castMatchBonus", Number(e.target.value))} />
                </Field>
                <Field label="Cast top N">
                  <input className={inputClass} type="number" min="1" step="1" value={state.castTopN} onChange={(e) => update("castTopN", Number(e.target.value))} />
                </Field>
                <Field label="Path esecuzione">
                  <select className={inputClass} value={state.executionPath} onChange={(e) => update("executionPath", e.target.value)}>
                    <option value="peppule">Peppule (raccomandato)</option>
                    <option value="wrapper">Wrapper diretto (fallback legacy)</option>
                  </select>
                </Field>
              </div>
            </Section>
          </div>

          <div className="space-y-6">
            <Section title="Azioni" subtitle="Queste azioni chiamano davvero il backend.">
              <div className="space-y-3">
                <button onClick={saveConfig} className="w-full rounded-xl bg-slate-900 text-white px-4 py-3 text-sm font-semibold hover:bg-slate-800">Salva configurazione</button>
                <button onClick={buildCatalog} className="w-full rounded-xl bg-indigo-600 text-white px-4 py-3 text-sm font-semibold hover:bg-indigo-700">Build catalogo esterno</button>
                <button onClick={() => runAgent(false)} className="w-full rounded-xl bg-emerald-600 text-white px-4 py-3 text-sm font-semibold hover:bg-emerald-700">Run agent (dry)</button>
                <button onClick={() => runAgent(true)} className="w-full rounded-xl bg-rose-600 text-white px-4 py-3 text-sm font-semibold hover:bg-rose-700">Run agent (execute)</button>
                <button onClick={loadDownloadQueue} className="w-full rounded-xl bg-blue-600 text-white px-4 py-3 text-sm font-semibold hover:bg-blue-700">Mostra coda download</button>
                <button onClick={simulateRun} className="w-full rounded-xl bg-amber-500 text-white px-4 py-3 text-sm font-semibold hover:bg-amber-600">Simula lato UI</button>
                <button onClick={resetAll} className="w-full rounded-xl bg-slate-200 text-slate-900 px-4 py-3 text-sm font-semibold hover:bg-slate-300">Reset valori UI</button>
              </div>

              <div className="pt-2 text-sm text-slate-600">
                <div className="flex items-center justify-between py-1">
                  <span>Ultimo salvataggio</span>
                  <span className="font-medium text-slate-900">{savedAt || "non ancora"}</span>
                </div>
                <div className="flex items-center justify-between py-1">
                  <span>TMDb</span>
                  <span className="font-medium text-slate-900">{state.useTmdb ? "attivo" : "spento"}</span>
                </div>
                <div className="flex items-center justify-between py-1">
                  <span>Playback Reporting</span>
                  <span className="font-medium text-slate-900">{state.usePlaybackReporting ? "attivo" : "spento"}</span>
                </div>
                <div className="flex items-center justify-between py-1">
                  <span>Path</span>
                  <span className="font-medium text-slate-900">{state.executionPath}</span>
                </div>
              </div>
            </Section>

            <Section title="Log rapido" subtitle="Qui vedi le risposte vere del backend.">
              <div className="rounded-2xl bg-slate-950 text-slate-100 p-4 text-xs leading-5 max-h-[640px] overflow-auto space-y-3">
                {logs.map((line, i) => (
                  <pre key={i} className="whitespace-pre-wrap break-words font-mono">{line}</pre>
                ))}
              </div>
            </Section>
          </div>
        </div>
      </div>
    </div>
  );
}


const rootEl = document.getElementById("root");
if (rootEl) {
  ReactDOM.createRoot(rootEl).render(<JellyfinNovitaGUIConnected />);
}
