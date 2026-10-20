"""AI Playground's chess tab: games read from a video, kept and replayed (a mixin of
ai_playground.Playground; the reading itself is chess_video).

Drop a video file or a YouTube link anywhere on the chess tab (a line at its top says so; the tab
is framed while something is dragged over it), or Import video…: a dialog shows the length, how
many frames there will be at most and which agent reads them; then a card shows the progress (Cancel
stops after the frame in work). The game is saved under the video's title, each saved game in a
card of its own under the board: its moves, Replay and Delete. Replay shows controls beneath the
board: first, back, next, last move, Auto (Pause while it plays) and Play from here, which goes on
against the opponent from the move in view.
"""

from __future__ import annotations

import shutil
import tempfile
import threading
from urllib.parse import unquote, urlparse

from gi.repository import Gdk, Gio, GLib, Gtk, Pango

import agent_run
import ai_core
import chess_rules as rules
import chess_video
import gpu_check
from i18n import _, ngettext

DROP_MIMES = ("text/uri-list", "text/plain;charset=utf-8", "text/plain")
MAX_DROP_TEXT = 64 * 1024        # bytes of a dropped or pasted link (or file:// list) read at most
DROP_SECONDS = 10

REPLAY_STEP_MS = 1200        # Play: one move every so often
ASK_AT_ONCE = 3              # frames asked at the same time (one at a time when the reader uses a local model)
ON_CARD = 3                  # saved games on the chess tab's card (the page has them all)
SEARCH_FROM = 6              # games from which on a search field shows
NEWEST = 10                  # games shown before Show all


class ChessImport:
    # --- building -----------------------------------------------------------------------------------

    def build_chess_import(self, row: Gtk.Box, drop_on: Gtk.Box):
        """Import video… in the opponent row, a line under it that says what can be dropped, the
        drop target on the whole tab, the replay controls beneath the board, and the saved games
        under the game."""
        row.append(self.small_button(_("Import video…"), self.chess_pick_video,
                                     _("A chess video (a file, or a YouTube link dropped here): an agent that "
                                       "sees reads the moves, and the game is saved to replay")))
        hint = Gtk.Box(spacing=8)
        hint.add_css_class("am-drop-hint")
        hint.append(Gtk.Image.new_from_icon_name("document-send-symbolic"))
        text = Gtk.Label(label=_("Drop a chess video here, a file or a YouTube link, anywhere on this tab, or copy "
                                 "a YouTube link and press Ctrl+V: an agent that sees reads its moves and the game is "
                                 "saved below to replay."),
                         xalign=0, wrap=True, hexpand=True)
        hint.append(text)
        drop_on.insert_child_after(hint, row)
        self.chess_drop_hint = hint
        # While a video is read: in place of the hint, always in view (the board may fill the window).
        bar = Gtk.Box(spacing=10, visible=False)
        bar.add_css_class("am-import-bar")
        self.chess_import_spinner = Gtk.Spinner()
        bar.append(self.chess_import_spinner)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        self.chess_import_title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.chess_import_title.add_css_class("am-chess-status")
        texts.append(self.chess_import_title)
        self.chess_import_status = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.chess_import_status.add_css_class("am-chess-said")
        texts.append(self.chess_import_status)
        self.chess_import_progress = Gtk.ProgressBar()
        texts.append(self.chess_import_progress)
        bar.append(texts)
        self.chess_import_stop = self.small_button(_("Cancel"), self.chess_import_cancel_now)
        self.chess_import_stop.set_valign(Gtk.Align.CENTER)
        bar.append(self.chess_import_stop)
        drop_on.insert_child_after(bar, hint)
        self.chess_import_bar = bar
        # Copy, link or move: browsers offer a dragged link as a link (a copy-only target refuses it).
        # The drop is read by us as text (a link, or file:// addresses from the file manager), with a
        # byte cap and a deadline: GTK's own reading of a drop has neither (chess_drop_read).
        drop = Gtk.DropTargetAsync.new(None, Gdk.DragAction.COPY | Gdk.DragAction.LINK | Gdk.DragAction.MOVE)
        drop.connect("accept", self.chess_drop_accept)
        drop.connect("drop", lambda _t, d, *_a: (drop_on.remove_css_class("am-drop-active"), self.chess_drop_read(d))[1])
        drop.connect("drag-enter", lambda _t, d, *_a: (drop_on.add_css_class("am-drop-active"),
                                                       self.chess_drop_action(d))[1])
        drop.connect("drag-motion", lambda _t, d, *_a: self.chess_drop_action(d))
        drop.connect("drag-leave", lambda *_a: drop_on.remove_css_class("am-drop-active"))
        drop_on.add_controller(drop)
        drop_on.add_css_class("am-drop-zone")

        # Beneath the board, while a saved game is replayed.
        self.chess_replay_row = Gtk.Box(spacing=4, visible=False, halign=Gtk.Align.CENTER)
        for icon, tip, step in (("go-first-symbolic", _("First move"), "first"),
                                ("go-previous-symbolic", _("Back one move"), "back"),
                                ("go-next-symbolic", _("Next move"), "next"),
                                ("go-last-symbolic", _("Last move"), "last")):
            button = Gtk.Button.new_from_icon_name(icon)
            button.add_css_class("am-small-btn")
            button.set_tooltip_text(tip)
            button.connect("clicked", lambda _b, s=step: self.chess_replay_step(s))
            self.chess_replay_row.append(button)
        self.chess_replay_play = Gtk.ToggleButton()
        self.chess_replay_play.add_css_class("am-small-btn")
        self.chess_replay_play.connect("toggled", self.chess_replay_toggled)
        self.chess_replay_toggled(self.chess_replay_play)
        self.chess_replay_row.append(self.chess_replay_play)
        self.chess_replay_row.append(self.small_button(_("Play from here"), self.chess_play_from_here,
                                                       _("Go on from the move in view against the opponent; you "
                                                         "play the side to move")))
        self.chess_column.append(self.chess_replay_row)
        # Your games and agent against agent: Save game in the Game card (a finished game is saved by
        # itself; saving again updates the same entry).
        self.chess_save_button = self.small_button(_("Save game"), self.chess_save_current,
                                                   _("Keep this game with the saved games (with who played and "
                                                     "the result); a finished game is kept by itself"))
        self.chess_save_button.set_visible(False)
        self.chess_info.append(self.chess_save_button)

        # Under the game: the newest saved games; the symbol in its heading opens the Saved games page.
        manage = Gtk.Button.new_from_icon_name("view-list-bullet-symbolic")
        manage.add_css_class("flat")
        manage.add_css_class("am-header-btn")
        manage.add_css_class("am-gear-btn")
        manage.set_tooltip_text(_("Manage saved games: search, rename, save as PGN, delete"))
        manage.connect("clicked", lambda *_a: self.show_page("games"))
        self.chess_games_card = self.group_card(self.chess_body, "chess-games", _("Saved games"),
                                                icon="document-open-recent-symbolic", default_open=True, action=manage)
        self.chess_import_card = None              # the running import (its state), None when there is none
        self.chess_replay = None
        self.chess_fill_games()

    def chess_import_busy(self, busy: bool):
        """The progress bar in place of the drop hint, and a spinner on the Chess tab button (seen from
        the other tabs too)."""
        self.chess_import_bar.set_visible(busy)
        self.chess_drop_hint.set_visible(not busy)
        if busy:
            self.chess_import_spinner.start()
        else:
            self.chess_import_spinner.stop()
        button = getattr(self, "tab_buttons", {}).get("chess")
        if button is not None:
            content = Gtk.Box(spacing=6)
            content.append(Gtk.Label(label=_("Chess")))
            if busy:
                spinner = Gtk.Spinner()
                spinner.start()
                content.append(spinner)
            button.set_child(content)
            button.set_tooltip_text(_("Reading a video…") if busy else "Ctrl+1")

    def chess_import_cancel_now(self):
        if self.chess_import_card is not None:
            self.chess_import_card["cancel"] = True
            self.chess_import_status.set_label(_("Stopping after this frame…"))
            self.chess_import_stop.set_sensitive(False)

    def build_games_page(self) -> Gtk.Widget:
        """The Saved games page (from the symbol on the Saved games card): every game, newest first,
        a search from SEARCH_FROM games on, and per game Replay, Rename…, Save as PGN… and Delete."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=ai_core.GAP)
        self.page_head(box, _("Saved games"))
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=ai_core.GAP)
        intro = Gtk.Label(label=_("Your saved games and the ones read from chess videos, newest first. The play "
                                  "symbol puts one on the board; Save as PGN gives it to other chess programs."),
                          xalign=0, wrap=True)
        intro.add_css_class("am-hint")
        body.append(intro)
        self.chess_games_search = Gtk.SearchEntry(placeholder_text=_("Search saved games"), visible=False)
        self.chess_games_search.connect("search-changed", lambda *_a: self.chess_fill_games())
        body.append(self.chess_games_search)
        self.chess_games_all = False                     # Show all: every game, not only the newest
        self.chess_games_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=ai_core.GAP)
        body.append(self.chess_games_box)
        box.append(self.page_scroller(body))
        self.chess_fill_games()
        return box

    def chess_replay_toggled(self, button):
        """Auto plays one move after another; while it plays, the button says Pause."""
        playing = button.get_active()
        content = Gtk.Box(spacing=6)
        content.append(Gtk.Image.new_from_icon_name("media-playback-pause-symbolic" if playing
                                                    else "media-playback-start-symbolic"))
        content.append(Gtk.Label(label=_("Pause") if playing else _("Auto")))
        button.set_child(content)
        button.set_tooltip_text(_("Stop at this move") if playing else _("Play the moves one after another"))
        if playing:
            self.chess_replay_auto()

    # --- importing -----------------------------------------------------------------------------------

    def chess_drop_accept(self, target, drop) -> bool:
        """Text: a link, or file:// addresses (files from the file manager). Chrome offers a dragged
        link as a link only (not a copy): the tab takes copy, link and move."""
        if drop.get_drag() is not None:
            return False                               # a piece dragged inside the window: the board's
        return bool(set(drop.get_formats().get_mime_types() or ()) & set(DROP_MIMES))

    def chess_drop_action(self, drop) -> Gdk.DragAction:
        """Copy when the source allows it, else link, else move."""
        actions = drop.get_actions() if drop is not None else Gdk.DragAction.COPY
        for action in (Gdk.DragAction.COPY, Gdk.DragAction.LINK, Gdk.DragAction.MOVE):
            if actions & action:
                return action
        return Gdk.DragAction.COPY

    def chess_drop_read(self, drop) -> bool:
        """Read the dropped text ourselves: at most MAX_DROP_TEXT bytes within DROP_SECONDS, then
        chess_dropped; past either limit the drop is refused."""
        offered = [m for m in DROP_MIMES if m in (drop.get_formats().get_mime_types() or ())]
        if not offered:
            return False
        cancel = Gio.Cancellable()
        data = bytearray()
        timer = GLib.timeout_add_seconds(DROP_SECONDS, lambda: (cancel.cancel(), False)[1])

        def finish(text):
            if not cancel.is_cancelled():
                GLib.source_remove(timer)
            action = self.chess_drop_action(drop) if text else Gdk.DragAction(0)
            drop.finish(action)
            if text:
                self.chess_dropped(None, text, 0, 0)
            elif text is None:
                self.toast(_("That drop was too large or too slow to read."))
            return False

        def opened(source, result):
            try:
                stream, _mime = source.read_finish(result)
            except GLib.Error:
                finish(None)
                return
            stream.read_bytes_async(16384, GLib.PRIORITY_DEFAULT, cancel, chunk, stream)

        def chunk(stream, result, _s):
            try:
                block = stream.read_bytes_finish(result)
            except GLib.Error:
                stream.close_async(GLib.PRIORITY_DEFAULT, None, None)
                finish(None)
                return
            size = block.get_size()
            if size:
                data.extend(block.get_data())
                if len(data) > MAX_DROP_TEXT:
                    cancel.cancel()
                    stream.close_async(GLib.PRIORITY_DEFAULT, None, None)
                    finish(None)
                    return
                stream.read_bytes_async(16384, GLib.PRIORITY_DEFAULT, cancel, chunk, stream)
                return
            stream.close_async(GLib.PRIORITY_DEFAULT, None, None)
            finish(bytes(data).decode("utf-8", "replace"))

        drop.read_async(offered, GLib.PRIORITY_DEFAULT, cancel, opened)
        return True

    def chess_dropped(self, _target, value, _x, _y) -> bool:
        """A file (its path) or a link (YouTube) dropped on the chess tab."""
        if isinstance(value, Gdk.FileList):
            files = value.get_files()
            if not files:
                return False
            path, uri = files[0].get_path(), files[0].get_uri() or ""
            if path:
                return self.chess_import_ask(path=path)
            return self.chess_import_ask(url=chess_video.youtube_url(uri))
        text = str(value or "")
        url = chess_video.youtube_url(text)
        lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
        if not url and lines and lines[0].startswith("file://"):
            return self.chess_import_ask(path=unquote(urlparse(lines[0]).path))
        return self.chess_import_ask(url=url)

    def chess_paste(self):
        """Ctrl+V on the chess tab: a YouTube link (or a video file) from the clipboard, as if dropped."""
        def worker():
            # wl-paste with a byte cap and a deadline (GTK's own read has neither)
            GLib.idle_add(read, ai_core.clipboard_text(max_bytes=MAX_DROP_TEXT, lines=True) or "")

        def read(text):
            if chess_video.youtube_url(text) or text.strip().startswith(("file://", "/")):
                self.chess_dropped(None, "file://" + text.strip() if text.strip().startswith("/") else text, 0, 0)
            else:
                self.toast(_("Copy a YouTube link first, then press Ctrl+V on the chess tab."))
            return False
        threading.Thread(target=worker, daemon=True).start()

    def chess_pick_video(self):
        dialog = Gtk.FileDialog(title=_("A chess video"))
        filters = Gio.ListStore.new(Gtk.FileFilter)
        wanted = Gtk.FileFilter()
        wanted.add_mime_type("video/*")
        filters.append(wanted)
        dialog.set_filters(filters)

        def picked(d, result):
            try:
                file = d.open_finish(result)
            except GLib.Error:
                return
            if file is not None and file.get_path():
                self.chess_import_ask(path=file.get_path())
        dialog.open(self.window, None, picked)

    def chess_readers(self) -> list:
        return [a for a in self.ready_agents if agent_run.takes_images(a.command)]

    def chess_import_ask(self, path: str = "", url: str = "") -> bool:
        """The import dialog: which agent reads, how often a frame is taken, what it costs."""
        from gi.repository import Adw
        if self.chess_import_card is not None:
            self.toast(_("A video is being read already."))
            return False
        if path and not chess_video.video_file(path):
            self.toast(_("That is not a video file (MP4, MKV, WebM, MOV, AVI)."))
            return False
        if not path and not url:
            self.toast(_("Drop a video file or a YouTube link."))
            return False
        if url and shutil.which("yt-dlp") is None:
            self.toast(_("A YouTube link needs yt-dlp; install it, or drop the video file."))
            return False
        seeing = self.chess_readers()                  # agents that take pictures: the frames
        everyone = list(self.ready_agents)             # any agent set up: the fast way (text only)
        if not (everyone if url else seeing):
            blind = [a.name for a in self.ready_agents]
            self.chess_notice(_("No agent here can read a video"),
                              _("Reading the moves needs an agent that sees pictures: Claude Code, Codex or Pi, "
                                "signed in or connected to a local vision model.") + "\n\n"
                              + (_("Set up here: {0}. They cannot see pictures.").format(", ".join(blind)) if blind
                                 else _("No agent is set up here yet.")) + "\n\n"
                              + _("Set up one of them (AI Agents Map can install it and sign you in), then drop the "
                                  "video again."))
            return False
        seconds = chess_video.duration(path) if path else 0.0
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        reader = Gtk.DropDown()
        readers = []                                   # the agents in `reader` now (they follow the way)
        opponent = self.chess_agent
        every = Gtk.DropDown.new_from_strings([_("A frame every {0} s").format(s) for s in chess_video.INTERVALS])
        every.set_selected(1)
        part = Gtk.DropDown.new_from_strings([_("The whole video")] + [_("The first {0} minutes").format(m)
                                                                       for m in chess_video.LIMITS[1:]])
        part.set_tooltip_text(_("Long videos: only their start is read (faster, fewer questions)"))
        # YouTube: the fast way (description and commentary, no download) or watching the frames.
        way = Gtk.DropDown.new_from_strings([_("Fast: the description and the commentary"),
                                             _("Watch the board, frame by frame (slower)")])
        way.set_tooltip_text(_("Fast: no download; the moves come from the video's description (often a PGN) or "
                               "from what is said, in seconds. If no game is found there, the frames are watched."))
        way.set_visible(bool(url))
        for widget in (way, reader, every, part):
            box.append(widget)
        note = Gtk.Label(xalign=0, wrap=True, max_width_chars=48)

        def describe(*_a):
            gap = chess_video.INTERVALS[every.get_selected()]
            limit = chess_video.LIMITS[part.get_selected()] * 60
            if seconds:
                most = min(chess_video.MAX_FRAMES, int(min(seconds, limit or seconds) // gap) + 1)
                length = f"{seconds:.0f} s" if seconds < 90 else f"{seconds / 60:.0f} min"
                note.set_label(_("{0} of video: at most {1} frames. Only frames where the picture changed are "
                                 "sent, each one question to the agent (it may count against your plan).").format(
                    length, most))
            elif way.get_selected() == 0:
                note.set_label(_("No download: the moves come from the description, or from one question to the agent "
                                 "about the description and what is said. Only if no game is found there, the video "
                                 "is downloaded and its frames are watched."))
            else:
                note.set_label(_("The video is downloaded first (at most 480p, 1 GB). Only frames where the picture "
                                 "changed are sent, each one question to the agent (it may count against your "
                                 "plan)."))
        every.connect("notify::selected", describe)
        way.connect("notify::selected", describe)
        part.connect("notify::selected", describe)
        describe()
        box.append(note)
        # Who cannot read, and a reader on a local model for the frames.
        other = Gtk.Label(xalign=0, wrap=True, max_width_chars=48)
        other.add_css_class("am-chess-said")
        box.append(other)
        local = Gtk.Label(xalign=0, wrap=True, max_width_chars=48)
        local.add_css_class("am-warning-text")
        box.append(local)

        def watching() -> bool:
            return not url or way.get_selected() == 1

        def check_reader(*_a):
            index = reader.get_selected()
            agent = readers[index] if 0 <= index < len(readers) else None
            used = gpu_check.agent_model(agent.command) if agent is not None and watching() else None
            local.set_label(_("{0} uses the local model {1}. Local models misread busy chess positions in tests "
                              "(Claude Code and Codex read them right); the fast way does not need pictures.").format(
                agent.name, used[1]) if used else "")
            local.set_visible(bool(used))

        def fill_readers(*_a):
            """The fast way: every agent set up; the frames: the agents that see pictures."""
            readers[:] = seeing if watching() else everyone
            reader.set_model(Gtk.StringList.new([a.name for a in readers] or [_("No agent sees pictures")]))
            commands = [a.command for a in readers]
            if opponent is not None and opponent.command in commands:
                reader.set_selected(commands.index(opponent.command))   # the opponent reads, when it can
            blind = opponent is not None and opponent.command not in commands
            other.set_label(_("{0}, your opponent, cannot see pictures: the agent chosen here reads the "
                              "video.").format(opponent.name) if blind else "")
            other.set_visible(blind)
            check_reader()
        reader.connect("notify::selected", check_reader)
        way.connect("notify::selected", fill_readers)
        fill_readers()
        # Read before (any link to the same video, or the same file): the new reading replaces it.
        before = chess_video.same_source(chess_video.load_games(), url or path)
        if before is not None:
            again = Gtk.Label(label=_("Read before: {0} ({1}); its game is replaced by the new reading.").format(
                before["title"], before.get("date", "")), xalign=0, wrap=True, max_width_chars=48)
            again.add_css_class("am-chess-said")
            box.append(again)
        dialog = Adw.AlertDialog(heading=_("Read the moves from this video?"),
                                 body=path.rsplit("/", 1)[-1] if path else url)              # plain text (no markup)
        dialog.set_extra_child(box)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("read", _("Read the moves"))
        dialog.set_response_appearance("read", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("read")
        dialog.set_close_response("cancel")
        def answered(_d, answer):
            index = reader.get_selected()
            if answer != "read" or not (0 <= index < len(readers)):
                return
            chosen = readers[index]
            # The frames, when the fast way finds no game: the chosen agent if it sees, else one that does.
            watcher = chosen if chosen in seeing else (seeing[0] if seeing else None)
            self.chess_import_start(path, url, chosen, chess_video.INTERVALS[every.get_selected()],
                                    before["id"] if before is not None else "",
                                    chess_video.LIMITS[part.get_selected()] * 60, not watching(), watcher)
        dialog.connect("response", answered)
        dialog.present(self.window)
        return True

    def chess_import_start(self, path: str, url: str, reader, gap: int, replace_id: str = "", limit: int = 0,
                           fast: bool = False, watcher=None):
        title = path.rsplit("/", 1)[-1].rsplit(".", 1)[0] if path else url
        state = {"cancel": False, "counting": False}
        self.chess_import_card = state
        self.chess_import_title.set_label(_("Reading a video: {0}").format(title))
        self.chess_import_status.set_label(_("Starting…"))
        self.chess_import_progress.set_fraction(0)
        self.chess_import_stop.set_sensitive(True)
        self.chess_import_busy(True)
        self.toast(_("Reading the moves from the video; the bar at the top of the chess tab shows how far."))

        def pulse():                                  # no count yet (download, frames): the bar moves
            if self.chess_import_card is not state:
                return False
            if not state["counting"]:
                self.chess_import_progress.pulse()
            return True
        GLib.timeout_add(200, pulse)

        def show(text: str, part: float | None = None):
            def apply():
                if self.chess_import_card is state:
                    if not state["cancel"]:
                        self.chess_import_status.set_label(text)
                    if part is not None:
                        state["counting"] = True
                        self.chess_import_progress.set_fraction(part)
                return False
            GLib.idle_add(apply)

        cancel = state                                 # work() reads cancel["cancel"]
        def work():
            folder = tempfile.mkdtemp(prefix="ai-playground-video-")       # private (0700)
            try:
                video, name = path, title
                eyes = reader                               # the agent that watches the frames
                if fast:
                    found = self.chess_read_text(url, reader, show)
                    if found is not None:
                        return GLib.idle_add(done, *found)
                    if watcher is None:
                        return GLib.idle_add(done, None, _("No game in the description or the commentary, and no "
                                                           "agent here sees pictures to watch the frames."))
                    eyes = watcher                          # an agent that sees pictures watches the frames
                    show(_("No game in the description or the commentary: watching the frames…"))
                if url:
                    show(_("Downloading the video…"))
                    video, name = chess_video.download(url, folder)
                    if not video:
                        return GLib.idle_add(done, None, _("The video could not be downloaded."))
                show(_("Taking frames…"))
                shots = chess_video.frames(video, folder, gap, limit)
                if not shots:
                    return GLib.idle_add(done, None, _("No frames could be taken from that video."))
                # Only frames where the picture changed are asked, ASK_AT_ONCE at a time; their
                # positions are followed in time order as the answers come in.
                picks, previous = [], b""
                for shot in shots:
                    thumb = chess_video.thumbnail(shot)
                    if chess_video.changed(previous, thumb):
                        previous = thumb
                        picks.append(shot)
                reading = chess_video.Reading()
                at_once = 1 if gpu_check.agent_model(eyes.command) else ASK_AT_ONCE

                def position(shot):
                    answer = agent_run.ask(eyes.command, chess_video.PROMPT, [shot], level="quick")
                    return chess_video.placement(answer.text)
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=at_once) as pool:
                    pending, sent, answered = [], 0, 0
                    while sent < len(picks) or pending:
                        while sent < len(picks) and len(pending) < at_once and not cancel["cancel"]:
                            pending.append(pool.submit(position, picks[sent]))
                            sent += 1
                        if not pending:
                            break
                        board = pending.pop(0).result()
                        answered += 1
                        if board is not None:
                            chess_video.follow(reading, board)
                        show(" · ".join((_("Frame {0} of {1}").format(answered, len(picks)),
                                         ngettext("{0} move read", "{0} moves read", len(reading.moves)).format(
                                             len(reading.moves)))), answered / max(1, len(picks)))
                GLib.idle_add(done, reading, name, "frames")
            finally:
                shutil.rmtree(folder, ignore_errors=True)

        def done(reading, name_or_error, how="frames"):
            self.chess_import_card = None
            self.chess_import_busy(False)
            if reading is None:
                self.toast(name_or_error)
                return False
            if not reading.moves:
                self.toast(_("No moves could be read from that video."))
                return False
            game = chess_video.game_record(name_or_error, url or path, reading)
            game["read"] = how
            kept = chess_video.without(chess_video.load_games(), replace_id)
            twin = chess_video.duplicate_of(kept, game)
            if twin is not None:                      # the same moves are saved already: no second entry
                self.chess_fill_games()
                self.toast(_("These moves are saved already: {0}").format(twin["title"]))
                self.chess_replay_open(twin)
                return False
            games = [game] + kept
            try:
                chess_video.save_games(games)
            except OSError:
                self.toast(_("The game could not be saved."))
            self.chess_fill_games()
            note = ngettext("Saved: {0}, {1} move", "Saved: {0}, {1} moves", len(reading.moves)).format(
                game["title"], len(reading.moves))
            note += " · " + {"description": _("read from the description"),
                             "commentary": _("read from the commentary"),
                             "frames": _("read from the frames")}[how]
            if reading.unmatched:
                note += " · " + ngettext("{0} frame did not fit", "{0} frames did not fit",
                                         reading.unmatched).format(reading.unmatched)
            self.toast(note)
            self.chess_replay_open(game)
            return False
        threading.Thread(target=work, daemon=True).start()

    def chess_read_text(self, url: str, reader, show):
        """The fast way (blocking): (reading, title, how) from the video's description, else from its
        description and captions through one question to the agent; None when they give no game."""
        show(_("Reading the video's description…"))
        info = chess_video.video_info(url)
        if not info:
            return None
        title = str(info.get("title") or url)[:200]
        description = str(info.get("description") or "")
        _start, moves = chess_video.moves_in_text(description)
        how = "description"
        if len(moves) < chess_video.MIN_TEXT_MOVES:
            show(_("Reading the commentary…"))
            spoken = chess_video.caption_text(info)
            text = (chess_video.TEXT_PROMPT + f"Title: {title}\n\nDescription:\n{description}\n\n"
                    f"Spoken:\n{spoken}")[:chess_video.MAX_TEXT + len(chess_video.TEXT_PROMPT)]
            answer = agent_run.ask(reader.command, text, level="quick")
            _start, said = chess_video.moves_in_text(answer.text)
            if len(said) > len(moves):
                moves, how = said, "commentary"
        if len(moves) < chess_video.MIN_TEXT_MOVES:
            return None
        return chess_video.reading_of(moves), title, how

    def chess_notice(self, heading: str, body: str):
        from gi.repository import Adw
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("ok", _("OK"))
        dialog.present(self.window)

    # --- saved games -----------------------------------------------------------------------------------

    def chess_fill_games(self):
        """The Saved games card on the chess tab and, once it is built, the Saved games page."""
        games = chess_video.load_games()
        self.chess_fill_card(games)
        if getattr(self, "chess_games_box", None) is not None:
            self.chess_fill_page(games)

    def chess_fill_card(self, games: list):
        """The newest ON_CARD games with Replay, then a link to all of them on the page."""
        card = self.chess_games_card
        while (child := card.get_first_child()) is not None:
            card.remove(child)
        card.group_summary.set_label(ngettext("{0} game", "{0} games", len(games)).format(len(games)))
        if not games:
            hint = Gtk.Label(label=_("None yet. Drop a chess video or a YouTube link on this tab, or use Import "
                                     "video…"), xalign=0, wrap=True)
            hint.add_css_class("am-hint")
            card.append(hint)
            return
        current = (self.chess_replay or {}).get("id") if self.chess_mode == "replay" else None
        for game in games[:ON_CARD]:
            line = Gtk.Box(spacing=8)
            title = Gtk.Label(label=game["title"], xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
            title.add_css_class("am-chess-said")
            if game["id"] == current:
                title.add_css_class("am-chess-status")             # the game on the board
            line.append(title)
            count = ngettext("{0} move", "{0} moves", len(game["moves"])).format(len(game["moves"]))
            info = Gtk.Label(label=count + (" · " + game["date"] if game.get("date") else ""))
            info.add_css_class("am-hint")
            line.append(info)
            if game.get("source"):
                line.append(self.chess_original_button(game))
            line.append(self.icon_button("media-playback-start-symbolic", lambda g=game: self.chess_replay_open(g),
                                         _("Replay game: on the board, with the controls beneath it")))
            card.append(line)
        if len(games) > ON_CARD:
            card.append(self.small_button(ngettext("All {0} game…", "All {0} games…", len(games)).format(len(games)),
                                          lambda: self.show_page("games")))

    def chess_fill_page(self, games: list):
        """A card per saved game on the Saved games page, newest first: its moves and where it came
        from, Replay, Rename…, Save as PGN… and Delete; the game on the board is open, the others
        folded. From SEARCH_FROM games a search over the titles; without a search the newest NEWEST,
        then Show all."""
        box = self.chess_games_box
        while (child := box.get_first_child()) is not None:
            box.remove(child)
        self.chess_games_search.set_visible(len(games) >= SEARCH_FROM)
        text = self.chess_games_search.get_text() if len(games) >= SEARCH_FROM else ""
        if not games:
            hint = Gtk.Label(label=_("None yet. Drop a chess video or a YouTube link on this tab, or use Import "
                                     "video…"), xalign=0, wrap=True)
            hint.add_css_class("am-hint")
            box.append(hint)
            return
        shown = chess_video.matching(games, text) if text else games
        if text and not shown:
            hint = Gtk.Label(label=_("No saved game has that in its title."), xalign=0)
            hint.add_css_class("am-hint")
            box.append(hint)
        more = 0 if text or self.chess_games_all else max(0, len(shown) - NEWEST)
        current = (self.chess_replay or {}).get("id") if self.chess_mode == "replay" else None
        for game in shown[:len(shown) - more]:
            start, moves = chess_video.replay(game)
            count = ngettext("{0} move", "{0} moves", len(moves)).format(len(moves))
            result = game.get("result") if game.get("result") not in (None, "*") else ""
            summary = count + (" · " + result if result else "") + (" · " + game["date"] if game.get("date") else "")
            icon = "applications-games-symbolic" if game.get("kind") == "played" else "video-x-generic-symbolic"
            card = self.group_card(box, f"chess-saved-{game['id']}", game["title"], icon=icon,
                                   summary=summary, default_open=game["id"] == current)
            info = Gtk.Label(label=summary + (" · " + game["source"] if game.get("source") else ""), xalign=0,
                             wrap=True, selectable=True)
            info.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            info.add_css_class("am-hint")
            card.append(info)
            if start is not None and moves:
                moves_text = Gtk.Label(label=rules.game_san(start, moves), xalign=0, wrap=True, selectable=True)
                moves_text.add_css_class("am-chess-said")
                card.append(moves_text)
            buttons = Gtk.Box(spacing=8)
            if game.get("source"):
                buttons.append(self.chess_original_button(game))
            buttons.append(self.icon_button("media-playback-start-symbolic", lambda g=game: self.chess_replay_open(g),
                                            _("Replay game: on the board, with the controls beneath it")))
            for label, action, tip in (
                    (_("Rename…"), lambda g=game: self.chess_rename_game(g), ""),
                    (_("Save as PGN…"), lambda g=game: self.chess_save_pgn(g),
                     _("PGN: the chess programs' format (Lichess, ChessBase, Scid…)"))):
                buttons.append(self.small_button(label, action, tip))
            buttons.append(self.icon_button("user-trash-symbolic", lambda g=game: self.chess_delete_game(g),
                                            _("Delete this game")))
            card.append(buttons)
        if more:
            box.append(self.small_button(ngettext("Show all {0} game", "Show all {0} games", len(shown)).format(len(shown)),
                                         lambda: (setattr(self, "chess_games_all", True), self.chess_fill_games())))

    def chess_original_button(self, game: dict) -> Gtk.Button:
        """The open-in-new symbol: the video the game was read from (YouTube in the browser, a file in
        the video player)."""
        import more_plugins
        button = Gtk.Button(valign=Gtk.Align.CENTER)
        mark = Gtk.Label(label=more_plugins.OPEN_GLYPH)
        mark.add_css_class("am-gear")
        button.set_child(mark)
        button.add_css_class("am-small-btn")
        button.set_tooltip_text(_("Open the original video: {0}").format(game["source"]))
        button.connect("clicked", lambda *_a: self.chess_open_original(game))
        return button

    def chess_open_original(self, game: dict):
        import os
        source = str(game.get("source") or "")
        url = chess_video.youtube_url(source)
        if url:
            target = url
        elif os.path.isabs(source) and os.path.isfile(source):
            target = Gio.File.new_for_path(source).get_uri()
        else:
            self.toast(_("The original video is not there any more: {0}").format(source))
            return
        try:
            Gio.AppInfo.launch_default_for_uri(target, None)
        except GLib.Error as error:
            self.toast(error.message)

    def chess_mark_save(self):
        button = getattr(self, "chess_save_button", None)
        if button is not None:
            button.set_visible(self.chess_mode == "game" and bool(self.chess_moves_played))
            button.set_label(_("Save again") if self.chess_saved_id else _("Save game"))

    def chess_player_name(self, side: str) -> str:
        agent = self.chess_agent_for(side) if self.chess_agent is not None else None
        return agent.name if agent is not None else _("You")

    def chess_save_current(self, auto: bool = False):
        """This game (yours, or agent against agent) with the saved games; the same entry again when
        it was saved before (its title as it may have been renamed)."""
        if self.chess_mode != "game" or not self.chess_moves_played:
            return
        white, black = self.chess_player_name("w"), self.chess_player_name("b")
        games = chess_video.load_games()
        before = next((g for g in games if g.get("id") == self.chess_saved_id), None) if self.chess_saved_id else None
        title = before["title"] if before else f"{white} – {black}"
        game = chess_video.played_record(title, self.chess_start_pos, self.chess_moves_played, white, black,
                                         chess_video.result_of(self.chess_pos), game_id=self.chess_saved_id)
        twin = chess_video.duplicate_of(games, game)
        if twin is not None:                          # the same moves are saved already (a replayed game)
            self.chess_saved_id = twin["id"]
            self.chess_mark_save()
            if not auto:
                self.toast(_("These moves are saved already: {0}").format(twin["title"]))
            return
        try:
            chess_video.save_games(chess_video.stored(games, game))
        except OSError:
            self.toast(_("The game could not be saved."))
            return
        self.chess_saved_id = game["id"]
        self.chess_fill_games()
        self.chess_mark_save()
        self.toast((_("Game over: saved as {0}") if auto else _("Saved: {0}")).format(title))

    def chess_store(self, games: list):
        try:
            chess_video.save_games(games)
        except OSError:
            self.toast(_("The game could not be saved."))
        self.chess_fill_games()

    def chess_rename_game(self, game: dict):
        from gi.repository import Adw
        entry = Gtk.Entry(text=game["title"], activates_default=True, max_length=200)
        self.guard_text_field(entry)
        dialog = Adw.AlertDialog(heading=_("Rename the game"))
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("rename", _("Rename"))
        dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("rename")
        dialog.set_close_response("cancel")

        def answered(_d, answer):
            if answer == "rename":
                self.chess_store(chess_video.renamed(chess_video.load_games(), game["id"], entry.get_text()))
        dialog.connect("response", answered)
        dialog.present(self.window)
        entry.grab_focus()

    def chess_delete_game(self, game: dict):
        from gi.repository import Adw
        dialog = Adw.AlertDialog(heading=_("Delete this game?"), body=game["title"])   # plain text (no markup)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, answer: answer == "delete" and self.chess_store(
            chess_video.without(chess_video.load_games(), game["id"])))
        dialog.present(self.window)

    def chess_save_pgn(self, game: dict):
        """A .pgn file where the person chooses (a save dialog)."""
        text = chess_video.pgn(game)
        if not text:
            self.toast(_("That game cannot be replayed."))
            return
        safe = "".join(c if c.isalnum() or c in " -_" else " " for c in game["title"]).strip()[:80] or "game"
        dialog = Gtk.FileDialog(title=_("Save as PGN"), initial_name=" ".join(safe.split()) + ".pgn")

        def chosen(d, result):
            try:
                file = d.save_finish(result)
            except GLib.Error:
                return
            try:
                file.replace_contents(text.encode(), None, False, Gio.FileCreateFlags.NONE, None)
                self.toast(_("Saved: {0}").format(file.get_basename()))
            except GLib.Error as error:
                self.toast(error.message)
        dialog.save(self.window, None, chosen)

    # --- replaying -------------------------------------------------------------------------------------

    def chess_replay_open(self, game: dict):
        start, moves = chess_video.replay(game)
        if start is None or not moves:
            self.toast(_("That game cannot be replayed."))
            return
        self.chess_reset(start, "w", "replay")
        self.chess_replay = {"id": game["id"], "title": game["title"], "start": start, "moves": moves, "index": 0}
        self.chess_replay_row.set_visible(True)
        self.chess_pause.set_visible(False)
        self.chess_fill_games()                        # its card open, the others folded
        self.chess_title.set_label((_("Chess") + " · " + game["title"]).upper())
        self.show_page("chess")
        self.chess_replay_show(0)

    def chess_replay_show(self, index: int):
        replay = self.chess_replay
        index = max(0, min(len(replay["moves"]), index))
        replay["index"] = index
        pos = replay["start"]
        for move in replay["moves"][:index]:
            pos = rules.apply(pos, move)
        self.chess_pos = pos
        self.chess_moves_played = list(replay["moves"][:index])
        self.chess_last = replay["moves"][index - 1] if index else None
        self.chess_refresh()

    def chess_replay_status(self) -> str:
        replay = self.chess_replay or {"moves": [], "index": 0, "title": ""}
        return _("{0}: move {1} of {2}").format(replay["title"], replay["index"], len(replay["moves"]))

    def chess_replay_step(self, step: str):
        if self.chess_mode != "replay" or self.chess_replay is None:
            return
        index, last = self.chess_replay["index"], len(self.chess_replay["moves"])
        self.chess_replay_show({"first": 0, "back": index - 1, "next": index + 1, "last": last}[step])

    def chess_replay_auto(self):
        def tick():
            replay = self.chess_replay
            if self.chess_mode != "replay" or replay is None or not self.chess_replay_play.get_active():
                return False
            if replay["index"] >= len(replay["moves"]):
                self.chess_replay_play.set_active(False)
                return False
            self.chess_replay_show(replay["index"] + 1)
            return True
        GLib.timeout_add(REPLAY_STEP_MS, tick)

    def chess_play_from_here(self):
        """The game goes on from the move in view: you play the side to move, the opponent the other."""
        if self.chess_agent is None:
            self.toast(_("Choose an opponent first."))
            return
        self.chess_replay_play.set_active(False)
        self.chess_mode = "game"
        self.chess_player = self.chess_pos.turn
        self.chess_rival = None
        self.chess_replay_row.set_visible(False)
        self.chess_title.set_label(self.chess_title_text().upper())
        self.chess_refresh()

    def chess_replay_leave(self):
        """Another game started: the replay controls go."""
        if getattr(self, "chess_replay_row", None) is not None:
            self.chess_replay_row.set_visible(False)
            self.chess_replay_play.set_active(False)
