#!/usr/bin/env python3
"""AI Playground: try out the AI agents on this computer, a test per tab: chess (against you or
against each other), speed and cost, head to head, vision (pictures, screenshots, video frames)
and the local LLMs.

A normal app window (no overlay): started again, it comes to the front; closing it quits.
`ai-playground --agent <command> --test <tab>` opens a tab with that agent chosen (AI Agents
Map starts it this way); an agent that is not installed and set up is ignored. `--toggle` (the bar
icon): closes it when it is the window in front, else starts it or brings it to the front.

Built from Asterism's core (one source, see tools/sync-plugin.py --edition playground): the
Omarchy plugin ubruckhaus.ai-playground, and the same app on GNOME and other desktops.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if not (HERE / "ai_core.py").is_file() and (HERE.parent / "ai_core.py").is_file():
    sys.path.insert(0, str(HERE.parent))          # run from Asterism's playground/ folder: the core is one up

import desktop  # noqa: E402

if desktop.NAME == "omarchy":
    desktop.use_identity("AI Playground", "org.omarchy.aiplayground",
                         Path.home() / ".config/omarchy/ai-playground")
else:
    desktop.use_identity("AI Playground", "io.github.ubruckhaus.AIPlayground",
                         Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "ai-playground")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

import ai_core  # noqa: E402
import corners  # noqa: E402
import i18n  # noqa: E402
import more_plugins  # noqa: E402
from chess_import import ChessImport  # noqa: E402
from chess_page import ChessPage  # noqa: E402
from host import Host, askable_agents  # noqa: E402
from i18n import _, N_  # noqa: E402
from palette import ThemePalette  # noqa: E402
from tabs import DuelTab, LocalTab, SpeedTab, VisionTab  # noqa: E402
from analyze_tab import AnalyzeTab  # noqa: E402
from text_guard import TextGuard  # noqa: E402
from generate_tab import GenerateTab  # noqa: E402
from theme_css import ThemeCss  # noqa: E402

TABS = (("analyze", N_("Analyze")), ("chess", N_("Chess")), ("speed", N_("Speed and cost")), ("duel", N_("Head to head")),
        ("vision", N_("Vision")), ("generate", N_("Generate")), ("local", N_("Local LLMs")))
ICON = "\U000F0297"                    # Nerd Font gamepad: the header and the bar icon
OWN_ID = "ubruckhaus.ai-playground"
AGENTS_MAP_ID = "ubruckhaus.ai-agents-map"
WINDOW_SIZE = (1100, 920)
FRONT_GRACE = 1.0          # seconds: a bar click may take the focus just before --toggle arrives


def parse_args(argv: list) -> dict:
    """--agent <command>, --test <tab> and --toggle (unknown words are ignored: it is a GUI)."""
    wanted = {"agent": "", "test": "", "toggle": "--toggle" in argv}
    words = list(argv)
    for i, word in enumerate(words):
        for key in ("agent", "test"):
            if word == f"--{key}" and i + 1 < len(words):
                wanted[key] = words[i + 1]
            elif word.startswith(f"--{key}="):
                wanted[key] = word.split("=", 1)[1]
    if wanted["test"] not in {k for k, _l in TABS}:
        wanted["test"] = ""
    if not all(c.isalnum() or c in "-_." for c in wanted["agent"]) or len(wanted["agent"]) > 64:
        wanted["agent"] = ""
    return wanted


class Playground(Adw.Application, TextGuard, Host, ThemeCss, AnalyzeTab, ChessPage, ChessImport, SpeedTab, DuelTab, VisionTab, GenerateTab, LocalTab):
    def __init__(self):
        super().__init__(application_id=desktop.app_id(), flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.window = None
        self.theme = ThemePalette()
        self.style_provider = None
        self.speed_run = self.vision_run = 0

    # --- start ------------------------------------------------------------------------------------

    def do_command_line(self, command_line):
        wanted = parse_args(command_line.get_arguments()[1:])
        if wanted["toggle"] and self.in_front():
            self.window.close()                        # as its close button: the tab is kept, it quits
            return 0
        if self.window is None:
            self.build_window()
        self.open_wanted(wanted)
        self.window.present()
        desktop.focus_window(desktop.app_id())
        return 0

    def in_front(self) -> bool:
        """The window is shown and in front, or was a moment ago (a click on the bar icon can take
        the focus just before the toggle arrives)."""
        window = self.window
        if window is None or not window.get_visible():
            return False
        return window.is_active() or time.monotonic() - self.left_front < FRONT_GRACE

    def open_wanted(self, wanted: dict):
        agent = next((a for a in self.ready_agents if a.command == wanted["agent"]), None)
        if wanted["agent"] and agent is None:
            self.toast(_("{0} is not installed and set up here.").format(wanted["agent"]))
        if agent is not None and getattr(self, "an_agent", None) is not None:
            self.an_agent.set_selected(self.ready_agents.index(agent))     # Analyze has it ready too
        test = wanted["test"] or ("chess" if agent is not None else "")
        if test == "chess" and agent is not None and self.can_play_chess(agent):
            commands = [a.command for a in self.chess_opponents]
            if agent.command in commands:
                self.chess_opponent.set_selected(commands.index(agent.command))
            self.open_chess(agent)
        if test:
            self.show_page(test)

    # --- the window -------------------------------------------------------------------------------

    def build_window(self):
        Gtk.Settings.get_default().set_property("gtk-enable-primary-paste", False)
        window = Adw.ApplicationWindow(application=self)
        window.set_decorated(desktop.decorated())
        desktop.attach_window(window)
        window.set_name("aiagentsmap-window")          # the house CSS (theme_css)
        window.set_title(desktop.app_name())
        window.set_icon_name("applications-games")
        window.set_default_size(*WINDOW_SIZE)
        window.set_size_request(720, 560)
        window.connect("close-request", self.on_close)
        self.left_front = 0.0
        window.connect("notify::is-active", lambda w, _p: not w.is_active()
                       and setattr(self, "left_front", time.monotonic()))
        self.window = window
        self.ready_agents = [a for a in askable_agents() if self.chess_set_up(a)]
        self.agent_list = self.ready_agents             # ChessPage's agents to play against

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=ai_core.GAP, hexpand=True, vexpand=True)
        card.add_css_class("am-card")
        self.toasts = Adw.ToastOverlay(child=card)
        header = self.build_header()
        if desktop.decorated():
            bar = Gtk.WindowHandle()
            bar.add_css_class("am-headerbar")
            row = Gtk.Box(spacing=8)
            row.add_css_class("am-header-row")
            header.set_hexpand(True)
            row.append(header)
            row.append(Gtk.WindowControls(side=Gtk.PackType.END))
            bar.set_child(row)
            toolbar = Adw.ToolbarView()
            toolbar.add_top_bar(bar)
            toolbar.set_content(self.toasts)
            window.set_content(toolbar)
        else:
            card.append(header)
            window.set_content(self.toasts)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=120,
                               vexpand=True)
        self.stack.add_named(self.build_analyze_tab(), "analyze")
        self.stack.add_named(self.build_chess_tab(), "chess")
        self.stack.add_named(self.build_speed_tab(), "speed")
        self.stack.add_named(self.build_duel_tab(), "duel")
        self.stack.add_named(self.build_vision_tab(), "vision")
        self.stack.add_named(self.build_generate_tab(), "generate")
        self.stack.add_named(self.build_local_tab(), "local")
        self.stack.add_named(self.build_settings_page(), "settings")
        self.stack.add_named(self.build_info_page(), "info")
        self.stack.add_named(self.build_games_page(), "games")      # from the Saved games card's symbol
        card.append(self.stack)
        self.guard_text_fields_in(window)              # paste and text drops only through bounded reads

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        window.add_controller(keys)
        self.apply_theme_css()
        self.watch_theme()
        saved = ai_core.setting("playground_tab")
        self.show_page(saved if saved in {k for k, _l in TABS} else "analyze")

    def build_header(self) -> Gtk.Widget:
        header = Gtk.Box(spacing=8)
        mark = Gtk.Label(label=ICON)
        mark.add_css_class("am-gear")
        header.append(mark)
        title = Gtk.Label(label=desktop.app_name(), xalign=0)
        title.add_css_class("am-title")
        header.append(title)
        tabs = Gtk.Box(spacing=4, margin_start=12)
        self.tab_buttons = {}
        group = None
        for i, (key, label) in enumerate(TABS):
            button = Gtk.ToggleButton(label=_(label), valign=Gtk.Align.CENTER)
            button.add_css_class("am-small-btn")
            button.add_css_class("am-view-btn")
            button.set_tooltip_text(f"Ctrl+{i + 1}")
            if group is not None:
                button.set_group(group)
            group = group or button
            button.connect("toggled", lambda b, k=key: b.get_active() and self.show_page(k))
            tabs.append(button)
            self.tab_buttons[key] = button
        header.append(tabs)
        header.append(Gtk.Box(hexpand=True))
        info = Gtk.Button(valign=Gtk.Align.CENTER)
        mark_i = Gtk.Label(label=more_plugins.INFO_GLYPH)
        mark_i.add_css_class("am-gear")
        info.set_child(mark_i)
        info.add_css_class("flat")
        info.add_css_class("am-header-btn")
        info.add_css_class("am-gear-btn")
        info.set_tooltip_text(_(more_plugins.INFORMATION))
        info.connect("clicked", lambda *_a: self.show_page("info"))
        header.append(info)
        gear = Gtk.Button(valign=Gtk.Align.CENTER)
        cog = Gtk.Label(label="")
        cog.add_css_class("am-gear")
        gear.set_child(cog)
        gear.add_css_class("flat")
        gear.add_css_class("am-header-btn")
        gear.add_css_class("am-gear-btn")
        gear.set_tooltip_text(_("Settings") + " (Ctrl+,)")
        gear.connect("clicked", lambda *_a: self.show_page("settings"))
        header.append(gear)
        return header

    def toast(self, text: str):
        toast = Adw.Toast(title=GLib.markup_escape_text(text), timeout=5)
        self.toasts.add_toast(toast)

    def on_close(self, *_args):
        page = self.page_name()
        if page in {k for k, _l in TABS}:
            self.save_setting(playground_tab=page)
        self.vision_cleanup()
        self.gen_cleanup()
        self.quit()
        return False

    # --- pages: what ChessPage asks of its host -------------------------------------------------------

    def page_name(self) -> str:
        return self.stack.get_visible_child_name() or ""

    def show_page(self, name: str):
        if self.stack.get_child_by_name(name) is None:
            return                                     # still being built (the chess tab opens its game)
        if name not in ("settings", "info", "games"):
            self.last_tab = name
        self.stack.set_visible_child_name(name)
        for key, button in self.tab_buttons.items():
            if button.get_active() != (key == name):
                button.set_active(key == name)

    def remember_page(self):
        pass

    def back_to_last_page(self):
        pass

    def chess_start_choice(self):
        """Agent against agent with two local models: the GPU memory check first (gpu_check)."""
        from chess_page import CHOICES
        if CHOICES[self.chess_choice.get_selected()][0] != "agents" or self.chess_agent is None:
            ChessPage.chess_start_choice(self)
            return
        index = self.chess_rival_choice.get_selected()
        rival = self.chess_rival_agents[index] if 0 <= index < len(self.chess_rival_agents) else self.chess_agent

        self.chess_rival_choice.set_visible(True)
        self.chess_rival_strength.set_visible(True)
        self.gpu_guard(self.agents_wanted([rival, self.chess_agent]),
                       lambda _one_by_one: ChessPage.chess_start_choice(self), alternating=True)

    def on_key(self, _controller, keyval, _keycode, state) -> bool:
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        if ctrl and Gdk.KEY_1 <= keyval <= Gdk.KEY_1 + len(TABS) - 1:
            self.show_page(TABS[keyval - Gdk.KEY_1][0])
            return True
        if ctrl and keyval in (Gdk.KEY_v, Gdk.KEY_V) and self.page_name() == "chess":
            self.chess_paste()                         # a YouTube link from the clipboard: a video import
            return True
        if ctrl and keyval == Gdk.KEY_comma:
            self.show_page("settings")
            return True
        if self.page_name() in ("settings", "info", "games") and keyval in (Gdk.KEY_Escape, Gdk.KEY_BackSpace) \
                and not (self.page_name() == "games" and keyval == Gdk.KEY_BackSpace
                         and (self.chess_games_search.has_focus()
                              or self.chess_games_search.get_delegate().has_focus())):     # typing in the search
            self.show_page(getattr(self, "last_tab", "chess"))
            return True
        return False

    # --- the chess tab: an opponent above the shared chess page --------------------------------------

    def build_chess_tab(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=ai_core.GAP)
        row = Gtk.Box(spacing=8)
        row.add_css_class("am-chess-controls")
        label = Gtk.Label(label=_("Opponent"))
        label.add_css_class("am-dim")
        row.append(label)
        self.chess_opponents = self.chess_playable_agents()
        self.chess_opponent = self.agent_dropdown(self.chess_opponents)
        self.chess_opponent.set_tooltip_text(
            _("The agent you play against; with “Agent against agent” it plays Black"))
        row.append(self.chess_opponent)
        box.append(row)
        box.append(self.build_chess_page())
        self.build_chess_import(row, box)           # Import video…, replay controls, drop target, Saved games
        if self.chess_opponents:
            self.open_chess(self.chess_opponents[0])
        else:
            self.chess_title.set_label(_("Chess").upper())
            self.chess_set_status(_("No agent that plays chess is set up: Claude Code, Codex, Hermes or Pi, "
                                    "signed in or connected to a local LLM."))
        self.chess_opponent.connect("notify::selected", lambda d, *_a: self.chess_opponents and self.open_chess(
            self.chess_opponents[d.get_selected()]))
        return box

    # --- settings and information ------------------------------------------------------------------

    def page_head(self, box: Gtk.Box, title_text: str):
        """← Back · ⌫ and the page's title, a rule below (Settings, Information)."""
        head = Gtk.Box(spacing=8)
        back = Gtk.Button()
        back.add_css_class("flat")
        back.add_css_class("am-header-btn")
        back.add_css_class("am-back-bordered")
        inner = Gtk.Box(spacing=6)
        inner.append(Gtk.Image.new_from_icon_name("go-previous-symbolic"))
        inner.append(Gtk.Label(label=_("Back")))
        inner.append(Gtk.Label(label="· ⌫"))
        back.set_child(inner)
        back.set_tooltip_text(_("Back (Backspace, Esc)"))
        back.connect("clicked", lambda *_a: self.show_page(getattr(self, "last_tab", "chess")))
        head.append(back)
        title = Gtk.Label(label=title_text.upper(), xalign=0)
        title.add_css_class("am-section-title")
        head.append(title)
        box.append(head)
        rule = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        rule.add_css_class("am-settings-rule")
        box.append(rule)

    def build_info_page(self) -> Gtk.Widget:
        """AI Agents Map in a section of its own (the playground's companion), then cards for the
        author's other plugins; a click opens the plugin's marketplace page."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=ai_core.GAP)
        self.page_head(box, _(more_plugins.INFORMATION))
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=ai_core.GAP)
        heading = Gtk.Label(label=_("Made to go with AI Agents Map").upper(), xalign=0)
        heading.add_css_class("am-more-heading")
        body.append(heading)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class("am-help-card")
        top = Gtk.Box(spacing=10)
        glyph = Gtk.Label(label="\U000F06A9", valign=Gtk.Align.START)
        glyph.add_css_class("am-more-icon")
        top.append(glyph)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        name = Gtk.Label(label="AI Agents Map", xalign=0)
        name.add_css_class("am-more-name")
        texts.append(name)
        for text in (_("Your whole AI setup on one map: coding agents with their plan, limits and usage, local "
                       "LLMs and their models, MCP servers and skills."),
                     _("It installs, updates and signs in everything in one place; you connect an agent to a "
                       "local model by drawing a line, with a preview and Undo. A red ! lists what needs fixing, "
                       "batteries show limits and free GPU memory."),
                     _("Together: the agents you set up there (signed in, or connected to a local LLM) are the ones "
                       "you can test here.")):
            line = self.answer_label(text)
            line.set_selectable(False)
            texts.append(line)
        top.append(texts)
        card.append(top)
        open_button = self.small_button(_("Open AI Agents Map in the Omarchy Plugin Marketplace"),
                                        lambda: more_plugins.open_page(AGENTS_MAP_ID))
        open_button.set_halign(Gtk.Align.START)
        card.append(open_button)
        body.append(card)
        body.append(more_plugins.build_cards(OWN_ID, "am", leave_out=(AGENTS_MAP_ID,)))
        box.append(self.page_scroller(body))
        return box

    def build_settings_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=ai_core.GAP)
        self.page_head(box, _("Settings"))
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=ai_core.GAP)
        body.add_css_class("am-settings")
        group = self.preference_group(body, _("General"))
        codes = ["auto"] + [code for code, _name in i18n.LANGUAGES]
        setting = i18n.language_setting()
        self.combo_row(group, _("Language"), _("AI Agents Map restarts in the new language."),
                       [_("System ({0})").format(i18n.language_name(i18n.system_language()))]
                       + [name for _code, name in i18n.LANGUAGES],
                       codes.index(setting) if setting in codes else 0, lambda i: self.set_language(codes[i]))
        rounding = desktop.window_rounding()
        like = _("Like windows ({0} px)").format(rounding) if rounding is not None else _("Like windows")
        self.combo_row(group, _("Corners"), desktop.corners_note(), [like, _("Rounded"), _("Square")],
                       corners.CHOICES.index(ai_core.saved_corners()),
                       lambda i: (self.save_setting(corners=corners.CHOICES[i]), self.apply_theme_css()))
        self.combo_row(group, _("Text in choice boxes"),
                       _("The chosen value in drop-down boxes: bright (white on dark themes, the theme's text colour "
                         "on light ones) or dimmed, as before."),
                       [_("Bright"), _("Dimmed")], 1 if ai_core.setting("choice_text") == "dim" else 0,
                       lambda i: (self.save_setting(choice_text=("bright", "dim")[i]), self.apply_theme_css()))
        if desktop.NAME == "omarchy":
            self.combo_row(group, _("Look"),
                           _("The colours, light or dark: from the Omarchy theme, or GNOME's Adwaita (light or dark "
                             "and the accent colour as set in the system). Everything else works the same."),
                           [_("Omarchy theme"), _("GNOME (Adwaita)")], ai_core.LOOKS.index(ai_core.saved_look()),
                           lambda i: (self.save_setting(look=ai_core.LOOKS[i]), self.theme.reload(),
                                      self.apply_theme_css()))
        # How it works: cards that fold, open until folded.
        alone = self.group_card(body, "explain-one-agent", _("One agent or none"), default_open=True)
        for text in (
                _("Most tests compare, but one agent is enough:"),
                _("Chess: play against it, or let it play itself (Agent against agent), each side with its own "
                  "strength, for example Master against Beginner."),
                _("Speed and cost, Head to head: the models of the local LLM servers running here (see Local LLMs) "
                  "take part next to the agents, so your agent can meet a local model, or two local models each "
                  "other. The same one twice in Head to head keeps no score."),
                _("Vision: only agents that take pictures (Claude Code, Codex, Pi). Local LLMs: no agent needed."),
                _("No agent offered: install one and sign in, or connect it to a local LLM (AI Agents Map does both). "
                  "An agent that is installed but not signed in is not offered, since it could not answer.")):
            line = self.answer_label(text)
            line.set_selectable(False)
            alone.append(line)
        explain = self.group_card(body, "explain-gpu", _("Several local models at once"), default_open=True)
        for text in (
                _("When a test would use two or more different local models at the same time (two models on the "
                  "Local LLMs tab, local models or agents that use a local LLM in Speed and cost or Head to head, "
                  "such agents in Vision or agent against agent chess), AI Playground first checks whether they fit "
                  "in the GPU memory."),
                _("Free GPU memory: what the graphics card reports (AMD through the kernel, NVIDIA through "
                  "nvidia-smi). A model that is loaded already needs nothing more. Any other model needs about its "
                  "file size plus a tenth, and half a GB for its context. The size comes from Ollama, or from the "
                  ".gguf file in the usual model folders and the models folder set in AI Agents Map."),
                _("Which local model an agent uses comes from its own settings, as AI Agents Map reads them."),
                _("When they fit, the test simply starts. When they do not, or when a size could not be found, you "
                  "choose: One after the other (the next model is asked only when the one before has answered; "
                  "Ollama is asked to unload it first), Both at once, or Cancel. In chess the agents answer in "
                  "turn anyway: there you choose Play anyway or Cancel."),
                _("Without GPU figures (no AMD or NVIDIA card found) nothing is checked.")):
            line = self.answer_label(text)
            line.set_selectable(False)
            explain.append(line)
        box.append(self.page_scroller(body))
        return box

    def set_language(self, code: str):
        if code == i18n.language_setting():
            return
        try:
            i18n.save_language(code)
        except OSError:
            return
        self.save_setting(playground_tab=getattr(self, "last_tab", "chess"))
        self.vision_cleanup()
        self.gen_cleanup()
        sys.stdout.flush()
        sys.stderr.flush()
        os.execv(sys.executable, [sys.executable, *sys.argv])


def main():
    ai_core.drop_agent_session_vars()                 # started from an agent's terminal: no session inherited
    i18n.set_name("AI Agents Map", desktop.app_name())
    app = Playground()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
