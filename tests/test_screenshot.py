"""F12 screenshots: new names only, notes next to the picture, no file for an empty note."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import screenshot  # noqa: E402


class ScreenshotFilesTest(unittest.TestCase):
    def test_free_name_never_reuses_a_picture_or_note(self):
        with tempfile.TemporaryDirectory() as folder:
            where = Path(folder)
            first = screenshot.free_name(where, "20261110-120000")
            self.assertEqual(first.name, "ai-playground-20261110-120000.png")
            first.write_bytes(b"x")
            second = screenshot.free_name(where, "20261110-120000")
            self.assertEqual(second.name, "ai-playground-20261110-120000-2.png")
            (where / "ai-playground-20261110-120000-2.txt").write_text("note")
            self.assertEqual(screenshot.free_name(where, "20261110-120000").name, "ai-playground-20261110-120000-3.png")

    def test_note_next_to_picture_and_none_when_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            picture = Path(folder) / "ai-playground-1.png"
            self.assertIsNone(screenshot.save_note(picture, "   "))
            self.assertFalse(picture.with_suffix(".txt").exists())
            note = screenshot.save_note(picture, " The gear is cut off ")
            self.assertEqual(note.read_text(), "The gear is cut off\n")
            self.assertEqual(os.stat(note).st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):      # never over an existing file
                screenshot.save_note(picture, "again")

    def test_write_new_refuses_a_link(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "elsewhere"
            target.write_text("keep")
            link = Path(folder) / "ai-playground-2.png"
            link.symlink_to(target)
            with self.assertRaises(OSError):
                screenshot.write_new(link, b"x")
            self.assertEqual(target.read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
