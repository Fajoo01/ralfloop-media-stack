import importlib.util
import pathlib
import sys
import types
import unittest

BASE = pathlib.Path(__file__).resolve().parents[1] / "services" / "peppule_runtime"


def load_runtime():
    pkg = types.ModuleType("peppule_local")
    pkg.__path__ = [str(BASE)]
    sys.modules["peppule_local"] = pkg
    for name in ("utils", "settings", "request_parser", "services", "pipelines"):
        spec = importlib.util.spec_from_file_location(f"peppule_local.{name}", BASE / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"peppule_local.{name}"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["peppule_local.pipelines"]


class DummyCat:
    def llm(self, prompt):
        return '{"title":"Rick and Morty","episodes":2}'


class LanguageRoutingTests(unittest.TestCase):
    def _run_series_and_capture(self, language):
        p = load_runtime()
        calls = []
        deep = []
        p.ask_llm_series_info = lambda *a, **k: ("Rick and Morty", 2, {})
        p._catalog_series_aliases = lambda *a, **k: []
        p._catalog_series_episode_count = lambda *a, **k: 2
        p.check_jellyfin_episode = lambda *a, **k: False
        p.write_cache = lambda *a, **k: None
        p.execute_download_amule = lambda *a, **k: {"ok": False, "status": "blocked"}
        def search(q, preferred_language="ita"):
            calls.append((q, preferred_language))
            return []
        def deep_search(q):
            deep.append(q)
            return []
        p.execute_search_amule = search
        p._deep_amule_job_search = deep_search
        out = p.pipeline_series(DummyCat(), "Rick and Morty", 1, max_episodes=2, language=language)
        return calls, deep, out

    def test_english_propagates_to_all_series_queries(self):
        calls, deep, out = self._run_series_and_capture("eng")
        self.assertTrue(calls)
        self.assertTrue(all(lang == "eng" for _, lang in calls))
        self.assertTrue(all("eng" in q.lower().split() for q, _ in calls))
        self.assertTrue(all("eng" in q.lower().split() for q in deep))
        self.assertIn("audio ENG", out)

    def test_default_italian_stays_italian(self):
        calls, deep, out = self._run_series_and_capture("ita")
        self.assertTrue(calls)
        self.assertTrue(all(lang == "ita" for _, lang in calls))
        self.assertTrue(all("ita" in q.lower().split() for q, _ in calls))
        self.assertTrue(all("ita" in q.lower().split() for q in deep))
        self.assertIn("audio ITA", out)


if __name__ == "__main__":
    unittest.main()
