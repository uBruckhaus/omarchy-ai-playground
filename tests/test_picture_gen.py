"""The Generate tab's engine: what sd-cli is started with, what a release zip may hold, when it
counts as set up, and that nothing is downloaded or kept without a matching checksum. Nothing is
downloaded and no picture is made."""

import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import picture_gen as pg  # noqa: E402


class PictureGenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)

    def set_up_program(self):
        folder = self.data / "sd" / "master-1"
        folder.mkdir(parents=True)
        exe = folder / "sd-cli"
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
        (self.data / "sd" / "current").symlink_to("master-1")
        return exe

    def test_status(self):
        state = pg.status(self.data)
        self.assertFalse(state.ready)
        self.assertIsNone(state.binary)
        self.assertEqual(len(state.missing), 3)
        self.assertEqual(state.to_download, sum(f[2] for f in pg.MODEL_FILES))
        self.assertEqual(self.set_up_program(), pg.binary(self.data))
        # A model file of the wrong size does not count; a link at its name neither.
        entry = pg.MODEL_FILES[1]
        path = pg.model_path(entry, self.data)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"x")
        self.assertIn(entry, pg.status(self.data).missing)
        path.unlink()
        path.symlink_to(self.data / "elsewhere")
        self.assertIn(entry, pg.status(self.data).missing)

    def test_current_link_must_name_a_plain_folder(self):
        (self.data / "sd").mkdir()
        (self.data / "sd" / "current").symlink_to("../../etc")
        self.assertIsNone(pg.binary(self.data))

    def test_argv(self):
        exe = self.set_up_program()
        argv = pg.build("  a red\nfox ", Path("/tmp/x.png"), "landscape", 7, self.data)
        self.assertEqual(argv[0], str(exe))
        self.assertEqual(argv[-2:], ["-p", "a red fox"])                # the prompt: one argument, last
        self.assertEqual(argv[argv.index("-W") + 1], "1280")
        self.assertEqual(argv[argv.index("-H") + 1], "768")
        self.assertEqual(argv[argv.index("-s") + 1], "7")
        self.assertEqual(argv[argv.index("--steps") + 1], "8")
        self.assertEqual(argv[argv.index("--diffusion-model") + 1],
                         str(self.data / "models/z-image-turbo/z_image_turbo-Q8_0.gguf"))
        self.assertEqual(len(pg.build("x" * 5000, Path("/tmp/x.png"), data=self.data)[-1]), pg.MAX_PROMPT)
        with self.assertRaises(ValueError):
            pg.build("   ", Path("/tmp/x.png"), data=self.data)

    def test_argv_needs_the_program(self):
        with self.assertRaises(RuntimeError):
            pg.build("fox", Path("/tmp/x.png"), data=self.data)

    def zip_with(self, entries):
        path = self.data / "r.zip"
        with zipfile.ZipFile(path, "w") as zf:
            for name, text in entries:
                zf.writestr(name, text)
        return path

    def test_unpack(self):
        pg.unpack(self.zip_with([("sd-cli", "bin"), ("libggml.so.0", "lib")]), self.data / "out")
        self.assertTrue(os.access(self.data / "out/sd-cli", os.X_OK))
        self.assertFalse(os.access(self.data / "out/libggml.so.0", os.X_OK))
        for bad in ("../evil", "dir/file", ".hidden", "a b"):
            with self.subTest(bad=bad):
                with self.assertRaises(RuntimeError):
                    pg.unpack(self.zip_with([(bad, "x")]), self.data / f"out-{abs(hash(bad))}")

    def test_unpack_refuses_links(self):
        path = self.data / "l.zip"
        with zipfile.ZipFile(path, "w") as zf:
            info = zipfile.ZipInfo("sd-cli")
            info.external_attr = (0o120777 << 16)
            zf.writestr(info, "/etc/passwd")
        with self.assertRaises(RuntimeError):
            pg.unpack(path, self.data / "out")

    def test_no_download_without_checksum(self):
        with self.assertRaises(RuntimeError):
            pg.download("https://example.org/x", self.data / "x", 10, "", lambda f: None, lambda: False)
        self.assertFalse((self.data / "x.part").exists())

    def test_a_wrong_file_is_not_kept(self):
        """A complete .part whose bytes do not match: no curl run, and nothing at the real name."""
        target = self.data / "m.gguf"
        (self.data / "m.gguf.part").write_bytes(b"0123456789")
        with self.assertRaises(RuntimeError):
            pg.download("https://example.org/m", target, 10, "0" * 64, lambda f: None, lambda: False)
        self.assertFalse(target.exists())

    def test_a_planted_link_is_refused(self):
        (self.data / "m.gguf.part").symlink_to(self.data / "victim")
        with self.assertRaises(OSError):
            pg.download("https://example.org/m", self.data / "m.gguf", 10, "0" * 64, lambda f: None, lambda: False)
        self.assertFalse((self.data / "victim").exists())

    def test_one_download_per_file(self):
        import fcntl
        (self.data / "m.gguf.part").write_bytes(b"0")
        holder = os.open(self.data / "m.gguf.part", os.O_RDONLY)
        self.addCleanup(os.close, holder)
        fcntl.flock(holder, fcntl.LOCK_EX)
        with self.assertRaisesRegex(RuntimeError, "already"):
            pg.download("https://example.org/m", self.data / "m.gguf", 10, "0" * 64, lambda f: None, lambda: False)

    def test_stop_all_ends_what_runs(self):
        import subprocess
        proc = subprocess.Popen(["sleep", "30"])
        pg._RUNNING.add(proc)
        pg.stop_all()
        self.assertIsNotNone(proc.wait(timeout=5))
        pg._RUNNING.discard(proc)

    def test_pinned_models(self):
        for repo, path, size, sha in pg.MODEL_FILES:
            self.assertRegex(sha, r"^[0-9a-f]{64}$")
            self.assertGreater(size, 0)
            self.assertNotIn("..", path)


if __name__ == "__main__":
    unittest.main()
