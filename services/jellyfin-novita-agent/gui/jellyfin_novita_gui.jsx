export default function JellyfinNovitaGUI() {
  const defaultState = {
    enabled: true,
    mode: "scheduled",
    frequency: "daily",
    time: "03:30",
    timezone: "Europe/Rome",
    maxPerDay: 3,
    observationDays: 90,
    suggestThreshold: 12,
    autoDownloadThreshold: 30,
    executionPath: "peppule",
    useTmdb: true,
    useSeedCatalog: true,
    usePlaybackReporting: false,
    useSampleViews: true,
    moviePages: 1,
    tvPages: 1,
    language: "it-IT",
    region: "IT",
    dryRun: true,
    notifyOnRun: true,
    notifyOnError: true,
    saveHistory: true,
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
  const [logs, setLogs] = React.useState([
    "GUI pronta: scheduler, soglie, sorgenti e API.",
    "Catalogo esterno: seed manuale + TMDb.",
    "Path consigliato: Peppule quando l'entrypoint macchina sarà pronto.",
  ]);

  const update = (key, value) => setState((s) => ({ ...s, [key]: value }));
  const updateApi = (key, value) =>
    setState((s) => ({ ...s, api: { ...s.api, [key]: value } }));

  const masked = (value) => {
    if (!value) return "non impostato";
    if (value.length <= 8) return "••••••••";
    return `${value.slice(0, 4)}••••${value.slice(-4)}`;
  };

  const saveConfig = () => {
    const payload = {
      scheduler: {
        enabled: state.enabled,
        mode: state.mode,
        frequency: state.frequency,
        time: state.time,
        timezone: state.timezone,
      },
      scoring: {
        observationDays: state.observationDays,
        suggestThreshold: state.suggestThreshold,
        autoDownloadThreshold: state.autoDownloadThreshold,
        maxPerDay: state.maxPerDay,
      },
      sources: {
        tmdb: state.useTmdb,
        seedCatalog: state.useSeedCatalog,
        playbackReporting: state.usePlaybackReporting,
        sampleViews: state.useSampleViews,
        moviePages: state.moviePages,
        tvPages: state.tvPages,
        language: state.language,
        region: state.region,
      },
      execution: {
        path: state.executionPath,
        dryRun: state.dryRun,
      },
      api: state.api,
      notifications: {
        onRun: state.notifyOnRun,
        onError: state.notifyOnError,
      },
      persistence: {
        saveHistory: state.saveHistory,
      },
    };

    setSavedAt(new Date().toLocaleString("it-IT"));
    setLogs((prev) => [
      `Configurazione salvata in memoria UI: ${new Date().toLocaleTimeString("it-IT")}`,
      JSON.stringify(payload, null, 2),
      ...prev,
    ]);
  };

  const simulateRun = () => {
    setLogs((prev) => [
      `Run simulato: mode=${state.mode}, path=${state.executionPath}, dryRun=${state.dryRun}`,
      `Scheduler ${state.enabled ? "attivo" : "disattivo"} • frequenza=${state.frequency} • orario=${state.time}`,
      `Sorgenti: TMDb=${state.useTmdb} • seed=${state.useSeedCatalog} • playback=${state.usePlaybackReporting}`,
      `API: Jellyfin=${state.api.jellyfinUrl || "n/d"} • Wrapper=${state.api.wrapperUrl || "n/d"} • Cheshire=${state.api.cheshireWsUrl || "n/d"}`,
      ...prev,
    ]);
  };

  const testConnection = (name, value) => {
    setLogs((prev) => [
      `Test ${name}: ${value ? "configurato" : "manca valore"}`,
      ...prev,
    ]);
  };

  const resetAll = () => {
    setState(defaultState);
    setLogs((prev) => ["Configurazione riportata ai valori iniziali.", ...prev]);
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
                GUI per attivare il job, impostare orari, scegliere le sorgenti esterne, configurare API e decidere se eseguire il flusso su Peppule o direttamente sul wrapper.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <span className={pill(state.enabled)}>{state.enabled ? "Scheduler attivo" : "Scheduler spento"}</span>
              <span className={pill(state.executionPath === "peppule")}>{state.executionPath === "peppule" ? "Path: Peppule" : "Path: Wrapper"}</span>
              <span className={pill(state.dryRun)}>{state.dryRun ? "Dry run" : "Esecuzione reale"}</span>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
          <div className="xl:col-span-2 space-y-6">
            <Section title="Scheduler" subtitle="Accensione, cadenza e fascia oraria del job automatico.">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <Field label="Abilita job automatico">
                  <button
                    onClick={() => update("enabled", !state.enabled)}
                    className={`w-full rounded-xl px-4 py-2.5 text-sm font-medium transition ${state.enabled ? "bg-emerald-600 text-white hover:bg-emerald-700" : "bg-slate-200 text-slate-800 hover:bg-slate-300"}`}
                  >
                    {state.enabled ? "Attivo" : "Disattivo"}
                  </button>
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

                <Field label="Massimo download/giorno" hint="Cap giornaliero">
                  <input className={inputClass} type="number" min="0" value={state.maxPerDay} onChange={(e) => update("maxPerDay", Number(e.target.value))} />
                </Field>
              </div>
            </Section>

            <Section title="API e credenziali" subtitle="Qui inserisci URL, token e chiavi senza toccare i file di configurazione a mano.">
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
                <button onClick={() => testConnection("Jellyfin", state.api.jellyfinUrl && state.api.jellyfinToken)} className="rounded-xl bg-slate-900 text-white px-4 py-2.5 text-sm font-medium hover:bg-slate-800">Test Jellyfin</button>
                <button onClick={() => testConnection("Wrapper", state.api.wrapperUrl && state.api.wrapperApiKey)} className="rounded-xl bg-slate-900 text-white px-4 py-2.5 text-sm font-medium hover:bg-slate-800">Test Wrapper</button>
                <button onClick={() => testConnection("Cheshire", state.api.cheshireWsUrl)} className="rounded-xl bg-slate-900 text-white px-4 py-2.5 text-sm font-medium hover:bg-slate-800">Test Cheshire</button>
                <button onClick={() => testConnection("TMDb", state.api.tmdbBearer || state.api.tmdbApiKey)} className="rounded-xl bg-slate-900 text-white px-4 py-2.5 text-sm font-medium hover:bg-slate-800">Test TMDb</button>
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
                <Field label="Path esecuzione">
                  <select className={inputClass} value={state.executionPath} onChange={(e) => update("executionPath", e.target.value)}>
                    <option value="peppule">Peppule / Cheshire Cat</option>
                    <option value="wrapper">Wrapper diretto</option>
                  </select>
                </Field>
              </div>
            </Section>
          </div>

          <div className="space-y-6">
            <Section title="Azioni" subtitle="Salvataggio, simulazione e reset rapido.">
              <div className="space-y-3">
                <button onClick={saveConfig} className="w-full rounded-xl bg-slate-900 text-white px-4 py-3 text-sm font-semibold hover:bg-slate-800">Salva configurazione</button>
                <button onClick={simulateRun} className="w-full rounded-xl bg-emerald-600 text-white px-4 py-3 text-sm font-semibold hover:bg-emerald-700">Simula run</button>
                <button onClick={() => update("dryRun", !state.dryRun)} className={`w-full rounded-xl px-4 py-3 text-sm font-semibold ${state.dryRun ? "bg-amber-500 text-white hover:bg-amber-600" : "bg-rose-600 text-white hover:bg-rose-700"}`}>{state.dryRun ? "Passa a esecuzione reale" : "Torna a dry run"}</button>
                <button onClick={resetAll} className="w-full rounded-xl bg-slate-200 text-slate-900 px-4 py-3 text-sm font-semibold hover:bg-slate-300">Reset valori</button>
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

            <Section title="Log rapido" subtitle="Feedback locale della GUI. I test sono simulati lato frontend finché non agganci il backend.">
              <div className="rounded-2xl bg-slate-950 text-slate-100 p-4 text-xs leading-5 max-h-[520px] overflow-auto space-y-3">
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
