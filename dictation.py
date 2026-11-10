"""Speak instead of typing: a microphone button beside the question fields.

It uses the desktop's own dictation (Omarchy: `omarchy-dictation`, the backend chosen in
Setup › Defaults › Dictation, e.g. Superwhisper or voxtype). A click puts the cursor in the field
and starts the dictation; the backend writes what is said at the cursor; a second click stops it.
AI Playground records nothing itself, starts no other program and sends nothing anywhere; where the
words are recognised (on this PC or a cloud service) is the backend's own setting. Without a
configured backend the button is not shown."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time

from gi.repository import GLib, Gtk

from i18n import _

MIC_GLYPH = "\U000F036C"          # Nerd Font microphone
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
_cache = {"at": 0.0, "backend": None}


def backend() -> str | None:
    """The configured dictation backend (e.g. "superwhisper"), or None (asked at most every 30 s)."""
    now = time.monotonic()
    if now - _cache["at"] < 30:
        return _cache["backend"]
    found = None
    if shutil.which("omarchy-dictation") and shutil.which("omarchy-default-dictation"):
        try:
            out = subprocess.run(["omarchy-default-dictation"], capture_output=True, text=True, timeout=3,
                                 stdin=subprocess.DEVNULL)
            name = out.stdout.strip()
            if out.returncode == 0 and NAME.match(name) and shutil.which(f"omarchy-dictation-{name}"):
                found = name
        except (OSError, subprocess.SubprocessError):
            found = None
    _cache.update(at=now, backend=found)
    return found


def display_name(name: str) -> str:
    return {"superwhisper": "Superwhisper", "voxtype": "Voxtype"}.get(name, name)


def run(action: str):
    """omarchy-dictation start|stop|toggle, without a shell, not waited for."""
    if action in ("start", "stop", "toggle"):
        subprocess.Popen(["omarchy-dictation", action], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)


def recording(name: str) -> bool | None:
    """Superwhisper says whether it records; other backends: unknown (None)."""
    if name != "superwhisper" or not shutil.which("superwhisper"):
        return None
    try:
        out = subprocess.run(["superwhisper", "status"], capture_output=True, text=True, timeout=3,
                             stdin=subprocess.DEVNULL)
        return json.loads(out.stdout[:200_000]).get("phase") == "recording"
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        return None


def mic_button(view: Gtk.TextView) -> Gtk.ToggleButton | None:
    """The microphone for a question field, or None without a dictation backend."""
    name = backend()
    if name is None:
        return None
    button = Gtk.ToggleButton(valign=Gtk.Align.END, halign=Gtk.Align.END, margin_end=6, margin_bottom=6)
    mark = Gtk.Label(label=MIC_GLYPH)
    mark.add_css_class("am-gear")
    button.set_child(mark)
    button.add_css_class("am-gear-btn")
    button.add_css_class("am-dictate")
    tip = _("Speak instead of typing: {0} writes what you say at the cursor. Click again to stop.")
    button.set_tooltip_text(tip.format(display_name(name)))
    state = {"poll": 0, "quiet": False}

    def watch():
        now = recording(name)
        if now is False and button.get_active():      # stopped with its own shortcut
            state["quiet"] = True
            button.set_active(False)
            state["quiet"] = False
        if not button.get_active():
            state["poll"] = 0
            return False
        return True

    def toggled(btn):
        if state["quiet"]:
            return
        end = view.get_buffer().get_end_iter()
        view.get_buffer().place_cursor(end)
        view.grab_focus()                               # the words go where the cursor is
        run("start" if btn.get_active() else "stop")
        if btn.get_active() and not state["poll"]:
            state["poll"] = GLib.timeout_add(1000, watch)
    button.connect("toggled", toggled)
    return button


def with_mic(view: Gtk.TextView) -> Gtk.Widget:
    """The question field with the microphone in its lower right corner (the field alone without one)."""
    button = mic_button(view)
    if button is None:
        return view
    overlay = Gtk.Overlay(child=view)
    overlay.add_overlay(button)
    view.set_right_margin(40)                           # the text keeps clear of the button
    return overlay
