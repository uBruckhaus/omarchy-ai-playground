"""AI Playground's Generate tab: a picture from a text, made on this computer's GPU
(picture_gen: stable-diffusion.cpp with Z-Image-Turbo). The first use sets both up, with a
progress bar and Cancel (a cancelled download resumes next time). Pictures stay in a private
folder until the app closes; Save as… keeps one, Ask about it hands it to the Vision tab."""

from __future__ import annotations

import random
import shutil
import tempfile
import threading
from pathlib import Path

from gi.repository import Gio, GLib, Gtk

import ai_core
import picture_gen
from tabs import VISION_PROMPT
from i18n import N_, _

GENERATE_PROMPT = "A red fox sitting in a snowy forest at dawn, soft light, photograph"
SIZE_CHOICES = (("square", N_("Square · 1024 × 1024")), ("landscape", N_("Landscape · 1280 × 768")),
                ("portrait", N_("Portrait · 768 × 1280")))


def _gigabytes(size: int) -> str:
    return f"{size / 1024 ** 3:.1f} GB"


class GenerateTab:
    def build_generate_tab(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=ai_core.GAP)
        self.tab_intro(box, _("Generate"),
                       _("A picture from a text, made on this computer's graphics card with stable-diffusion.cpp "
                         "and the Z-Image-Turbo model: nothing leaves the computer."))
        # Set up: shown until the program and the model are there.
        self.gen_setup = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.gen_setup.add_css_class("am-help-card")
        self.gen_setup_text = Gtk.Label(xalign=0, wrap=True)
        self.gen_setup_text.add_css_class("am-detail-value")
        self.gen_setup.append(self.gen_setup_text)
        self.gen_setup_button = self.action_button(_("Set up"), self.gen_install)
        self.gen_setup.append(self.gen_setup_button)
        box.append(self.gen_setup)

        self.gen_prompt = self.prompt_view(GENERATE_PROMPT, 60)
        box.append(self.gen_prompt)
        row = Gtk.Box(spacing=8)
        row.add_css_class("am-chess-controls")
        self.gen_size = Gtk.DropDown.new_from_strings([_(label) for _k, label in SIZE_CHOICES])
        row.append(self.gen_size)
        self.gen_clear_button = self.small_button(_("Clear"), self.gen_clear, _("Remove the pictures"))
        self.gen_clear_button.set_sensitive(False)
        row.append(self.gen_clear_button)
        box.append(row)
        self.gen_button = self.action_button(_("Generate"), self.gen_start,
                                             _("Makes one picture; a new one each time (another seed)"))
        box.append(self.gen_button)

        # While it works: what it does, how far, Cancel.
        self.gen_busy = Gtk.Box(spacing=8)
        self.gen_progress = Gtk.ProgressBar(hexpand=True, valign=Gtk.Align.CENTER, show_text=True)
        self.gen_busy.append(self.gen_progress)
        self.gen_busy.append(self.small_button(_("Cancel"), self.gen_cancel))
        self.gen_busy.set_visible(False)
        box.append(self.gen_busy)

        self.gen_results = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=3,
                                       column_spacing=ai_core.GAP, row_spacing=ai_core.GAP, homogeneous=True)
        box.append(self.gen_results)
        self.gen_folder = tempfile.mkdtemp(prefix="ai-playground-generated-")     # private (0700)
        self.gen_count = 0
        self.gen_stop = None
        self.gen_refresh()
        return self.page_scroller(box)

    def gen_cleanup(self):
        self.gen_cancel()
        picture_gen.stop_all()                 # no download or picture outlives the app
        shutil.rmtree(getattr(self, "gen_folder", ""), ignore_errors=True)

    def gen_refresh(self):
        state = picture_gen.status()
        busy = self.gen_stop is not None
        self.gen_setup.set_visible(not state.ready)
        if not state.ready:
            parts = []
            if state.binary is None:
                parts.append(_("stable-diffusion.cpp (its Vulkan build from GitHub, about 40 MB)"))
            if state.missing:
                parts.append(_("the Z-Image-Turbo model with its text encoder ({0} from Hugging Face)")
                             .format(_gigabytes(state.to_download)))
            self.gen_setup_text.set_label(
                _("Needed first: {0}. Every file is checked against its published SHA-256 before it is kept; "
                  "they go to {1}. A cancelled download goes on where it stopped.")
                .format("; ".join(parts), str(picture_gen.DATA).replace(str(Path.home()), "~", 1)))
        self.gen_setup_button.set_sensitive(not busy)
        self.gen_button.set_sensitive(state.ready and not busy)

    # --- the work off the main thread ----------------------------------------------------------------

    def gen_run(self, label: str, work, done):
        """work(progress, cancelled) in a thread; done(error or None, value) back on the main thread."""
        stop = threading.Event()
        self.gen_stop = stop
        self.gen_progress.set_fraction(0)
        self.gen_progress.set_text(label)
        self.gen_busy.set_visible(True)
        self.gen_refresh()

        def progress(fraction, text=None):
            GLib.idle_add(self.gen_show_progress, stop, fraction, f"{label} {text}" if text else label)

        def thread():
            try:
                value, error = work(progress, stop.is_set), None
            except picture_gen.Cancelled:
                value, error = None, ""
            except (OSError, RuntimeError, ValueError) as failure:
                value, error = None, str(failure)
            GLib.idle_add(finish, error, value)

        def finish(error, value):
            if self.gen_stop is stop:
                self.gen_stop = None
                self.gen_busy.set_visible(False)
            done(error, value)
            self.gen_refresh()
            return False
        threading.Thread(target=thread, daemon=True).start()

    def gen_show_progress(self, stop, fraction, text):
        if self.gen_stop is stop:
            self.gen_progress.set_fraction(max(0.0, min(1.0, fraction)))
            self.gen_progress.set_text(text)
        return False

    def gen_cancel(self):
        if self.gen_stop is not None:
            self.gen_stop.set()

    def gen_install(self):
        def work(progress, cancelled):
            if picture_gen.binary() is None:
                picture_gen.install_program(lambda f: progress(f, _("(program)")), cancelled)
            picture_gen.install_model(lambda f: progress(f, f"({int(f * 100)} %)"), cancelled)

        def done(error, _value):
            if error is None:
                self.toast(_("Generate is set up."))
            elif error:
                self.toast(_("Setting up stopped: {0}").format(error))

        self.gen_run(_("Downloading…"), work, done)

    # --- pictures ------------------------------------------------------------------------------------

    def gen_start(self):
        prompt = self.view_text(self.gen_prompt)
        if not prompt:
            self.toast(_("Write what the picture should show first."))
            return
        size = SIZE_CHOICES[max(0, self.gen_size.get_selected())][0]
        seed = random.randrange(1, 2 ** 31)
        self.gen_count += 1
        out = Path(self.gen_folder) / f"picture-{self.gen_count}.png"

        def work(progress, cancelled):
            progress(0, _("loading the model"))
            return picture_gen.generate(prompt, out, progress, cancelled, size, seed)

        def done(error, seconds):
            if error is None:
                self.gen_add(out, prompt, seconds, seed)
            elif error:
                self.toast(_("No picture: {0}").format(error))

        self.gen_run(_("Generating…"), work, done)

    def gen_add(self, path: Path, prompt: str, seconds: float, seed: int):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.add_css_class("am-help-card")
        picture = Gtk.Picture.new_for_filename(str(path))
        picture.set_size_request(300, 300)
        picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        picture.set_tooltip_text(prompt)
        card.append(picture)
        info = Gtk.Label(label=f"{seconds:.1f} s · " + _("seed {0}").format(seed), xalign=0)
        info.add_css_class("am-hint")
        card.append(info)
        buttons = Gtk.Box(spacing=6)
        buttons.append(self.small_button(_("Save as…"), lambda: self.gen_save(path)))
        buttons.append(self.small_button(_("Ask about it"), lambda: self.gen_to_vision(path),
                                         _("Opens it on the Vision tab, for the agents that can see")))
        card.append(buttons)
        self.gen_results.insert(card, 0)                        # the newest first
        self.gen_clear_button.set_sensitive(True)

    def gen_clear(self):
        self.gen_results.remove_all()
        for child in Path(self.gen_folder).glob("picture-*.png"):
            child.unlink(missing_ok=True)
        self.gen_clear_button.set_sensitive(False)

    def gen_save(self, path: Path):
        dialog = Gtk.FileDialog(title=_("Save the picture"), initial_name="picture.png")

        def chosen(d, result):
            try:
                file = d.save_finish(result)
            except GLib.Error:
                return
            try:
                file.replace_contents(path.read_bytes(), None, False, Gio.FileCreateFlags.NONE, None)
                self.toast(_("Saved: {0}").format(file.get_basename()))
            except (GLib.Error, OSError) as error:
                self.toast(getattr(error, "message", str(error)))
        dialog.save(self.window, None, chosen)

    def gen_to_vision(self, path: Path):
        self.vision_clear()
        self.vision_load([(str(path), "generated")], _("That picture cannot be read."), VISION_PROMPT)
        self.show_page("vision")
