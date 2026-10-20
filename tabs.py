"""AI Playground's test tabs besides chess: Speed and cost, Head to head, Vision and Local LLMs
(mixins of ai_playground.Playground). Every question runs off the main thread; its answer comes
back with GLib.idle_add and is dropped when a newer run has started (the run counter)."""

from __future__ import annotations

import os
import random
import shutil
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from gi.repository import Gio, GLib, Gtk

import agent_run
import ai_core
import gpu_check
import local_llm
import picture_check
from i18n import _

SPEED_PROMPT = "Write a Python function that tells whether a string is a palindrome, ignoring case and spaces. " \
               "Answer with the code only."
DUEL_PROMPT = "Explain in three sentences why the sky is blue, for a ten-year-old."
VISION_PROMPT = "Describe what you see in two or three sentences."
VIDEO_PROMPT = "These are frames of one video, in order. Describe what happens in two or three sentences."
LOCAL_PROMPT = "In one sentence: what is a large language model?"
PICTURE_SIDE = 1600          # pictures are scaled down to this (and saved again: no metadata goes out)
VIDEO_FRAMES = 4


def _fmt_seconds(seconds: float) -> str:
    return f"{seconds:.1f} s"


def result_line(result) -> str:
    """12.3 s · 450 tokens · $0.0210, as far as the agent reports them."""
    parts = [_fmt_seconds(result.seconds)]
    if result.tokens_out is not None:
        parts.append(_("{0} tokens").format(result.tokens_out))
    if getattr(result, "cost", None) is not None:
        parts.append(f"${result.cost:.4f}")
    return " · ".join(parts)


class GpuGuard:
    """Before two or more local models answer together: do they fit in the free GPU memory
    (gpu_check)? If not, or if that cannot be told, ask: one after the other, both at once, or
    not at all. go(one_by_one) runs the test."""

    def gpu_guard(self, make_wanted, go, alternating: bool = False):
        """make_wanted() -> [(label, server, model)] (runs off the main thread: it reads the agents'
        settings). alternating: they never answer at the same time (chess), only both stay loaded."""
        def work():
            wanted = make_wanted()
            result = gpu_check.check(wanted) if wanted else None
            GLib.idle_add(decide, result)

        def decide(result):
            if result is None or result.fits is True or not result.total:
                go(False)                      # one model, it fits, or no GPU figures to go by
            else:
                self.gpu_dialog(result, go, alternating)
            return False
        threading.Thread(target=work, daemon=True).start()

    def gpu_dialog(self, result, go, alternating: bool):
        from gi.repository import Adw
        gb = lambda size: f"{size / gpu_check.GIB:.1f}"
        lines = []
        for need in result.needs:
            if need.loaded:
                lines.append(_("{0}: loaded already").format(need.label))
            elif need.size is None:
                lines.append(_("{0}: size not found").format(need.label))
            else:
                lines.append(_("{0}: about {1} GB").format(need.label, gb(need.bytes_needed)))
        if result.fits is False:
            lines.append(_("Together about {0} GB more are needed; {1} GB of {2} GB GPU memory are free.").format(
                gb(result.needed), gb(result.free), gb(result.total)))
        else:
            lines.append(_("{0} GB of {1} GB GPU memory are free; not every size was found.").format(
                gb(result.free), gb(result.total)))
        if alternating:
            lines.append(_("They answer in turn, but both may stay loaded: the local LLM may have to swap them "
                           "for every move (slow), or loading the second one fails."))
        else:
            lines.append(_("Loading them at once can fail or make the computer stall. One after the other asks "
                           "the next only when the one before has answered."))
        dialog = Adw.AlertDialog(heading=_("These local models may not fit in GPU memory together"),
                                 body="\n".join(lines))
        dialog.add_response("cancel", _("Cancel"))
        if alternating:
            dialog.add_response("both", _("Play anyway"))
            dialog.set_default_response("cancel")
        else:
            dialog.add_response("both", _("Both at once"))
            dialog.add_response("one", _("One after the other"))
            dialog.set_response_appearance("one", Adw.ResponseAppearance.SUGGESTED)
            dialog.set_default_response("one")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, answer: answer != "cancel" and go(answer == "one"))
        dialog.present(self.window)

    def agents_wanted(self, contestants: list):
        """A make_wanted for these agents and local models: the local model each one uses."""
        def make():
            wanted = []
            for c in contestants:
                used = self.model_of(c)
                if used is not None:
                    label = used[1] if getattr(c, "kind", "agent") == "model" else f"{c.name}: {used[1]}"
                    wanted.append((label, used[0], used[1]))
            return wanted
        return make

    # --- contestants: the agents set up here, and the models of the local LLM servers running ------

    @staticmethod
    def as_contestant(agent):
        return SimpleNamespace(id=agent.command, name=agent.name, kind="agent", command=agent.command,
                               server=None, model="")

    def contestants(self, with_models: bool = True) -> list:
        """The agents that are set up, then (with_models) every model of the local LLM servers found
        on the Local LLMs tab: one agent is enough to compare it with a local model."""
        found = [self.as_contestant(a) for a in self.ready_agents]
        if with_models:
            found += [SimpleNamespace(id=f"local:{server.key}:{model}", name=f"{server.name} · {model}", kind="model",
                                      command="", server=server, model=model)
                      for server, model in getattr(self, "local_choices", [])]
        return found

    @staticmethod
    def model_of(contestant):
        """(server key, model) of the local model it uses: the model itself, or the agent's."""
        if getattr(contestant, "kind", "agent") == "model":
            return contestant.server.key, contestant.model
        return gpu_check.agent_model(contestant.command)

    def ask_contestant(self, contestant, prompt: str, images=()):
        """Blocking: an agent through its own command, a local model through its server's API."""
        if getattr(contestant, "kind", "agent") == "model":
            return local_llm.ask(contestant.server.port, contestant.model, prompt)
        return agent_run.ask(contestant.command, prompt, images)

    def make_room(self, contestant):
        """After its answer, when asked one after the other: Ollama unloads the model."""
        used = self.model_of(contestant)
        if used is not None:
            gpu_check.unload(*used)

    def contestants_changed(self):
        """The Local LLMs tab found its servers: the tabs that take local models show them too."""
        if getattr(self, "speed_checks_box", None) is not None:
            self.speed_fill()
        if getattr(self, "duel_a", None) is not None:
            self.duel_fill()


class AgentChecks(GpuGuard):
    """Tick boxes for the agents a run asks (remembered in playground_agents)."""

    def agent_checks(self, contestants: list, box: Gtk.FlowBox | None = None) -> tuple[Gtk.Widget, dict]:
        """A tick box per agent or local model (in box, emptied first). Ticked: as saved; before
        anything is saved, every agent and no local model."""
        saved = ai_core.setting("playground_agents") or []
        if box is None:
            box = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=12, row_spacing=4,
                              max_children_per_line=6)
        box.remove_all()
        checks = {}
        for c in contestants:
            check = Gtk.CheckButton(label=c.name, active=c.id in saved if saved else c.kind == "agent")
            check.connect("toggled", lambda *_a: self.save_setting(
                playground_agents=[i for i, b in checks.items() if b.get_active()]))
            checks[c.id] = check
            box.append(check)
        if not contestants:
            box.append(Gtk.Label(label=_("No agent that can be asked is set up."), xalign=0))
        return box, checks

    def run_agents(self, agents: list, checks: dict, prompt: str, results: Gtk.Box, images=(),
                   counter: str = "speed_run"):
        """Ask every ticked agent the same question (at once, or one after the other when their
        local models may not fit in GPU memory together); a card per agent with its time."""
        chosen = [c for c in agents if checks.get(c.id) is not None and checks[c.id].get_active()]
        if not prompt or not chosen:
            return
        self.gpu_guard(self.agents_wanted(chosen),
                       lambda one_by_one: self.run_chosen(chosen, prompt, results, images, counter, one_by_one))

    def run_chosen(self, chosen: list, prompt: str, results: Gtk.Box, images, counter: str, one_by_one: bool):
        while (child := results.get_first_child()) is not None:
            results.remove(child)
        run = getattr(self, counter, 0) + 1
        setattr(self, counter, run)
        start = time.monotonic()
        jobs = []
        for agent in chosen:
            card = self.group_card(results, f"run-{agent.id}", agent.name, summary=_("asking…"),
                                   default_open=True)
            answer = self.answer_label(_("asking…"))
            card.append(answer)
            card.group_summary.set_visible(True)

            flag = [False]                     # set when the answer is in: the clock stops

            def tick(summary=card.group_summary, flag=flag):
                if getattr(self, counter) != run or flag[0]:
                    return False
                summary.set_label(_("asking… {0}").format(_fmt_seconds(time.monotonic() - start)))
                return True
            GLib.timeout_add(500, tick)

            def work(agent=agent, card=card, answer=answer, flag=flag):
                result = self.ask_contestant(agent, prompt, images)
                GLib.idle_add(self.show_result, run, counter, card, answer, result, flag)
                if one_by_one:
                    self.make_room(agent)                # room for the next one (Ollama)
            jobs.append(work)
        if one_by_one:
            def in_turn():
                for job in jobs:
                    if getattr(self, counter) != run:
                        return                           # a newer run started
                    job()
            threading.Thread(target=in_turn, daemon=True).start()
        else:
            for job in jobs:
                threading.Thread(target=job, daemon=True).start()

    def show_result(self, run, counter, card, answer, result, flag):
        if getattr(self, counter) != run:
            return False
        flag[0] = True
        card.group_summary.set_label(result.error or result_line(result))
        answer.set_label(result.text or result.error)
        return False


class SpeedTab(AgentChecks):
    def build_speed_tab(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=ai_core.GAP)
        self.tab_intro(box, _("Speed and cost"),
                       _("The same question to several agents and local models at once: how long each one takes, "
                         "and its tokens and cost where it reports them (Claude Code and the local LLMs do)."))
        self.speed_prompt = self.prompt_view(SPEED_PROMPT)
        box.append(self.speed_prompt)
        self.speed_checks_box, self.speed_checks = self.agent_checks(self.contestants())
        box.append(self.speed_checks_box)
        box.append(self.action_button(_("Ask"), self.speed_ask, _("Every ticked agent gets the question")))
        self.speed_results = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=ai_core.GAP)
        box.append(self.speed_results)
        return self.page_scroller(box)

    def speed_fill(self):
        _box, self.speed_checks = self.agent_checks(self.contestants(), self.speed_checks_box)

    def speed_ask(self):
        self.run_agents(self.contestants(), self.speed_checks, self.view_text(self.speed_prompt),
                        self.speed_results, counter="speed_run")


class DuelTab(GpuGuard):
    def build_duel_tab(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=ai_core.GAP)
        self.tab_intro(box, _("Head to head"),
                       _("One question to two agents or local models; you pick the better answer and the score is "
                         "kept. Blind: you see which one wrote which only after you have chosen."))
        self.duel_prompt = self.prompt_view(DUEL_PROMPT)
        box.append(self.duel_prompt)
        row = Gtk.Box(spacing=8)
        self.duel_choices = self.contestants()
        self.duel_a = self.agent_dropdown(self.duel_choices)
        self.duel_b = self.agent_dropdown(self.duel_choices)
        self.duel_b.set_selected(1 if len(self.duel_choices) > 1 else 0)
        row.add_css_class("am-chess-controls")
        row.append(self.duel_a)
        row.append(Gtk.Label(label=_("against")))
        row.append(self.duel_b)
        self.duel_blind = Gtk.CheckButton(label=_("Blind"), active=True)
        row.append(self.duel_blind)
        box.append(row)
        box.append(self.action_button(_("Ask"), self.duel_ask, _("Both get the same question")))
        self.duel_sides = Gtk.Box(spacing=ai_core.GAP, homogeneous=True)
        self.duel_cards = []
        for _side in range(2):
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.START)
            card.add_css_class("am-help-card")
            title = Gtk.Label(xalign=0)
            title.add_css_class("am-setting-heading")
            card.append(title)
            text = self.answer_label()
            card.append(text)
            self.duel_sides.append(card)
            self.duel_cards.append((title, text))
        self.duel_sides.set_visible(False)
        box.append(self.duel_sides)
        self.duel_votes = Gtk.Box(spacing=8, visible=False)
        for label, winner in ((_("Left is better"), 0), (_("A tie"), None), (_("Right is better"), 1)):
            self.duel_votes.append(self.small_button(label, lambda w=winner: self.duel_vote(w)))
        box.append(self.duel_votes)
        self.duel_score = Gtk.Label(xalign=0, wrap=True)
        self.duel_score.add_css_class("am-hint")
        box.append(self.duel_score)
        self.duel_run = 0
        self.duel_pair = None
        return self.page_scroller(box)

    def duel_fill(self):
        """Local models found: both choices list them too; what was chosen stays chosen."""
        chosen = [self.duel_choices[d.get_selected()].id if d.get_selected() < len(self.duel_choices) else ""
                  for d in (self.duel_a, self.duel_b)]
        self.duel_choices = self.contestants()
        ids = [c.id for c in self.duel_choices]
        names = Gtk.StringList.new([c.name for c in self.duel_choices] or [_("No agent is set up")])
        for drop, was, fallback in ((self.duel_a, chosen[0], 0), (self.duel_b, chosen[1], 1)):
            drop.set_model(names)
            drop.set_selected(ids.index(was) if was in ids else min(fallback, max(0, len(ids) - 1)))

    def duel_ask(self):
        agents = self.duel_choices
        prompt = self.view_text(self.duel_prompt)
        if not agents or not prompt:
            return
        a, b = agents[self.duel_a.get_selected()], agents[self.duel_b.get_selected()]
        self.gpu_guard(self.agents_wanted([a, b]), lambda one_by_one: self.duel_start(a, b, prompt, one_by_one))

    def duel_start(self, a, b, prompt: str, one_by_one: bool):
        pair = [a, b]
        if self.duel_blind.get_active():
            random.shuffle(pair)
        self.duel_pair = pair
        self.duel_run += 1
        run = self.duel_run
        self.duel_answers = [None, None]
        self.duel_sides.set_visible(True)
        self.duel_votes.set_visible(False)
        self.duel_score.set_label("")
        jobs = []
        for side, agent in enumerate(pair):
            title, text = self.duel_cards[side]
            title.set_label(self.duel_name(side).upper())
            text.set_label(_("asking…"))

            def work(side=side, agent=agent):
                result = self.ask_contestant(agent, prompt)
                GLib.idle_add(self.duel_answer, run, side, result)
                if one_by_one:
                    self.make_room(agent)
            jobs.append(work)
        if one_by_one:
            threading.Thread(target=lambda: [job() for job in jobs if run == self.duel_run], daemon=True).start()
        else:
            for job in jobs:
                threading.Thread(target=job, daemon=True).start()

    def duel_name(self, side: int, reveal: bool = False) -> str:
        if self.duel_blind.get_active() and not reveal:
            return _("Left") if side == 0 else _("Right")
        return self.duel_pair[side].name

    def duel_answer(self, run, side, result):
        if run != self.duel_run:
            return False
        self.duel_answers[side] = result
        title, text = self.duel_cards[side]
        text.set_label(result.text or result.error)
        if not self.duel_blind.get_active():
            title.set_label(f"{self.duel_name(side)} · {result_line(result)}".upper())
        if all(r is not None for r in self.duel_answers):
            self.duel_votes.set_visible(True)
        return False

    def duel_vote(self, winner):
        """The names (and times) show; the score of this pair goes up."""
        pair = self.duel_pair
        self.duel_votes.set_visible(False)
        for side in (0, 1):
            title, _text = self.duel_cards[side]
            title.set_label(f"{self.duel_name(side, True)} · {result_line(self.duel_answers[side])}".upper())
        first, second = sorted((pair[0].id, pair[1].id))
        if first == second:
            self.duel_score.set_label(_("The same one twice: no score is kept."))
            return
        scores = ai_core.setting("duel_scores") or {}
        key = f"{first}|{second}"
        score = scores.get(key) if isinstance(scores.get(key), list) and len(scores.get(key)) == 3 else [0, 0, 0]
        if winner is None:
            score[1] += 1
        else:
            score[0 if pair[winner].id == first else 2] += 1
        self.save_setting(duel_scores={**scores, key: score})
        names = {a.id: a.name for a in pair}
        self.duel_score.set_label(_("Score: {0} {1} · ties {2} · {3} {4}").format(
            names[first], score[0], score[1], names[second], score[2]))


class VisionTab(AgentChecks):
    def build_vision_tab(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=ai_core.GAP)
        self.tab_intro(box, _("Vision"),
                       _("A picture, a screenshot or frames of a video for the agents that can see "
                         "(Claude Code, Codex, Pi). Pictures are scaled down and saved again first, so no "
                         "photo metadata goes out."))
        self.vision_agents = [c for c in self.contestants(with_models=False) if agent_run.takes_images(c.command)]
        row = Gtk.Box(spacing=8)
        row.append(self.small_button(_("Picture…"), self.vision_pick_picture))
        if shutil.which("grim"):
            row.append(self.small_button(_("Screenshot in 3 s"), self.vision_screenshot,
                                         _("The whole screen, three seconds after the click")))
        if shutil.which("ffmpeg") and shutil.which("ffprobe"):
            row.append(self.small_button(_("Video…"), self.vision_pick_video,
                                         _("Four frames spread over the video")))
        self.vision_clear_button = self.small_button(_("Clear"), self.vision_clear,
                                                     _("Remove the pictures (the answers stay)"))
        self.vision_clear_button.set_sensitive(False)
        row.append(self.vision_clear_button)
        box.append(row)
        self.vision_previews = Gtk.Box(spacing=6)
        box.append(self.vision_previews)
        self.vision_prompt = self.prompt_view(VISION_PROMPT, 60)
        box.append(self.vision_prompt)
        checks, self.vision_checks = self.agent_checks(self.vision_agents)
        box.append(checks)
        box.append(self.action_button(_("Ask"), self.vision_ask, _("Every ticked agent gets the pictures and "
                                                                    "the question")))
        self.vision_results = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=ai_core.GAP)
        box.append(self.vision_results)
        self.vision_folder = tempfile.mkdtemp(prefix="ai-playground-pictures-")   # private (0700)
        self.vision_images = []
        return self.page_scroller(box)

    def vision_cleanup(self):
        shutil.rmtree(getattr(self, "vision_folder", ""), ignore_errors=True)

    def vision_clear(self):
        for path in self.vision_images:
            try:
                os.unlink(path)
            except OSError:
                pass
        self.vision_images = []
        while (child := self.vision_previews.get_first_child()) is not None:
            self.vision_previews.remove(child)
        self.vision_clear_button.set_sensitive(False)

    def vision_prepare(self, source: str, name: str) -> str:
        """Off the main thread: the picture scaled to PICTURE_SIDE and saved as JPEG in the private
        folder (no metadata goes out); "" when it cannot be read or its header declares a picture
        too large to decode (picture_check: checked before anything decodes it)."""
        from gi.repository import GdkPixbuf
        if not picture_check.acceptable(source):
            return ""
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(source, PICTURE_SIDE, PICTURE_SIDE, True)
            path = os.path.join(self.vision_folder, name + ".jpg")
            pixbuf.savev(path, "jpeg", ["quality"], ["90"])
        except GLib.Error:
            return ""
        return path

    def vision_load(self, sources: list, failed: str, prompt: str, remove: bool = False):
        """[(file, name)] prepared in a thread, then shown; `failed` is toasted when none could be
        read. `remove`: the files are our own temporary ones (screenshot, frames) and go after."""
        def work():
            done = [p for p in (self.vision_prepare(source, name) for source, name in sources) if p]
            if remove:
                for source, _name in sources:
                    try:
                        os.unlink(source)
                    except OSError:
                        pass
            GLib.idle_add(show, done)

        def show(paths):
            for path in paths:
                self.vision_show(path)
            if not paths:
                self.toast(failed)
            self.vision_set_prompt(prompt)
            return False
        threading.Thread(target=work, daemon=True).start()

    def vision_show(self, path: str):
        """One of our own prepared pictures (JPEG, at most PICTURE_SIDE) among the previews."""
        self.vision_images.append(path)
        picture = Gtk.Picture.new_for_filename(path)
        picture.set_size_request(160, 100)
        picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.vision_previews.append(picture)
        self.vision_clear_button.set_sensitive(True)

    def vision_dialog(self, title: str, mime_types: list, on_file):
        dialog = Gtk.FileDialog(title=title)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        wanted = Gtk.FileFilter()
        for mime in mime_types:
            wanted.add_mime_type(mime)
        filters.append(wanted)
        dialog.set_filters(filters)

        def picked(d, result):
            try:
                file = d.open_finish(result)
            except GLib.Error:
                return
            if file is not None and file.get_path():
                on_file(file.get_path())
        dialog.open(self.window, None, picked)

    def vision_pick_picture(self):
        def chosen(path):
            self.vision_clear()
            self.vision_load([(path, "picture")], _("That picture cannot be read."), VISION_PROMPT)
        self.vision_dialog(_("A picture"), ["image/png", "image/jpeg", "image/webp", "image/gif"], chosen)

    def vision_screenshot(self):
        def shoot():
            raw = os.path.join(self.vision_folder, "screen.png")
            self.vision_clear()

            def grab():
                ai_core.read_bounded_output(["grim", raw], timeout=10)
            thread = threading.Thread(target=grab, daemon=True)
            thread.start()

            def wait():
                if thread.is_alive():
                    return True
                self.vision_load([(raw, "screenshot")], _("The screenshot did not work."), VISION_PROMPT,
                                 remove=True)
                return False
            GLib.timeout_add(100, wait)
            return False
        GLib.timeout_add(3000, shoot)

    def vision_pick_video(self):
        def chosen(path):
            self.vision_clear()
            self.toast(_("Taking frames from the video…"))

            def work():
                frames = video_frames(path, self.vision_folder)
                GLib.idle_add(done, frames)

            def done(frames):
                self.vision_load([(frame, f"frame-{i + 1}") for i, frame in enumerate(frames)],
                                 _("No frames could be taken from that video."), VIDEO_PROMPT, remove=True)
                return False
            threading.Thread(target=work, daemon=True).start()
        self.vision_dialog(_("A video"), ["video/*"], chosen)

    def vision_set_prompt(self, text: str):
        if self.view_text(self.vision_prompt) in (VISION_PROMPT, VIDEO_PROMPT, ""):
            self.vision_prompt.get_buffer().set_text(text)

    def vision_ask(self):
        if not self.vision_images:
            self.toast(_("Pick a picture, a screenshot or a video first."))
            return
        self.run_agents(self.vision_agents, self.vision_checks, self.view_text(self.vision_prompt),
                        self.vision_results, images=list(self.vision_images), counter="vision_run")


def video_frames(path: str, folder: str, count: int = VIDEO_FRAMES) -> list:
    """count frames spread over the video, as PNG files in folder (ffprobe, ffmpeg; bounded)."""
    import ai_core as core
    import chess_video
    if not os.path.isabs(path):
        return []
    duration = chess_video.duration(path)          # local file, real video container, memory limit
    if duration <= 0:
        return []
    frames = []
    for i in range(count):
        at = duration * (i + 0.5) / count
        target = os.path.join(folder, f"video-{i + 1}.png")
        argv = chess_video.media_argv("ffmpeg", ["-nostdin", "-v", "error", "-y", "-ss", f"{at:.2f}"], path,
                                      ["-frames:v", "1", "-vf", f"scale='min({PICTURE_SIDE},iw)':-2", target])
        _out, ok = core.read_bounded_output(argv, max_bytes=4096, timeout=30)
        if ok and Path(target).is_file():
            frames.append(target)
    return frames


class LocalTab(GpuGuard):
    def build_local_tab(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=ai_core.GAP)
        self.tab_intro(box, _("Local LLMs"),
                       _("The local LLM servers running on this computer (llama.cpp, Ollama, LM Studio, vLLM, Jan, "
                         "KoboldCpp): one question to a model, with its time and tokens per second. Only servers "
                         "that run under your own account are asked."))
        row = Gtk.Box(spacing=8)
        row.add_css_class("am-chess-controls")
        self.local_model = Gtk.DropDown.new_from_strings([_("Looking…")])
        row.append(self.local_model)
        row.append(Gtk.Label(label=_("compared with")))
        self.local_model_b = Gtk.DropDown.new_from_strings([_("no second model")])
        self.local_model_b.set_tooltip_text(_("A second model gets the same question; if both may not fit in GPU "
                                              "memory together, you are asked first"))
        row.append(self.local_model_b)
        row.append(self.small_button(_("Look again"), self.local_scan))
        box.append(row)
        self.local_prompt = self.prompt_view(LOCAL_PROMPT, 60)
        box.append(self.local_prompt)
        self.local_ask_button = self.action_button(_("Ask"), self.local_ask)
        box.append(self.local_ask_button)
        self.local_result = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=ai_core.GAP)
        box.append(self.local_result)
        self.local_choices = []
        self.local_run = 0
        self.local_scan()
        return self.page_scroller(box)

    def local_scan(self):
        self.local_model.set_model(Gtk.StringList.new([_("Looking…")]))
        if getattr(self, "local_ask_button", None) is not None:
            self.local_ask_button.set_sensitive(False)

        def work():
            servers = local_llm.running()
            GLib.idle_add(done, servers)

        def done(servers):
            self.local_choices = [(s, m) for s in servers if not s.error for m in s.models]
            labels = [f"{s.name} · {m}" for s, m in self.local_choices]
            notes = [f"{s.name}: " + (_("runs under another account; not asked") if s.error == "another account"
                                      else s.error) for s in servers if s.error]
            self.local_model.set_model(Gtk.StringList.new(labels or [_("No local LLM server is running")]))
            self.local_model_b.set_model(Gtk.StringList.new([_("no second model")] + labels))
            self.local_ask_button.set_sensitive(bool(self.local_choices))   # the boxes stay readable
            self.contestants_changed()
            if notes:
                self.toast(" · ".join(notes))
            return False
        threading.Thread(target=work, daemon=True).start()

    def local_ask(self):
        prompt = self.view_text(self.local_prompt)
        first, second = self.local_model.get_selected(), self.local_model_b.get_selected() - 1
        if not (0 <= first < len(self.local_choices)) or not prompt:
            return
        picked = [self.local_choices[first]]
        if 0 <= second < len(self.local_choices) and second != first:
            picked.append(self.local_choices[second])
        wanted = [(f"{server.name}: {model}", server.key, model) for server, model in picked]
        self.gpu_guard(lambda: wanted, lambda one_by_one: self.local_start(picked, prompt, one_by_one))

    def local_start(self, picked: list, prompt: str, one_by_one: bool):
        self.local_run += 1
        run = self.local_run
        while (child := self.local_result.get_first_child()) is not None:
            self.local_result.remove(child)
        jobs = []
        for server, model in picked:
            card = self.group_card(self.local_result, f"local-{model}", f"{server.name} · {model}",
                                   summary=_("asking…"), default_open=True)
            card.group_summary.set_visible(True)
            text = self.answer_label(_("asking… (a model that is not loaded yet loads first)"))
            card.append(text)

            def work(server=server, model=model, card=card, text=text):
                answer = local_llm.ask(server.port, model, prompt)
                GLib.idle_add(done, card, text, answer)
                if one_by_one:
                    gpu_check.unload(server.key, model)       # room for the next one (Ollama)
            jobs.append(work)

        def done(card, text, answer):
            if run != self.local_run:
                return False
            parts = [_fmt_seconds(answer.seconds)]
            if answer.tokens_out is not None:
                parts.append(_("{0} tokens").format(answer.tokens_out))
            if answer.per_second is not None:
                parts.append(_("{0} tokens per second").format(f"{answer.per_second:.1f}"))
            card.group_summary.set_label(answer.error or " · ".join(parts))
            text.set_label(answer.text or answer.error)
            return False
        if one_by_one:
            threading.Thread(target=lambda: [job() for job in jobs if run == self.local_run], daemon=True).start()
        else:
            for job in jobs:
                threading.Thread(target=job, daemon=True).start()
