"""Speak instead of typing: a microphone button beside the question fields.

It uses the desktop's own dictation (Omarchy: `omarchy-dictation`, the backend chosen in
Setup › Defaults › Dictation, e.g. Superwhisper or voxtype). A click puts the cursor in the field
and starts the dictation; the backend writes what is said at the cursor; a second click stops it.
AI Playground records nothing itself, starts no other program and sends nothing anywhere; where the
words are recognised (on this PC or a cloud service) is the backend's own setting. With no backend
set up yet, a click explains how to set one up (Omarchy's Setup › Defaults › Dictation); away from
Omarchy there is no button."""

from __future__ import annotations

import json
import os
import re
import selectors
import shutil
import subprocess
import time

from gi.repository import GLib, Gtk

from i18n import _

MIC_GLYPH = "\U000F036C"          # Nerd Font microphone
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
_cache = {"at": 0.0, "backend": None}
OUTPUT_LIMIT = 64 * 1024          # bytes read from a helper's answer; more ends it
# The one microphone that is recording (a single recorder for the whole app): its "switch off".
_active = {"off": None}


def read_output(argv: list, timeout: float = 3.0, limit: int = OUTPUT_LIMIT) -> tuple[int, str] | None:
    """Run argv (no shell) and read at most `limit` bytes of its output within `timeout` seconds;
    None when it fails, takes too long or says too much (then it is ended). Undecodable bytes are
    replaced, so a broken helper cannot raise here."""
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return None
    data = bytearray()
    deadline = time.monotonic() + timeout
    sel = selectors.DefaultSelector()
    try:
        sel.register(proc.stdout, selectors.EVENT_READ)
        while True:
            left = deadline - time.monotonic()
            if left <= 0 or not sel.select(left):
                raise TimeoutError
            chunk = os.read(proc.stdout.fileno(), 65536)
            if not chunk:
                break
            data += chunk
            if len(data) > limit:
                raise OverflowError
        code = proc.wait(timeout=max(0.1, deadline - time.monotonic()))
        return code, data.decode("utf-8", errors="replace")
    except (OSError, ValueError, TimeoutError, OverflowError, subprocess.SubprocessError):
        proc.kill()
        try:
            proc.wait(timeout=1)
        except subprocess.SubprocessError:
            pass
        return None
    finally:
        sel.close()
        proc.stdout.close()


def stop_all():
    """The app closes: a dictation it started must not go on writing into another window."""
    off = _active["off"]
    if off is not None:
        off()


SETUP_ROUTE = "setup.default.dictation"      # Omarchy's menu: Setup › Defaults › Dictation


def supported() -> bool:
    """The desktop has a dictation command to call (Omarchy)."""
    return bool(shutil.which("omarchy-dictation") and shutil.which("omarchy-default-dictation"))


def backend(fresh: bool = False) -> str | None:
    """The configured dictation backend whose own program is installed (as Omarchy's key bindings
    check it), e.g. "superwhisper", or None; asked at most every 30 s unless `fresh`."""
    now = time.monotonic()
    if not fresh and now - _cache["at"] < 30:
        return _cache["backend"]
    found = None
    if shutil.which("omarchy-dictation") and shutil.which("omarchy-default-dictation"):
        answer = read_output(["omarchy-default-dictation"], limit=256)
        if answer is not None:
            code, text = answer
            name = text.strip()
            if code == 0 and NAME.match(name) and shutil.which(f"omarchy-dictation-{name}") and shutil.which(name):
                found = name
    _cache.update(at=now, backend=found)
    return found


def display_name(name: str) -> str:
    return {"superwhisper": "Superwhisper", "voxtype": "Voxtype"}.get(name, name)


def open_setup():
    """Omarchy's menu at Setup › Defaults › Dictation (argv, not waited for)."""
    if shutil.which("omarchy-menu"):
        subprocess.Popen(["omarchy-menu", "summon", SETUP_ROUTE], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


SETUP_TITLE = "Set up dictation"
SETUP_TEXT = ("To speak instead of typing, choose a dictation program first: in Omarchy's menu under Setup › "
              "Defaults › Dictation, pick Superwhisper or Voxtype; Omarchy installs and sets it up. Then click "
              "the microphone again.")
SETUP_BUTTON = "Open dictation setup"
NOT_SET_UP_TIP = "Speak instead of typing: no dictation program is set up yet. Click for how to set one up."


def run(action: str):
    """omarchy-dictation start|stop|toggle, without a shell, not waited for."""
    if action in ("start", "stop", "toggle"):
        subprocess.Popen(["omarchy-dictation", action], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)


def recording(name: str) -> bool | None:
    """Superwhisper says whether it records; other backends: unknown (None)."""
    if name != "superwhisper" or not shutil.which("superwhisper"):
        return None
    answer = read_output(["superwhisper", "status"])
    if answer is None:
        return None
    try:
        status = json.loads(answer[1])
    except ValueError:
        return None
    return status.get("phase") == "recording" if isinstance(status, dict) else None


def mic_button(view: Gtk.TextView, explain=None) -> Gtk.ToggleButton | None:
    """The microphone for a question field (None away from Omarchy). `explain()` shows the set-up
    hint when no dictation program is set up."""
    if not supported():
        return None
    button = Gtk.ToggleButton(valign=Gtk.Align.END, halign=Gtk.Align.END, margin_end=6, margin_bottom=6)
    mark = Gtk.Label(label=MIC_GLYPH)
    mark.add_css_class("am-gear")
    button.set_child(mark)
    button.add_css_class("am-gear-btn")
    button.add_css_class("am-dictate")
    state = {"poll": 0, "quiet": False, "name": backend()}

    def tip():
        name = state["name"]
        if name is None:
            button.set_tooltip_text(_(NOT_SET_UP_TIP))
        else:
            button.set_tooltip_text(_("Speak instead of typing: {0} writes what you say at the cursor. "
                                      "Click again to stop.").format(display_name(name)))
    tip()

    def quietly_off():
        state["quiet"] = True
        button.set_active(False)
        state["quiet"] = False
        if _active["off"] is stop_here:
            _active["off"] = None

    def stop_here():
        """Stop the dictation this button started (the app closes, or another field takes over)."""
        if button.get_active():
            run("stop")
            quietly_off()

    def watch():
        if recording(state["name"]) is False and button.get_active():   # stopped with its own shortcut
            quietly_off()
        if not button.get_active():
            state["poll"] = 0
            return False
        return True

    def toggled(btn):
        if state["quiet"]:
            return
        if btn.get_active():
            state["name"] = backend(fresh=True)        # set up meanwhile? ask again
            tip()
            if state["name"] is None:
                quietly_off()
                if explain is not None:
                    explain()
                return
        end = view.get_buffer().get_end_iter()
        view.get_buffer().place_cursor(end)
        view.grab_focus()                               # the words go where the cursor is
        if btn.get_active():
            other = _active["off"]
            if other is not None and other is not stop_here:
                other()                                 # one recorder: the other field stops first
            _active["off"] = stop_here
        run("start" if btn.get_active() else "stop")
        if not btn.get_active() and _active["off"] is stop_here:
            _active["off"] = None
        if btn.get_active() and not state["poll"]:
            state["poll"] = GLib.timeout_add(1000, watch)
    button.connect("toggled", toggled)
    return button


def setup_dialog(parent: Gtk.Widget):
    """The hint when no dictation program is set up, with a button to Omarchy's dictation menu."""
    from gi.repository import Adw
    dialog = Adw.AlertDialog(heading=_(SETUP_TITLE), body=_(SETUP_TEXT))
    dialog.add_response("close", _("Close"))
    dialog.add_response("setup", _(SETUP_BUTTON))
    dialog.set_response_appearance("setup", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("setup")
    dialog.set_close_response("close")
    dialog.connect("response", lambda _d, answer: answer == "setup" and open_setup())
    dialog.present(parent)


def with_mic(view: Gtk.TextView) -> Gtk.Widget:
    """The question field with the microphone in its lower right corner (the field alone without one)."""
    button = mic_button(view, explain=lambda: setup_dialog(view.get_root() or view))
    if button is None:
        return view
    overlay = Gtk.Overlay(child=view)
    overlay.add_overlay(button)
    view.set_right_margin(40)                           # the text keeps clear of the button
    return overlay
