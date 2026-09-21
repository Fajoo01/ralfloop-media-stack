import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).parents[1] / "jellyfin_media_verify.py"
spec = importlib.util.spec_from_file_location("jellyfin_media_verify", MODULE_PATH)
media_verify = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(media_verify)


def probe(duration=6300.0, format_name="mov,mp4,m4a,3gp,3g2,mj2"):
    return {
        "ok": True,
        "duration": duration,
        "format_name": format_name,
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
            {"codec_type": "audio", "codec_name": "aac", "channels": 2},
        ],
    }


class StructureGateTests(unittest.TestCase):
    def make_movie(self, root, suffix=".mp4"):
        film = Path(root) / "Film"
        film.mkdir()
        path = film / f"movie{suffix}"
        path.write_bytes(b"x")
        return path

    def test_rejects_implausible_movie_duration(self):
        with tempfile.TemporaryDirectory() as td:
            path = self.make_movie(td)
            with mock.patch.object(media_verify, "ffprobe", return_value=probe(duration=24 * 3600)):
                result = media_verify.verify_path(str(path), sample_seconds=1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "movie_duration_implausible")

    def test_rejects_container_extension_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            path = self.make_movie(td)
            with mock.patch.object(media_verify, "ffprobe", return_value=probe(format_name="avi")):
                result = media_verify.verify_path(str(path), sample_seconds=1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "container_extension_mismatch")

    def test_accepts_sane_mp4_structure(self):
        with tempfile.TemporaryDirectory() as td:
            path = self.make_movie(td)
            with mock.patch.object(media_verify, "ffprobe", return_value=probe()), \
                 mock.patch.object(media_verify, "decode_sample", return_value={"ok": True, "stderr": ""}), \
                 mock.patch.object(media_verify, "audio_sync_check", return_value={"ok": True, "status": "ok"}):
                result = media_verify.verify_path(str(path), sample_seconds=1)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "ok")


if __name__ == "__main__":
    unittest.main()
