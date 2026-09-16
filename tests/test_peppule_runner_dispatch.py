import importlib.util
import os
import unittest
from unittest.mock import patch

ROOT = "/home/bandi/ralf-media-peppule-series-intent"
os.environ["PEPPULE_BASE"] = ROOT + "/services/peppule_runtime"
spec = importlib.util.spec_from_file_location("runner", ROOT + "/tools/local_peppule_runner.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class RunnerDispatchTests(unittest.TestCase):
    def test_legacy_movie_entrypoint_reclassifies_all_seasons_english(self):
        calls = []
        def fake_all(title, *, language="ita", replacement=False):
            calls.append((title, language, replacement))
            return "SERIES_ALL_OK"
        with patch.object(runner, "run_all_seasons", fake_all):
            runner.main(["runner", "movie", "rick and morty tutte le stagioni in inglese"])
        self.assertEqual(calls, [("rick and morty", "eng", False)])


if __name__ == "__main__":
    unittest.main()
