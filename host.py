"""AI Playground's small helpers for its pages, in the house style of AI Agents Map (the same CSS
classes, so theme_css styles both): a scroller, a card that folds, settings rows, and the agents
that can be asked.

AI Agents Map's own helpers (page_parts, settings_pages) bring the map and the setup pages
with them; these are the few pieces the playground needs.
"""

from __future__ import annotations

import shutil
from types import SimpleNamespace

from gi.repository import Adw, Gtk, Pango

import ai_core
import chess_agent
import harnesses
from i18n import _


def askable_agents() -> list:
    """The agents whose adapter says how to ask them once and whose program is installed, as
    {command, name, installed}; the chess page and the tabs check which are set up."""
    found = []
    for harness in harnesses.all_harnesses():
        argv = chess_agent.ask_argv(harness.command)
        if argv and shutil.which(argv[0]):
            found.append(SimpleNamespace(command=harness.command, name=harness.name, installed=True))
    return found


class Host:
    def save_setting(self, **values):
        try:
            ai_core.update_settings(**values)
        except OSError:
            pass

    def page_scroller(self, child: Gtk.Widget, bar_room: bool = True) -> Gtk.ScrolledWindow:
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        if bar_room:
            child.set_margin_end(max(child.get_margin_end(), 14))
        scroller.set_child(child)
        return scroller

    def group_card(self, box, key: str, title: str, icon: str = "", summary: str = "", badges=(),
                   named_by_addon: bool = False, default_open: bool = False,
                   action: Gtk.Widget | None = None) -> Gtk.Box:
        """A card that folds, as AI Agents Map's (its heading opens and closes it; the summary shows
        while it is folded); `action`: a button at the right of its heading. Returns the box to fill."""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.add_css_class("am-help-card")
        card.add_css_class("am-group-card")
        head = Gtk.Box(spacing=8)
        chevron = Gtk.Image.new_from_icon_name("pan-down-symbolic" if default_open else "pan-end-symbolic")
        head.append(chevron)
        if icon:
            head.append(Gtk.Image.new_from_icon_name(icon))
        heading = Gtk.Label(label=title.upper(), xalign=0)
        heading.add_css_class("am-setting-heading")
        head.append(heading)
        line = Gtk.Label(label=summary, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END,
                         max_width_chars=1, visible=not default_open)
        line.add_css_class("am-agent-detail")
        head.append(line)
        filler = Gtk.Box(hexpand=True, visible=default_open)
        head.append(filler)
        if action is not None:
            action.set_valign(Gtk.Align.CENTER)
            head.append(action)
        head.set_cursor_from_name("pointer")
        card.append(head)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=ai_core.GAP, margin_top=ai_core.GAP,
                          margin_bottom=6)
        revealer = Gtk.Revealer(child=content, reveal_child=default_open,
                                transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        card.append(revealer)
        box.append(card)

        def flip(*_args):
            now = not revealer.get_reveal_child()
            revealer.set_reveal_child(now)
            chevron.set_from_icon_name("pan-down-symbolic" if now else "pan-end-symbolic")
            line.set_visible(not now)
            filler.set_visible(now)
        click = Gtk.GestureClick()
        click.connect("released", flip)
        head.add_controller(click)
        content.group_summary = line
        content.group_card = card
        return content

    @staticmethod
    def boxed_list() -> Gtk.ListBox:
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")
        return listbox

    # --- pieces of the tabs -----------------------------------------------------------------------

    @staticmethod
    def tab_intro(box: Gtk.Box, title: str, text: str):
        heading = Gtk.Label(label=title.upper(), xalign=0)
        heading.add_css_class("am-section-title")
        box.append(heading)
        hint = Gtk.Label(label=text, xalign=0, wrap=True)
        hint.add_css_class("am-hint")
        box.append(hint)

    @staticmethod
    def icon_button(icon: str, on_click, tip: str) -> Gtk.Button:
        """A small button with a symbol only; what it does in its tooltip."""
        button = Gtk.Button.new_from_icon_name(icon)
        button.add_css_class("am-small-btn")
        button.set_valign(Gtk.Align.CENTER)
        button.set_tooltip_text(tip)
        button.connect("clicked", lambda *_a: on_click())
        return button

    @staticmethod
    def small_button(label: str, on_click, tip: str = "") -> Gtk.Button:
        button = Gtk.Button(label=label, halign=Gtk.Align.START)
        button.add_css_class("am-small-btn")
        if tip:
            button.set_tooltip_text(tip)
        button.connect("clicked", lambda *_a: on_click())
        return button

    @staticmethod
    def action_button(label: str, on_click, tip: str = "") -> Gtk.Button:
        """The tab's main button (Ask): large, in the accent colour."""
        button = Gtk.Button(label=label, halign=Gtk.Align.START)
        button.add_css_class("am-action-btn")
        button.add_css_class("am-primary")
        if tip:
            button.set_tooltip_text(tip)
        button.connect("clicked", lambda *_a: on_click())
        return button

    @staticmethod
    def agent_dropdown(agents: list, selected_command: str = "") -> Gtk.DropDown:
        drop = Gtk.DropDown.new_from_strings([a.name for a in agents] or [_("No agent is set up")])
        commands = [a.command for a in agents]
        if selected_command in commands:
            drop.set_selected(commands.index(selected_command))
        return drop

    @staticmethod
    def prompt_view(text: str = "", height: int = 90) -> Gtk.TextView:
        view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=6, bottom_margin=6, left_margin=8,
                            right_margin=8)
        view.get_buffer().set_text(text)
        view.add_css_class("am-prompt")
        view.set_size_request(-1, height)
        return view

    @staticmethod
    def view_text(view: Gtk.TextView) -> str:
        buffer = view.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False).strip()

    @staticmethod
    def answer_label(text: str = "") -> Gtk.Label:
        label = Gtk.Label(label=text, xalign=0, yalign=0, wrap=True, selectable=True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.add_css_class("am-chess-said")
        return label

    # --- settings rows ------------------------------------------------------------------------------

    @staticmethod
    def preference_group(box, title: str) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=title)
        box.append(group)
        return group

    def combo_row(self, group, title: str, hint: str, labels: list, selected: int, on_change) -> Adw.ComboRow:
        row = Adw.ComboRow(title=title, subtitle=hint, model=Gtk.StringList.new(labels), use_markup=False)
        row.set_subtitle_lines(0)
        row.set_selected(max(0, selected))
        row.connect("notify::selected", lambda r, *_: on_change(r.get_selected()))
        group.add(row)
        return row
