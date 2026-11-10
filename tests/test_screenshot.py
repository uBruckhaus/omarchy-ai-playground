"""F12 screenshots: a private folder only, new names only, complete files, notes next to the picture."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import screenshot  # noqa: E402


class ScreenshotFilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.where = self.root / "AI Playground"

    def tearDown(self):
        self.tmp.cleanup()

    def test_folder_is_made_private_and_names_never_reused(self):
        fd = screenshot.private_folder(self.where)
        try:
            self.assertEqual(os.stat(self.where).st_mode & 0o777, 0o700)
            first = screenshot.free_name(fd, "20261110-120000")
            self.assertEqual(first, "ai-playground-20261110-120000.png")
            screenshot.write_new(fd, first, b"png")
            self.assertEqual(screenshot.free_name(fd, "20261110-120000"), "ai-playground-20261110-120000-2.png")
            (self.where / "ai-playground-20261110-120000-2.txt").write_text("note")
            self.assertEqual(screenshot.free_name(fd, "20261110-120000"), "ai-playground-20261110-120000-3.png")
            self.assertEqual(os.stat(self.where / first).st_mode & 0o777, 0o600)
        finally:
            os.close(fd)

    def test_symlinked_folder_refused_and_nothing_written_there(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        self.where.symlink_to(elsewhere)
        with self.assertRaises(OSError):
            screenshot.private_folder(self.where)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_folder_writable_by_others_refused(self):
        self.where.mkdir()
        os.chmod(self.where, 0o777)
        with self.assertRaises(PermissionError):
            screenshot.private_folder(self.where)

    def test_failed_write_leaves_no_partial_file(self):
        fd = screenshot.private_folder(self.where)
        try:
            with mock.patch.object(screenshot.os, "fsync", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    screenshot.write_new(fd, "ai-playground-x.png", b"data")
            self.assertFalse((self.where / "ai-playground-x.png").exists())
        finally:
            os.close(fd)

    def test_write_refuses_an_existing_link(self):
        target = self.root / "keep"
        target.write_text("keep")
        fd = screenshot.private_folder(self.where)
        try:
            (self.where / "ai-playground-2.png").symlink_to(target)
            with self.assertRaises(OSError):
                screenshot.write_new(fd, "ai-playground-2.png", b"x")
        finally:
            os.close(fd)
        self.assertEqual(target.read_text(), "keep")

    def test_note_next_to_picture_and_none_when_empty(self):
        fd = screenshot.private_folder(self.where)
        os.close(fd)
        picture = self.where / "ai-playground-1.png"
        self.assertIsNone(screenshot.save_note(picture, "   "))
        self.assertFalse(picture.with_suffix(".txt").exists())
        note = screenshot.save_note(picture, " The gear is cut off ")
        self.assertEqual(note.read_text(), "The gear is cut off\n")
        self.assertEqual(os.stat(note).st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):      # never over an existing file
            screenshot.save_note(picture, "again")


if __name__ == "__main__":
    unittest.main()
