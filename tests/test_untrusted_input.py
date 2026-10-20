"""Input from outside AI Playground: picture headers checked before decoding, videos read only as
local files in a real video container, drops and pastes read with a byte cap. No window is opened
(the drop reader runs with a fake drop and GLib's own streams)."""

import os
import shutil
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
if not (HERE / "ai_core.py").exists():
    sys.path.insert(0, str(HERE.parent))           # Asterism's playground/ folder: the core is one up
import chess_video  # noqa: E402
import picture_check  # noqa: E402


def png(path, width, height):
    raw = zlib.compress(b"\x00" * 4)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


class PictureHeaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_png_sizes(self):
        png(self.dir / "ok.png", 1920, 1080)
        png(self.dir / "bomb.png", 60000, 60000)
        png(self.dir / "wide.png", 20000, 10)
        self.assertEqual(picture_check.declared_size(self.dir / "ok.png"), (1920, 1080, 1))
        self.assertTrue(picture_check.acceptable(self.dir / "ok.png"))
        self.assertFalse(picture_check.acceptable(self.dir / "bomb.png"))     # 3.6 gigapixels in a few bytes
        self.assertFalse(picture_check.acceptable(self.dir / "wide.png"))

    def test_jpeg_gif_webp(self):
        sof = b"\xff\xd8" + b"\xff\xe1" + struct.pack(">H", 6) + b"Exif" + b"\xff\xc0" + struct.pack(">HBHH", 17, 8, 40000, 40000)
        (self.dir / "big.jpg").write_bytes(sof + b"\x00" * 20)
        self.assertEqual(picture_check.declared_size(self.dir / "big.jpg"), (40000, 40000, 1))
        self.assertFalse(picture_check.acceptable(self.dir / "big.jpg"))
        (self.dir / "a.gif").write_bytes(b"GIF89a" + struct.pack("<HH", 300, 200) + b"\x00" * 8)
        self.assertTrue(picture_check.acceptable(self.dir / "a.gif"))
        vp8x = b"RIFF\x00\x00\x00\x00WEBPVP8X" + b"\x00" * 8 + (16000).to_bytes(3, "little") + (16000).to_bytes(3, "little")
        (self.dir / "a.webp").write_bytes(vp8x)
        self.assertEqual(picture_check.declared_size(self.dir / "a.webp")[:2], (16001, 16001))
        self.assertFalse(picture_check.acceptable(self.dir / "a.webp"))

    def test_unknown_or_broken_is_refused(self):
        (self.dir / "x.png").write_bytes(b"not a picture")
        (self.dir / "cut.jpg").write_bytes(b"\xff\xd8\xff\xe1\x00")
        self.assertFalse(picture_check.acceptable(self.dir / "x.png"))
        self.assertFalse(picture_check.acceptable(self.dir / "cut.jpg"))
        self.assertFalse(picture_check.acceptable(self.dir / "missing.png"))

    def test_real_pictures_match_gdkpixbuf(self):
        import gi
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf
        png(self.dir / "r.png", 33, 21)
        _fmt, w, h = GdkPixbuf.Pixbuf.get_file_info(str(self.dir / "r.png"))
        self.assertEqual(picture_check.declared_size(self.dir / "r.png")[:2], (w, h))


class VideoInputTest(unittest.TestCase):
    def test_every_media_run_is_limited(self):
        argv = chess_video.media_argv("ffprobe", ["-v", "error"], "/videos/game.mp4", ["-of", "csv"])
        tool = argv.index("ffprobe")
        self.assertEqual(argv[argv.index("-protocol_whitelist") + 1], "file")
        self.assertEqual(argv[argv.index("-format_whitelist") + 1], chess_video.VIDEO_FORMATS)
        self.assertLess(argv.index("-format_whitelist"), argv.index("-i"))   # input options come first
        self.assertEqual(argv[argv.index("-i") + 1], "/videos/game.mp4")
        if shutil.which("prlimit"):
            self.assertEqual(argv[:tool], ["prlimit", f"--as={chess_video.MEDIA_MEMORY}", "--"])
        with self.assertRaises(ValueError):
            chess_video.media_argv("ffprobe", [], "-i.mp4", [])                # never an option

    @unittest.skipUnless(shutil.which("ffprobe"), "ffprobe not installed")
    def test_a_playlist_named_mp4_is_not_read(self):
        """An HLS playlist renamed .mp4 (its entries could be web addresses or other local files)."""
        with tempfile.TemporaryDirectory() as folder:
            fake = os.path.join(folder, "game.mp4")
            Path(fake).write_text("#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\nhttp://127.0.0.1:9/x.ts\n"
                                  "#EXT-X-ENDLIST\n")
            self.assertEqual(chess_video.duration(fake), 0.0)
            concat = os.path.join(folder, "game.mkv")
            Path(concat).write_text("ffconcat version 1.0\nfile '/etc/hostname'\n")
            self.assertEqual(chess_video.duration(concat), 0.0)


class DropReadTest(unittest.TestCase):
    """The chess tab's drop reader with a fake Gdk.Drop that serves a GLib memory stream."""

    def run_drop(self, payload: bytes):
        import gi
        gi.require_version("Gdk", "4.0")
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gdk, Gio, GLib
        import chess_import

        loop = GLib.MainLoop()
        seen = {"dropped": None, "toast": None, "finished": None}

        class FakeFormats:
            def get_mime_types(self):
                return ["text/uri-list"]

        class FakeDrop:
            def get_formats(self):
                return FakeFormats()

            def get_actions(self):
                return Gdk.DragAction.COPY

            def read_async(self, mimes, priority, cancel, callback):
                self.stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(payload))
                GLib.idle_add(lambda: (callback(self, None), False)[1])

            def read_finish(self, _result):
                return self.stream, "text/uri-list"

            def finish(self, action):
                seen["finished"] = action
                GLib.idle_add(loop.quit)

        class Host(chess_import.ChessImport):
            def chess_dropped(self, _t, text, _x, _y):
                seen["dropped"] = text

            def toast(self, text):
                seen["toast"] = text

        Host().chess_drop_read(FakeDrop())
        GLib.timeout_add_seconds(5, loop.quit)
        loop.run()
        return seen

    def test_a_link_is_read(self):
        seen = self.run_drop(b"https://www.youtube.com/watch?v=dQw4w9WgXcQ\r\n")
        self.assertIn("youtube.com", seen["dropped"])
        self.assertTrue(seen["finished"])

    def test_an_endless_drop_is_cut_off(self):
        import chess_import
        seen = self.run_drop(b"x" * (chess_import.MAX_DROP_TEXT * 4))
        self.assertIsNone(seen["dropped"])
        self.assertIsNotNone(seen["toast"])
        self.assertFalse(seen["finished"])                  # refused (no action)


if __name__ == "__main__":
    unittest.main()
