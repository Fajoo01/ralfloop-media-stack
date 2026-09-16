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
    return sys.modules["peppule_local.request_parser"], sys.modules["peppule_local.pipelines"]


class PeppuleRequestParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parser, cls.pipelines = load_runtime()

    def test_all_seasons_english(self):
        req = self.parser.parse_media_request("rick and morty tutte le stagioni in inglese")
        self.assertEqual((req.kind, req.title, req.language, req.season), ("series_all", "rick and morty", "eng", None))

    def test_all_seasons_default_italian(self):
        req = self.parser.parse_media_request("rick and morty tutte le stagioni")
        self.assertEqual((req.kind, req.title, req.language), ("series_all", "rick and morty", "ita"))

    def test_single_season_english(self):
        req = self.parser.parse_media_request("rick and morty stagione 3 in inglese")
        self.assertEqual((req.kind, req.title, req.language, req.season), ("series", "rick and morty", "eng", 3))

    def test_movie_default_italian(self):
        req = self.parser.parse_media_request("Shutter Island 2010")
        self.assertEqual((req.kind, req.title, req.language), ("movie", "Shutter Island 2010", "ita"))

    def test_english_word_in_title_is_not_audio_marker(self):
        req = self.parser.parse_media_request("The English Patient")
        self.assertEqual(req.language, "ita")
        self.assertFalse(self.pipelines._has_requested_audio_marker("The.English.Patient.1996.ITA.1080p.mkv", "eng"))
        self.assertTrue(self.pipelines._has_requested_audio_marker("The.English.Patient.1996.ENG.1080p.mkv", "eng"))


if __name__ == "__main__":
    unittest.main()
