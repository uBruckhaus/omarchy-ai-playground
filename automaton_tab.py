"""AI Playground's Automaton tab: a cellular automaton to draw on, run, step, and change the rule of
while it runs (the engine is automaton.py). The same tab as Maprisma Playground's, so the texts and
their translations are shared."""

from __future__ import annotations


from gi.repository import Gdk, GLib, Gtk

import ai_core
from automaton import NAMED_RULES, PATTERNS, Automaton, Rule, rule_name
from i18n import _

OWN_RULE = "Own rule…"
DRAW = "Draw cells"
DENSITIES = (.1, .2, .3, .4, .5)
INTRO = ("A cellular automaton: each cell lives or dies by how many of its eight neighbours are alive. Draw cells "
         "with the mouse (the right button erases), place a pattern, or fill at random, then Play. The rule can be "
         "changed while it runs: B is how many neighbours bring a cell to life, S how many keep it alive.")


def _rgb(hex_color: str, fallback=(0.5, 0.5, 0.5)):
    try:
        h = hex_color.lstrip("#")
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except (ValueError, AttributeError):
        return fallback


class AutomatonTab:
    def build_automaton_tab(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=ai_core.GAP, margin_end=14)
        self.tab_intro(box, _("Automaton"), _(INTRO))
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
        row = Gtk.Box(spacing=8)
        row.add_css_class("am-chess-controls")
        self.au_rule_names = list(NAMED_RULES) + [OWN_RULE]
        self.au_rule = Gtk.DropDown.new_from_strings(
            [f"{n}  {NAMED_RULES[n]}" for n in NAMED_RULES] + [_(OWN_RULE)])
        self.au_rule.set_tooltip_text(_("Rule"))
        name = rule_name(rule)
        self.au_rule.set_selected(self.au_rule_names.index(name) if name else len(self.au_rule_names) - 1)
        self.au_rule.connect("notify::selected", lambda *_a: self.au_pick_rule())
        row.append(self.au_rule)
        self.au_rule_text = Gtk.Entry(text=rule.notation, width_chars=12, placeholder_text="B3/S23")
        self.au_rule_text.connect("activate", lambda *_a: self.au_apply_rule(self.au_rule_text.get_text()))
        row.append(self.au_rule_text)
        row.append(self.small_button(_("Use rule"), lambda: self.au_apply_rule(self.au_rule_text.get_text()),
                                     _("B: neighbours a dead cell needs to be born, S: neighbours a live cell needs "
                                       "to stay, C: states of a Generations rule")))
        self.au_tools = [DRAW] + [p.name for p in PATTERNS]
        self.au_tool = Gtk.DropDown.new_from_strings([_(t) for t in self.au_tools])
        self.au_tool.set_tooltip_text(_("What a click does: draw (the right button erases) or place a pattern"))
        self.au_tool.set_margin_start(8)
        row.append(self.au_tool)
        box.append(row)

        # Play, Step, Clear, Random with its density, Speed, Edges wrap.
        row = Gtk.Box(spacing=8)
        row.add_css_class("am-chess-controls")
        self.au_play = self.action_button(_("Play"), self.au_play_pause, _("Space"))
        row.append(self.au_play)
        row.append(self.small_button(_("Step"), self.au_step, _("One generation (→)")))
        row.append(self.small_button(_("Clear"), self.au_clear))
        row.append(self.small_button(_("Random"), self.au_random, _("Fill at random with the density beside it")))
        self.au_density = Gtk.DropDown.new_from_strings([f"{int(d * 100)} %" for d in DENSITIES])
        self.au_density.set_selected(2)
        self.au_density.set_tooltip_text(_("Density of a random fill"))
        row.append(self.au_density)
        label = Gtk.Label(label=_("Speed"), margin_start=8)
        row.append(label)
        self.au_speed_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1, 60, 1)
        self.au_speed_scale.set_value(self.au_speed)
        self.au_speed_scale.set_size_request(160, -1)
        self.au_speed_scale.connect("value-changed", lambda s: self.au_set_speed(int(s.get_value())))
        row.append(self.au_speed_scale)
        self.au_speed_label = Gtk.Label()
        self.au_speed_label.add_css_class("am-hint")
        row.append(self.au_speed_label)
        wrap = Gtk.CheckButton(label=_("Edges wrap"), active=self.au_grid.wrap, margin_start=8)
        wrap.set_tooltip_text(_("On: what leaves at one edge comes back at the other. Off: outside the grid is dead."))
        wrap.connect("toggled", lambda b: (setattr(self.au_grid, "wrap", b.get_active()),
                                           self.save_setting(automaton_wrap=b.get_active())))
        row.append(wrap)
        for child in (row.get_first_child(),):        # the small buttons stay their own height beside Play
            while child is not None:
                child.set_valign(Gtk.Align.CENTER)
                child = child.get_next_sibling()
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

    def au_set_speed(self, speed: int):
        self.au_speed = max(1, min(60, speed))
        self.save_setting(automaton_speed=self.au_speed)
        self.au_schedule()
        self.au_refresh(draw=False)

    def au_refresh(self, draw: bool = True):
        g = self.au_grid
        self.au_play.set_label(_("Pause") if getattr(self, "au_playing", False) else _("Play"))
        self.au_speed_label.set_label(_("{0} / s").format(self.au_speed))
        self.au_status.set_label(_("Generation {0}  ·  {1} alive  ·  {2} × {3} cells").format(
            g.generation, g.population, g.width, g.height))
        self.au_size_hint.set_label(_("Ctrl + wheel: cell size ({0} px)").format(self.au_cell))
        self.au_spark.queue_draw()
        if draw:
            self.au_area.queue_draw()

    # --- the rule ----------------------------------------------------------------------------------

    def au_pick_rule(self):
        name = self.au_rule_names[self.au_rule.get_selected()]
        if name != OWN_RULE:
            self.au_apply_rule(NAMED_RULES[name])

    def au_apply_rule(self, text: str):
        try:
            rule = Rule.parse(text)
        except ValueError as error:
            self.au_rule_text.add_css_class("error")
            self.toast(_(str(error)))
            return
        self.au_rule_text.remove_css_class("error")
        self.au_grid.set_rule(rule)
        self.au_rule_text.set_text(rule.notation)
        self.save_setting(automaton_rule=rule.notation)
        name = rule_name(rule) or OWN_RULE
        index = self.au_rule_names.index(name)
        if self.au_rule.get_selected() != index:
            self.au_rule.set_selected(index)
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
        for state in range(1, states):
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
        cr.set_source_rgba(*fg, 0.12)
        cr.rectangle(.5, .5, w - 1, h - 1)
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
