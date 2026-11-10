"""AI Playground's Automaton tab: a cellular automaton to draw on, run, step, and change the rule of
while it runs (the engine is automaton.py). The same tab as Maprisma Playground's, so the texts and
their translations are shared."""

from __future__ import annotations


from gi.repository import Adw, Gdk, GLib, Gtk

import math

import ai_core
import corners
from automaton import MIXER_NAME, NAMED_RULES, PATTERNS, Automaton, Rule, find_pattern, is_materials_rule, rule_name
from automaton_info import MATERIALS_NOTE, PATTERN_INFO, RULE_INFO
from i18n import N_, _

OWN_RULE = "Own rule…"
DRAW = "Draw cells"
DENSITIES = (.1, .2, .3, .4, .5)
# Rules under which the Life patterns (glider, pulsar, gun …) behave as described.
LIFE_LIKE = ("B3/S23", "B36/S23")
PATTERN_NOTE = N_("This pattern is made for Conway's Life. Under the rule of “{0}” it may grow, fall apart or "
                  "stand still instead; choose Conway's Life above to see it as described.")

INTRO = ("A cellular automaton: each cell lives or dies by how many of its eight neighbours are alive. Draw cells "
         "with the mouse (the right button erases), place a pattern, or fill at random, then Play. The rule can be "
         "changed while it runs: B is how many neighbours bring a cell to life, S how many keep it alive.")


# The colours by age: they tell what a cell is doing. Born now is green; young cells (what moves,
# gliders, oscillators) go from yellow to orange; settled ones turn cyan; what has stood still a
# long time (still lifes) is violet. A cell that has just died leaves a faint red trace.
BORN, YOUNG, ACTIVE, SETTLED, STILL, DIED = "#4ade80", "#facc15", "#fb923c", "#22d3ee", "#a78bfa", "#f87171"
STOPS = ((1, BORN), (3, YOUNG), (8, ACTIVE), (25, SETTLED), (80, STILL))
RESIN, HARDENER = "#fb923c", "#22d3ee"
LEGEND = ((BORN, "Born"), (YOUNG, "Young: moves or changes"), (ACTIVE, "Active"), (SETTLED, "Settled"),
          (STILL, "Standing still"), (DIED, "Just died"))


def age_color(age: int) -> tuple:
    """The colour of a live cell of this age (on a log scale: the first generations change quickly)."""
    if age <= STOPS[0][0]:
        return _rgb(STOPS[0][1])
    for (a0, c0), (a1, c1) in zip(STOPS, STOPS[1:]):
        if age <= a1:
            t = (math.log(age) - math.log(a0)) / (math.log(a1) - math.log(a0))
            return tuple(p + (q - p) * t for p, q in zip(_rgb(c0), _rgb(c1)))
    return _rgb(STOPS[-1][1])


def _rgb(hex_color: str, fallback=(0.5, 0.5, 0.5)):
    try:
        h = hex_color.lstrip("#")
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except (ValueError, AttributeError):
        return fallback


class AutomatonTab:
    @staticmethod
    def au_flow_row(spacing: int = 12, **props) -> Adw.WrapBox:
        """A row of control groups that wraps onto the next line in a narrow window (the window can
        then be narrower than all the controls side by side, and the header stays on screen); each
        line packs its groups at their own width."""
        row = Adw.WrapBox(child_spacing=spacing, line_spacing=6, **props)
        row.add_css_class("am-chess-controls")
        return row

    @staticmethod
    def au_group(*widgets) -> Gtk.Box:
        """Controls that belong together and wrap as one (a choice and its ⓘ, Speed and its scale)."""
        group = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        group.add_css_class("am-chess-controls")
        for widget in widgets:
            widget.set_valign(Gtk.Align.CENTER)       # the small buttons stay their own height beside Play
            group.append(widget)
        return group

    def build_automaton_tab(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=ai_core.GAP)
        self.tab_intro(box, _("Automaton"), _(INTRO))
        self.au_intro = box.get_last_child().get_last_child()     # tells what the chosen scenario does
        self.au_speed = int(ai_core.setting("automaton_speed"))
        self.au_cell = int(ai_core.setting("automaton_cell"))
        try:
            rule = Rule.parse(ai_core.setting("automaton_rule"))
        except ValueError:
            rule = Rule.parse("B3/S23")
        self.au_grid = Automaton(80, 50, rule, wrap=bool(ai_core.setting("automaton_wrap")))
        self.au_seeded = False
        self.au_timer = 0
        self.au_paint = None          # while dragging: 1 draws, 0 erases
        self.au_last = None

        # Rule, own rule, Use rule, what a click does.
        row = self.au_flow_row()
        self.au_rule_names = list(NAMED_RULES) + [MIXER_NAME, OWN_RULE]
        self.au_rule = Gtk.DropDown.new_from_strings(
            [f"{_(n) if is_materials_rule(n) else n}  {NAMED_RULES[n]}" for n in NAMED_RULES]
            + [_(MIXER_NAME), _(OWN_RULE)])
        self.au_rule.set_tooltip_text(_("Rule"))
        name = rule_name(rule)
        self.au_rule.set_selected(self.au_rule_names.index(name) if name else len(self.au_rule_names) - 1)
        self.au_rule.connect("notify::selected", lambda *_a: self.au_pick_rule())
        self.au_show_scenario()                        # the saved scenario's text from the start
        row.append(self.au_group(self.au_rule,
                                 self.icon_button("help-about-symbolic", self.au_explain_rule, _("About this rule"))))
        self.au_rule_text = Gtk.Entry(text=rule.notation, width_chars=12, placeholder_text="B3/S23")
        self.au_rule_text.connect("activate", lambda *_a: self.au_apply_rule(self.au_rule_text.get_text()))
        row.append(self.au_group(
            self.au_rule_text,
            self.small_button(_("Use rule"), lambda: self.au_apply_rule(self.au_rule_text.get_text()),
                              _("B: neighbours a dead cell needs to be born, S: neighbours a live cell needs "
                                "to stay, C: states of a Generations rule"))))
        self.au_tools = [DRAW] + [p.name for p in PATTERNS]
        self.au_tool = Gtk.DropDown.new_from_strings([_(t) for t in self.au_tools])
        self.au_tool.set_tooltip_text(_("What a click does: draw (the right button erases) or place a pattern"))
        row.append(self.au_group(self.au_tool, self.icon_button("help-about-symbolic", self.au_explain_pattern,
                                                                _("About this pattern"))))
        box.append(row)

        # Play, Step, Clear, Random with its density, Speed, Edges wrap.
        row = self.au_flow_row()
        self.au_play = self.action_button(_("Play"), self.au_play_pause, _("Space"))
        row.append(self.au_group(self.au_play, self.small_button(_("Step"), self.au_step, _("One generation (→)")),
                                 self.small_button(_("Clear"), self.au_clear)))
        random_button = self.small_button(_("Random"), self.au_random, _("Fill at random with the density beside it"))
        self.au_density = Gtk.DropDown.new_from_strings([f"{int(d * 100)} %" for d in DENSITIES])
        self.au_density.set_selected(2)
        self.au_density.set_tooltip_text(_("Density of a random fill"))
        row.append(self.au_group(random_button, self.au_density))
        label = Gtk.Label(label=_("Speed"))
        self.au_speed_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1, 60, 1)
        self.au_speed_scale.set_value(self.au_speed)
        self.au_speed_scale.set_size_request(160, -1)
        self.au_speed_scale.connect("value-changed", lambda s: self.au_set_speed(int(s.get_value())))
        self.au_speed_label = Gtk.Label()
        self.au_speed_label.add_css_class("am-hint")
        row.append(self.au_group(label, self.au_speed_scale, self.au_speed_label))
        self.au_by_age = ai_core.setting("automaton_colors") == "age"
        self.au_colors_choice = Gtk.DropDown.new_from_strings([_("Colours by age"), _("One colour")])
        self.au_colors_choice.set_selected(0 if self.au_by_age else 1)
        self.au_colors_choice.set_tooltip_text(
            _("By age: what is born, moves or stands still and what has just died, each in its own colour"))
        self.au_colors_choice.connect("notify::selected", lambda d, *_a: self.au_set_colors(d.get_selected() == 0))
        row.append(self.au_group(self.au_colors_choice))
        wrap = Gtk.CheckButton(label=_("Edges wrap"), active=self.au_grid.wrap)
        self.au_wrap_check = wrap
        wrap.set_tooltip_text(_("On: what leaves at one edge comes back at the other. Off: outside the grid is dead."))
        wrap.connect("toggled", lambda b: (setattr(self.au_grid, "wrap", b.get_active()),
                                           self.save_setting(automaton_wrap=b.get_active())))
        row.append(self.au_group(wrap))
        box.append(row)

        # Generation, population, size; the sparkline; the cell size hint.
        row = Gtk.Box(spacing=12)
        self.au_status = Gtk.Label(xalign=0)
        self.au_status.add_css_class("am-hint")
        row.append(self.au_status)
        self.au_spark = Gtk.DrawingArea(hexpand=True, content_height=22)
        self.au_spark.set_draw_func(self.au_draw_spark)
        row.append(self.au_spark)
        self.au_size_hint = Gtk.Label(xalign=1)
        self.au_size_hint.add_css_class("am-hint")
        row.append(self.au_size_hint)
        box.append(row)

        # What the colours mean, while they are on.
        self.au_legend = self.au_flow_row(16, visible=self.au_by_age)
        for color, text in LEGEND:
            item = Gtk.Box(spacing=6)
            swatch = Gtk.DrawingArea(content_width=11, content_height=11, valign=Gtk.Align.CENTER)
            swatch.set_draw_func(lambda _a, cr, w, h, c=color: (cr.set_source_rgba(*_rgb(c), .35 if c == DIED else 1),
                                                                corners.rounded_path(cr, 0, 0, w, h, min(getattr(self, 'ui_radius', 0), 3)), cr.fill()))
            item.append(swatch)
            label = Gtk.Label(label=_(text))
            label.add_css_class("am-hint")
            item.append(label)
            self.au_legend.append(item)
        box.append(self.au_legend)
        self.au_mix_legend = self.au_flow_row(16, visible=False)
        for color, text in ((RESIN, "Resin (component A)"), (HARDENER, "Hardener (component B)")):
            item = Gtk.Box(spacing=6)
            swatch = Gtk.DrawingArea(content_width=11, content_height=11, valign=Gtk.Align.CENTER)
            swatch.set_draw_func(lambda _a, cr, w, h, c=color: (cr.set_source_rgb(*_rgb(c)), corners.rounded_path(
                cr, 0, 0, w, h, min(getattr(self, 'ui_radius', 0), 3)),
                                                                cr.fill()))
            item.append(swatch)
            label = Gtk.Label(label=_(text))
            label.add_css_class("am-hint")
            item.append(label)
            self.au_mix_legend.append(item)
        box.append(self.au_mix_legend)

        # The grid fills the rest of the tab.
        self.au_area = Gtk.DrawingArea(hexpand=True, vexpand=True, focusable=True)
        self.au_area.set_draw_func(self.au_draw)
        self.au_area.set_cursor_from_name("crosshair")
        press = Gtk.GestureClick(button=0)
        press.connect("pressed", self.au_pressed)
        press.connect("released", lambda *_a: setattr(self, "au_paint", None))
        self.au_area.add_controller(press)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self.au_motion)
        self.au_area.add_controller(motion)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self.au_scroll)
        self.au_area.add_controller(scroll)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.au_key)
        self.au_area.add_controller(keys)
        box.append(self.au_area)
        self.au_refresh()
        return box

    # --- running -----------------------------------------------------------------------------------

    def au_running(self) -> bool:
        return bool(self.au_timer)

    def au_schedule(self):
        if self.au_timer:
            GLib.source_remove(self.au_timer)
            self.au_timer = 0
        if getattr(self, "au_playing", False):
            self.au_timer = GLib.timeout_add(max(16, 1000 // self.au_speed), self.au_tick)

    def au_tick(self) -> bool:
        # Only while the tab is in view: a hidden tab costs nothing.
        if self.page_name() != "automaton":
            return True
        changed = self.au_grid.step()
        if not changed or self.au_grid.empty:
            self.au_playing = False          # nothing moves any more: a still life does not burn CPU
            self.au_timer = 0
            self.au_refresh()
            return False
        self.au_area.queue_draw()
        if self.au_grid.generation % 5 == 0:
            self.au_refresh(draw=False)
        return True

    def au_play_pause(self):
        self.au_playing = not getattr(self, "au_playing", False)
        self.au_schedule()
        self.au_refresh()

    def au_step(self):
        if getattr(self, "au_playing", False):
            self.au_play_pause()
        self.au_grid.step()
        self.au_refresh()

    def au_clear(self):
        self.au_grid.clear()
        self.au_playing = False
        self.au_schedule()
        self.au_refresh()

    def au_random(self):
        self.au_grid.randomize(DENSITIES[self.au_density.get_selected()])
        self.au_refresh()

    def au_set_colors(self, by_age: bool):
        self.au_by_age = by_age
        self.au_legend.set_visible(by_age)
        self.save_setting(automaton_colors="age" if by_age else "plain")
        self.au_area.queue_draw()

    def au_set_speed(self, speed: int):
        self.au_speed = max(1, min(60, speed))
        self.save_setting(automaton_speed=self.au_speed)
        self.au_schedule()
        self.au_refresh(draw=False)

    def au_refresh(self, draw: bool = True):
        g = self.au_grid
        self.au_play.set_label(_("Pause") if getattr(self, "au_playing", False) else _("Play"))
        self.au_speed_label.set_label(_("{0} / s").format(self.au_speed))
        if g.mixing:
            self.au_status.set_label(_("Mixer element {0}  ·  {1} layers  ·  {2} × {3} cells").format(
                g.generation, g.layers, g.width, g.height))
        else:
            self.au_status.set_label(_("Generation {0}  ·  {1} alive  ·  {2} × {3} cells").format(
                g.generation, g.population, g.width, g.height))
        self.au_legend.set_visible(self.au_by_age and not g.mixing)
        self.au_mix_legend.set_visible(g.mixing)
        self.au_size_hint.set_label(_("Ctrl + wheel: cell size ({0} px)").format(self.au_cell))
        self.au_spark.queue_draw()
        if draw:
            self.au_area.queue_draw()

    # --- the hint windows -------------------------------------------------------------------------

    def au_explain_rule(self):
        name = self.au_rule_names[self.au_rule.get_selected()]
        if name == OWN_RULE:
            self.au_explain(_(OWN_RULE), self.au_rule_text.get_text(), RULE_INFO["own"])
        elif name == MIXER_NAME:
            self.au_explain(_(MIXER_NAME), "", RULE_INFO[MIXER_NAME], materials=True)
        else:
            materials = is_materials_rule(name)
            self.au_explain(_(name) if materials else name, NAMED_RULES[name], RULE_INFO[name], materials=materials)

    def au_explain_pattern(self):
        """The second ⓘ follows the scenario too: drawing explains what to look for and try in the
        chosen scenario (and Try it sets that up); a Life pattern under another rule says so."""
        tool = self.au_tools[self.au_tool.get_selected()]
        scenario = self.au_rule_names[self.au_rule.get_selected()]
        scenario_title = _(scenario) if scenario in (OWN_RULE, MIXER_NAME) or is_materials_rule(scenario) else scenario
        if tool == DRAW:
            info = dict(PATTERN_INFO["draw"])
            if scenario != OWN_RULE and scenario in RULE_INFO:
                own = RULE_INFO[scenario]
                info.update({"watch": own["watch"], "try": own["try"], "setup": own["setup"]})
            self.au_explain(f"{_(tool)} · {scenario_title}", "", info)
            return
        note = ""
        if self.au_grid.mixing or self.au_grid.rule.notation not in LIFE_LIKE:
            note = _(PATTERN_NOTE).format(scenario_title)
        self.au_explain(_(tool), "", PATTERN_INFO[tool], note=note)

    def au_explain(self, title: str, notation: str, info: dict, materials: bool = False, note: str = ""):
        """What the scenario is, what to look for, how to try it; "Try it" sets it up and starts it."""
        from gi.repository import Adw, Pango
        dialog = Adw.AlertDialog(heading=title + (f"   {notation}" if notation else ""))
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        parts = [("starred-symbolic", _("What happens"), info["what"]),
                 ("view-reveal-symbolic", _("What to look for"), info["watch"]),
                 ("media-playback-start-symbolic", _("Try this"), info["try"])]
        if materials:
            parts.append(("applications-science-symbolic", _("Good to know"), MATERIALS_NOTE))
        if note:
            parts.append(("dialog-information-symbolic", _("Good to know"), note))
        for icon, heading, text in parts:
            part = Gtk.Box(spacing=10)
            part.append(Gtk.Image(icon_name=icon, valign=Gtk.Align.START))
            texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
            head = Gtk.Label(label=heading.upper(), xalign=0)
            head.add_css_class("am-section-title")
            texts.append(head)
            line = Gtk.Label(label=_(text), xalign=0, wrap=True, max_width_chars=60)
            line.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            texts.append(line)
            part.append(texts)
            body.append(part)
        if info.get("links"):
            part = Gtk.Box(spacing=10)
            part.append(Gtk.Image(icon_name="web-browser-symbolic", valign=Gtk.Align.START))
            texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
            head = Gtk.Label(label=_("Read more").upper(), xalign=0)
            head.add_css_class("am-section-title")
            texts.append(head)
            row = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=6, row_spacing=6,
                              max_children_per_line=4)
            for title_text, url in info["links"]:
                # Only the fixed https addresses of automaton_info, opened in the browser.
                row.append(self.small_button(f"{title_text}  ↗", lambda u=url: self.au_open_page(u), url))
            texts.append(row)
            part.append(texts)
            body.append(part)
        dialog.set_extra_child(body)
        dialog.add_response("close", _("Close"))
        dialog.add_response("try", _("Try it"))
        dialog.set_response_appearance("try", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("try")
        dialog.set_close_response("close")
        dialog.connect("response", lambda _d, answer: answer == "try" and self.au_try(info["setup"]))
        dialog.present(self.window)

    def au_open_page(self, url: str):
        if url.startswith("https://"):
            Gtk.UriLauncher.new(url).launch(self.window, None, None)

    def au_try(self, setup: dict):
        """The scenario's rule, edges and start (a random fill or a pattern in the middle of an empty
        grid, or the two components of the mixer), then it runs (or waits for Step)."""
        g = self.au_grid
        if "speed" in setup:
            self.au_speed_scale.set_value(setup["speed"])
        if setup.get("mixer"):
            self.au_rule.set_selected(self.au_rule_names.index(MIXER_NAME))
            g.mixing = True
            g.fill_halves()
            self.au_playing = setup.get("play", True)
            self.au_schedule()
            self.au_refresh()
            return
        if setup.get("rule"):
            self.au_apply_rule(setup["rule"])
        if "wrap" in setup and setup["wrap"] != g.wrap:
            g.wrap = setup["wrap"]
            self.save_setting(automaton_wrap=g.wrap)
            if hasattr(self, "au_wrap_check"):
                self.au_wrap_check.set_active(g.wrap)
        g.clear()
        if "density" in setup:
            g.randomize(setup["density"])
        elif "pattern" in setup:
            p = find_pattern(setup["pattern"])
            g.stamp(p, (g.width - p.width) // 2, (g.height - p.height) // 2)
        self.au_playing = setup.get("play", True)
        self.au_schedule()
        self.au_refresh()

    # --- the rule ----------------------------------------------------------------------------------

    def au_show_scenario(self):
        """The text under the heading follows the chosen scenario (its "What happens"); with an own
        rule it is the tab's general explanation."""
        name = self.au_rule_names[self.au_rule.get_selected()]
        info = RULE_INFO.get(name) if name != OWN_RULE else None
        self.au_intro.set_label(_(info["what"]) if info else _(INTRO))

    def au_pick_rule(self):
        name = self.au_rule_names[self.au_rule.get_selected()]
        self.au_show_scenario()
        self.au_grid.mixing = name == MIXER_NAME
        if name == MIXER_NAME:
            self.au_grid.fill_halves()      # resin above, hardener below: ready for the first element
            self.au_refresh()
        elif name != OWN_RULE:
            self.au_apply_rule(NAMED_RULES[name])

    def au_apply_rule(self, text: str):
        try:
            rule = Rule.parse(text)
        except ValueError as error:
            self.au_rule_text.add_css_class("error")
            self.toast(_(str(error)))
            return
        self.au_rule_text.remove_css_class("error")
        self.au_grid.mixing = False
        self.au_grid.set_rule(rule)
        self.au_rule_text.set_text(rule.notation)
        self.save_setting(automaton_rule=rule.notation)
        name = rule_name(rule) or OWN_RULE
        index = self.au_rule_names.index(name)
        if self.au_rule.get_selected() != index:
            self.au_rule.set_selected(index)
        self.au_show_scenario()
        self.au_refresh()

    # --- drawing -----------------------------------------------------------------------------------

    def au_cell_at(self, x: float, y: float):
        cx, cy = int(x // self.au_cell), int(y // self.au_cell)
        if 0 <= cx < self.au_grid.width and 0 <= cy < self.au_grid.height:
            return cx, cy
        return None

    def au_pressed(self, gesture, _n, x, y):
        self.au_area.grab_focus()
        cell = self.au_cell_at(x, y)
        if cell is None:
            return
        erase = gesture.get_current_button() == Gdk.BUTTON_SECONDARY
        tool = self.au_tools[self.au_tool.get_selected()]
        if not erase and tool != DRAW:
            pattern = next(p for p in PATTERNS if p.name == tool)
            # The pattern's middle goes where the click was.
            self.au_grid.stamp(pattern, cell[0] - pattern.width // 2, cell[1] - pattern.height // 2)
            self.au_paint = None
        else:
            # A click on a live cell with the left button erases it, as in most Life programs.
            self.au_paint = 0 if erase else (0 if self.au_grid.cell(*cell) == 1 else 1)
            self.au_grid.set(*cell, self.au_paint)
            self.au_last = cell
        self.au_refresh()

    def au_motion(self, _controller, x, y):
        if self.au_paint is None:
            return
        cell = self.au_cell_at(x, y)
        if cell is None:
            return
        last = self.au_last or cell
        steps = max(abs(cell[0] - last[0]), abs(cell[1] - last[1]))
        for i in range(steps + 1):                 # a fast stroke stays a line
            t = 1.0 if steps == 0 else i / steps
            self.au_grid.set(round(last[0] + (cell[0] - last[0]) * t), round(last[1] + (cell[1] - last[1]) * t),
                             self.au_paint)
        self.au_last = cell
        self.au_area.queue_draw()

    def au_scroll(self, controller, _dx, dy) -> bool:
        if not controller.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK:
            return False
        size = max(4, min(24, self.au_cell + (1 if dy < 0 else -1)))
        if size != self.au_cell:
            self.au_cell = size
            self.save_setting(automaton_cell=size)
            self.au_area.queue_draw()
            self.au_refresh(draw=False)
        return True

    def au_key(self, _controller, keyval, _keycode, _state) -> bool:
        if keyval == Gdk.KEY_space:
            self.au_play_pause()
            return True
        if keyval == Gdk.KEY_Right:
            self.au_step()
            return True
        return False

    def au_colors(self):
        colors = getattr(getattr(self, "theme", None), "colors", None) or {}
        fg = _rgb(colors.get("foreground", "#d0d0d0"))
        bg = _rgb(colors.get("background", "#1e1e24"))
        accent = _rgb(colors.get("accent", "#7aa2f7"))
        yellow = _rgb(colors.get("yellow", "#e0af68"))
        return fg, bg, accent, yellow

    def au_draw(self, _area, cr, width: int, height: int):
        cell = self.au_cell
        cols, rows = max(1, width // cell), max(1, height // cell)
        g = self.au_grid
        if (cols, rows) != (g.width, g.height):
            g.resize(cols, rows)
            GLib.idle_add(lambda: (self.au_refresh(draw=False), False)[1])
        if not self.au_seeded:
            self.au_seeded = True
            if g.empty:
                g.randomize(DENSITIES[self.au_density.get_selected()])
            GLib.idle_add(lambda: (self.au_refresh(draw=False), False)[1])
        fg, bg, accent, yellow = self.au_colors()
        w, h = g.width * cell, g.height * cell
        corner = min(getattr(self, "ui_radius", 0), 12)       # Settings › Corners, as the cards
        cr.save()
        corners.rounded_path(cr, 0, 0, w, h, corner)
        cr.clip()
        cr.set_source_rgba(*fg, 0.04)
        cr.rectangle(0, 0, w, h)
        cr.fill()
        if cell >= 8:
            cr.set_source_rgba(*fg, 0.06)
            cr.set_line_width(1)
            for x in range(1, g.width):
                cr.move_to(x * cell + .5, 0)
                cr.line_to(x * cell + .5, h)
            for y in range(1, g.height):
                cr.move_to(0, y * cell + .5)
                cr.line_to(w, y * cell + .5)
            cr.stroke()
        inset = 1 if cell >= 8 else 0
        states = g.rule.states
        if g.mixing:
            # Resin and hardener: every cell is one of the two components.
            cr.set_source_rgba(*_rgb(HARDENER), .85)
            cr.rectangle(0, 0, w, h)
            cr.fill()
            cr.set_source_rgb(*_rgb(RESIN))
            for i, c in enumerate(g.cells):
                if c == 1:
                    cr.rectangle((i % g.width) * cell, (i // g.width) * cell, cell, cell)
            cr.fill()
            return
        if self.au_by_age:
            # A faint red trace where a cell just died, then the live cells grouped by their colour.
            cr.set_source_rgba(*_rgb(DIED), .35)
            for i, d in enumerate(g.died):
                if d:
                    cr.rectangle((i % g.width) * cell + inset, (i // g.width) * cell + inset, cell - 2 * inset,
                                 cell - 2 * inset)
            cr.fill()
            groups: dict = {}
            for i, c in enumerate(g.cells):
                if c == 1:
                    groups.setdefault(min(80, max(1, g.ages[i])), []).append(i)
            for age, cells in groups.items():
                cr.set_source_rgb(*age_color(age))
                for i in cells:
                    cr.rectangle((i % g.width) * cell + inset, (i // g.width) * cell + inset, cell - 2 * inset,
                                 cell - 2 * inset)
                cr.fill()
        for state in range(1 if not self.au_by_age else 2, states):
            if state == 1:
                cr.set_source_rgb(*accent)
            else:                                 # dying states fade from yellow to the background
                t = (state - 1) / (states - 1) * .8
                cr.set_source_rgb(*(a + (b - a) * t for a, b in zip(yellow, bg)))
            for y in range(g.height):
                base = y * g.width
                for x in range(g.width):
                    if g.cells[base + x] == state:
                        cr.rectangle(x * cell + inset, y * cell + inset, cell - 2 * inset, cell - 2 * inset)
            cr.fill()
        cr.restore()
        cr.set_source_rgba(*fg, 0.12)
        corners.rounded_path(cr, .5, .5, w - 1, h - 1, corner)
        cr.stroke()

    def au_draw_spark(self, _area, cr, width: int, height: int):
        values = self.au_grid.history
        if len(values) < 2:
            return
        top, bottom = max(values), min(values)
        span = max(1, top - bottom)
        _fg, _bg, accent, _y = self.au_colors()
        cr.set_source_rgb(*accent)
        cr.set_line_width(1.5)
        for i, v in enumerate(values):
            x = width * i / 199
            y = height - 2 - (v - bottom) / span * (height - 4)
            (cr.move_to if i == 0 else cr.line_to)(x, y)
        cr.stroke()
