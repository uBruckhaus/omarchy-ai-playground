"""AI Playground's Analyze tab (the first one): pick an agent and one of its models, then Analyze
shows what it can do, one card per capability with a big mark at the end of its heading (green ✓
can, red ✗ cannot, yellow ! slowly, ? not stated), what was found and what it means (analyze: the
tests and the facts)."""

from __future__ import annotations

import threading

from gi.repository import GLib, Gtk

import ai_core
import analyze
import frame_check
from i18n import _


class AnalyzeTab:
    def build_analyze_tab(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=ai_core.GAP)
        self.tab_intro(box, _("Analyze"),
                       _("What an agent can do with one of its models: text, pictures, chess videos, thinking time, "
                         "chess, how much it keeps in mind. A few short test questions are asked (they may count "
                         "against your plan); a local model is set up for pictures where it can be."))
        row = Gtk.Box(spacing=8)
        row.add_css_class("am-chess-controls")
        self.an_agent = self.agent_dropdown(self.ready_agents)
        self.an_agent.set_tooltip_text(_("The agent to analyze"))
        row.append(self.an_agent)
        self.an_model = Gtk.DropDown.new_from_strings([_("Looking…")])
        self.an_model.set_tooltip_text(_("The model it is asked with (its own, or another one it lists)"))
        row.append(self.an_model)
        box.append(row)
        self.an_button = self.action_button(_("Analyze"), self.an_start)
        box.append(self.an_button)
        self.an_cards = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=ai_core.GAP)
        box.append(self.an_cards)
        self.an_choices, self.an_run, self.an_parts = [], 0, {}
        self.an_agent.connect("notify::selected", lambda *_a: self.an_fill_models())
        self.an_model.connect("notify::selected", lambda *_a: self.an_clear())     # findings were for the other one
        if not self.ready_agents:
            self.an_button.set_sensitive(False)
            self.an_model.set_model(Gtk.StringList.new([_("No agent is set up here yet")]))
        else:
            self.an_fill_models()
        return self.page_scroller(box)

    def an_selected_agent(self):
        index = self.an_agent.get_selected()
        return self.ready_agents[index] if 0 <= index < len(self.ready_agents) else None

    def an_fill_models(self):
        agent = self.an_selected_agent()
        if agent is None:
            return
        self.an_clear()
        self.an_model.set_model(Gtk.StringList.new([_("Looking…")]))
        self.an_button.set_sensitive(False)
        asked = agent.command

        def work():
            found = analyze.choices(agent)
            GLib.idle_add(done, found)

        def done(found):
            current = self.an_selected_agent()
            if current is None or current.command != asked:
                return False
            self.an_choices = found
            self.an_model.set_model(Gtk.StringList.new([c.label for c in found]))
            self.an_button.set_sensitive(True)
            return False
        threading.Thread(target=work, daemon=True).start()

    def an_clear(self):
        while (child := self.an_cards.get_first_child()) is not None:
            self.an_cards.remove(child)
        self.an_parts = {}

    def an_start(self):
        agent, index = self.an_selected_agent(), self.an_model.get_selected()
        if agent is None or not (0 <= index < len(self.an_choices)):
            return
        choice = self.an_choices[index]
        self.an_run += 1
        run = self.an_run
        self.an_clear()
        for key, title, meaning in analyze.CARDS:
            mark = Gtk.Label(label=analyze.MARKS["waiting"], xalign=1)
            mark.add_css_class("am-an-mark")
            card = self.group_card(self.an_cards, f"analyze-{key}", _(title), summary=_("waiting…"),
                                   default_open=True, action=mark)
            card.group_summary.set_visible(True)
            explain = Gtk.Label(label=_(meaning), xalign=0, wrap=True)
            explain.add_css_class("am-hint")
            card.append(explain)
            found = self.answer_label("")
            found.set_visible(False)
            card.append(found)
            fetch = Gtk.Button(label=_("Download the vision file"), halign=Gtk.Align.START, visible=False)
            card.append(fetch)
            self.an_parts[key] = (card, mark, found, fetch)
        self.an_button.set_sensitive(False)
        self.an_agent.set_sensitive(False)
        self.an_model.set_sensitive(False)

        def report(finding):
            GLib.idle_add(self.an_show, run, agent, choice, finding)

        def work():
            try:
                analyze.run(agent, choice, report, lambda: run != self.an_run)
            finally:
                GLib.idle_add(finish)

        def finish():
            if run == self.an_run:
                self.an_button.set_sensitive(True)
                self.an_agent.set_sensitive(True)
                self.an_model.set_sensitive(True)
            return False
        threading.Thread(target=work, daemon=True).start()

    def an_show(self, run, agent, choice, finding):
        if run != self.an_run or finding.key not in self.an_parts:
            return False
        card, mark, found, fetch = self.an_parts[finding.key]
        mark.set_label(analyze.MARKS.get(finding.state, "?"))
        mark.set_css_classes(["am-an-mark", finding.state])
        mark.set_tooltip_text(finding.summary or None)
        card.group_summary.set_label(finding.summary)
        card.group_summary.set_tooltip_text(finding.summary or None)
        found.set_label(finding.detail)
        found.set_visible(bool(finding.detail))
        fetch.set_visible(finding.download is not None)
        if finding.download is not None:
            fetch.set_label(_("Download the vision file ({0:.1f} GB)").format(finding.download.size / 1024 ** 3))
            if getattr(fetch, "an_handler", None):
                fetch.disconnect(fetch.an_handler)
            fetch.an_handler = fetch.connect("clicked", lambda b: self.an_download(b, agent, choice, finding.download))
        return False

    def an_download(self, button, agent, choice, wanted):
        button.set_sensitive(False)
        run = self.an_run
        _card, _mark, found, _fetch = self.an_parts["pictures"]

        def progress(part):
            GLib.idle_add(lambda: (run == self.an_run and found.set_label(
                _("Downloading the vision file… {0:.0f} %").format(part * 100)), False)[1])

        def work():
            why = frame_check.fetch_mmproj(wanted, progress, lambda: run != self.an_run)
            frame_check.forget(agent)
            GLib.idle_add(done, why)

        def done(why):
            if run != self.an_run:
                return False
            if why:
                found.set_label(_("The vision file could not be set up: {0}").format(why))
                button.set_sensitive(True)
            else:
                self.an_start()                       # all again, now with the vision file
            return False
        threading.Thread(target=work, daemon=True).start()
