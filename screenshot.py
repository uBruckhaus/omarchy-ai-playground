"""F12: a picture of AI Playground's window, for telling what is wrong (as Maprisma's screenshot key).

The picture goes to ~/Pictures/AI Playground/ai-playground-<date>-<time>.png (never over an older
one); a dialog then names the file and takes an optional note, written next to it as a .txt with
the picture's name. Settings › F12 saves a screenshot turns the key off. Nothing is uploaded and no
other program is started: the window draws itself into the picture (Gtk.WidgetPaintable)."""

from __future__ import annotations

import os
import time
from pathlib import Path

from gi.repository import Adw, GLib, Gtk

import ai_core
from i18n import _

NOTE_LIMIT = 4000          # characters of a note: a few sentences, not a document


def folder() -> Path:
    pictures = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
    return Path(pictures or Path.home() / "Pictures") / "AI Playground"


def free_name(where: Path, stamp: str) -> Path:
    """ai-playground-<stamp>.png, or -2, -3 … when one already has that name."""
    path = where / f"ai-playground-{stamp}.png"
    n = 2
    while path.exists() or path.with_suffix(".txt").exists():
        path = where / f"ai-playground-{stamp}-{n}.png"
        n += 1
    return path


def write_new(path: Path, data: bytes):
    """A new file of our own (never through a link, never over an existing file)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(data)


def save_note(picture: Path, text: str) -> Path | None:
    """The note next to the picture (same name, .txt); no text, no file."""
    text = text.strip()[:NOTE_LIMIT]
    if not text:
        return None
    note = picture.with_suffix(".txt")
    write_new(note, (text + "\n").encode("utf-8"))
    return note


class ScreenshotKey:
    def screenshot_on(self) -> bool:
        return bool(ai_core.setting("screenshot_key"))

    def take_screenshot(self):
        """F12: the window as it is now into a PNG, then the dialog for a note."""
        window = self.window
        width, height = window.get_width(), window.get_height()
        if width <= 0 or height <= 0:
            return
        try:
            paintable = Gtk.WidgetPaintable.new(window)
            snapshot = Gtk.Snapshot()
            paintable.snapshot(snapshot, width, height)
            node = snapshot.to_node()
            if node is None:
                raise OSError(_("The window drew nothing."))
            texture = window.get_renderer().render_texture(node, None)
            where = folder()
            where.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = free_name(where, time.strftime("%Y%m%d-%H%M%S"))
            write_new(path, texture.save_to_png_bytes().get_data())
        except (OSError, GLib.Error) as error:
            self.toast(_("The screenshot could not be saved: {0}").format(getattr(error, "message", None) or error))
            return
        self.screenshot_note(path)

    def screenshot_note(self, path: Path):
        dialog = Adw.AlertDialog(heading=_("Screenshot saved"),
                                 body=_("Saved as {0}").format(str(path).replace(str(Path.home()), "~", 1)))
        entry = Gtk.Entry(placeholder_text=_("What is wrong? (optional)"), activates_default=True,
                          max_length=NOTE_LIMIT)
        dialog.set_extra_child(entry)
        dialog.add_response("done", _("Done"))
        dialog.set_default_response("done")
        dialog.set_close_response("done")

        def done(_dialog, _response):
            try:
                save_note(path, entry.get_text())
            except OSError as error:
                self.toast(_("The note could not be saved: {0}").format(error.strerror or error))
        dialog.connect("response", done)
        dialog.present(self.window)
