"""Reading a chess game from a video (AI Playground's chess tab): a video file, or a YouTube link
downloaded with yt-dlp; a frame every few seconds; an agent that sees names the position on each
new frame; the chess rules turn each change of position into the legal move (or two) that explains
it. The games are kept in games.json in the app's settings folder, to be replayed.

- Links: only https YouTube addresses (youtube.com/watch, /shorts, /live, youtu.be), given to
  yt-dlp as an argument after "--", with --ignore-config (no configuration file, so no --exec from
  one), --no-exec, no playlist, at most 480p (a 2D board reads well) and 1 GB, into a private
  folder that is removed after.
- Frames: ffprobe and ffmpeg, bounded, from the keyframes only when they are close enough (fast), PNG files
  scaled to at most FRAME_WIDTH wide, optionally only the first minutes. A frame that looks like
  the one before (a still board) is not sent again.
- Positions: the agent writes the piece placement as FEN (from White's side) or NONE; only eight
  well-formed ranks are taken.

- Fast way (YouTube, as Simple Recipes reads cooking videos): no download, only the video's
  details (yt-dlp --skip-download --dump-single-json): its description, often with the game's moves
  or PGN, read here without any agent; else its captions (the spoken commentary, fetched over
  https from YouTube only) and the description go to the agent once, which writes the moves. In
  both cases only a legal sequence from the start position is kept.

Blocking functions: call them off the main thread.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import chess_rules as rules

YOUTUBE = re.compile(r"https://(?:(?:www\.|m\.)?youtube\.com/(?:watch\?v=|shorts/|live/)|youtu\.be/)"
                     r"[A-Za-z0-9_-]{6,20}(?:[?&#][A-Za-z0-9_=&%.-]{0,200})?")
VIDEO_TYPES = (".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v")
MAX_DOWNLOAD = "1G"
DOWNLOAD_TIMEOUT = 30 * 60
FRAME_WIDTH = 1024
MAX_FRAMES = 600
INTERVALS = (2, 4, 8)            # seconds between frames (the import dialog's choice)
THUMB = 64                       # frames are compared as THUMB×THUMB grey thumbnails
PIXEL_CHANGE = 30                # a thumbnail pixel this much lighter or darker (0..255) has changed …
CHANGED_PIXELS = 3               # … and this many such pixels mean something moved (a piece is ~20 of them;
                                 # video compression noise stays below PIXEL_CHANGE)
GAMES_LIMIT = 200
PROMPT = ("This is one frame of a chess video. If a chess board with pieces is visible, write its position as the "
          "piece placement part of a FEN (eight ranks from rank 8 to rank 1, seen from White's side even when the "
          "board is shown turned) alone on the first line. If no board is visible, write NONE. Write nothing else.")


def youtube_url(text: str) -> str:
    """The YouTube address in a dropped text, or ""."""
    for word in str(text or "").split()[:20]:
        if len(word) <= 300 and YOUTUBE.fullmatch(word):
            return word
    return ""


# Every ffmpeg/ffprobe run reads only a local file in a real video container: a file named .mp4 that
# is really a playlist (HLS, concat, …) would otherwise make ffmpeg fetch addresses or read other files
# named in it. The tools also get a memory limit (a small file can declare a huge picture).
VIDEO_FORMATS = "mov,mp4,m4a,3gp,3g2,mj2,matroska,webm,avi,flv,mpegts"
MEDIA_MEMORY = 4 * 1024 ** 3


def media_argv(tool: str, options: list, video: str, rest: list) -> list:
    """[prlimit …] tool <options> -protocol_whitelist file -format_whitelist … -i video <rest>."""
    if not os.path.isabs(video):
        raise ValueError("a video path must be absolute")
    argv = [tool, *options, "-protocol_whitelist", "file", "-format_whitelist", VIDEO_FORMATS, "-i", video, *rest]
    if shutil.which("prlimit"):
        argv = ["prlimit", f"--as={MEDIA_MEMORY}", "--", *argv]
    return argv


def video_file(path) -> bool:
    try:
        return Path(path).suffix.lower() in VIDEO_TYPES and Path(path).is_file()
    except OSError:
        return False


def download(url: str, folder: str, runner=None) -> tuple[str, str]:
    """(video file, title) of a YouTube video downloaded into folder; ("", why) when it failed."""
    import ai_core
    if not youtube_url(url) or shutil.which("yt-dlp") is None:
        return "", "no yt-dlp" if youtube_url(url) else "not a YouTube link"
    argv = ["yt-dlp", "--ignore-config", "--no-exec", "--no-playlist", "--no-progress", "--no-mtime",
            "--no-cache-dir", "--restrict-filenames", "--max-filesize", MAX_DOWNLOAD,
            "-f", "bv*[height<=480][ext=mp4]/bv*[height<=480]/b[height<=480]/b",
            "-o", os.path.join(folder, "%(title).80B.%(ext)s"),
            "--print", "after_move:filepath", "--", url]
    out, ok = (runner or ai_core.read_bounded_output)(argv, max_bytes=64 * 1024, timeout=DOWNLOAD_TIMEOUT,
                                                     cwd=folder)
    lines = [line.strip() for line in (out or b"").decode("utf-8", "replace").splitlines() if line.strip()]
    path = lines[-1] if lines else ""
    real = os.path.realpath(path) if path else ""
    if not ok or not real.startswith(os.path.realpath(folder) + os.sep) or not os.path.isfile(real):
        return "", "download failed"
    return real, Path(real).stem.replace("_", " ")


def duration(video: str) -> float:
    import ai_core
    try:
        argv = media_argv("ffprobe", ["-v", "error", "-show_entries", "format=duration",
                                      "-of", "default=noprint_wrappers=1:nokey=1"], video, [])
    except ValueError:
        return 0.0
    out, ok = ai_core.read_bounded_output(argv, max_bytes=4096, timeout=30)
    try:
        return float(out.decode().strip()) if ok else 0.0
    except ValueError:
        return 0.0


LIMITS = (0, 5, 15, 30)          # minutes read from the start (0: the whole video); the import dialog's choice


def keyframe_gap(video: str) -> float:
    """The largest distance (seconds) between keyframes in the video's first minute (0: unknown)."""
    import ai_core
    try:
        argv = media_argv("ffprobe", ["-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey",
                                      "-read_intervals", "%+60", "-show_entries", "frame=pts_time", "-of", "csv=p=0"],
                          video, [])
    except ValueError:
        return 0.0
    out, ok = ai_core.read_bounded_output(argv, max_bytes=256 * 1024, timeout=30)
    times = []
    for line in out.decode("utf-8", "replace").splitlines() if ok else []:
        try:
            times.append(float(line.strip().strip(",")))
        except ValueError:
            continue
    gaps = [b - a for a, b in zip(times, times[1:]) if b > a]
    return max(gaps) if gaps else 0.0


def frames(video: str, folder: str, every: int, limit: int = 0) -> list:
    """PNG frames, one every `every` seconds (at most MAX_FRAMES), in time order; only the first
    `limit` seconds when it is given. When the keyframes are no farther apart than `every`, only
    they are decoded (many times faster); else every frame, so that no position is missed."""
    import ai_core
    if not os.path.isabs(video):
        return []
    pattern = os.path.join(folder, "frame-%04d.png")
    _out, ok = ai_core.read_bounded_output(
        media_argv("ffmpeg", ["-nostdin", "-v", "error", "-y"]
                   + (["-skip_frame", "nokey"] if 0 < keyframe_gap(video) <= every else []), video,
                   (["-t", str(int(limit))] if limit else [])
                   + ["-vf", f"fps=1/{int(every)},scale='min({FRAME_WIDTH},iw)':-2", "-frames:v", str(MAX_FRAMES),
                      pattern]),
        max_bytes=64 * 1024, timeout=20 * 60)
    return sorted(str(p) for p in Path(folder).glob("frame-*.png")) if ok else []


def thumbnail(path: str) -> bytes:
    """THUMB×THUMB grey pixels of a frame (to see whether anything moved)."""
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf, GLib
    try:
        pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, THUMB, THUMB, False)
    except GLib.Error:
        return b""
    data, step, channels = pix.get_pixels(), pix.get_rowstride(), pix.get_n_channels()
    return bytes((data[y * step + x * channels] + data[y * step + x * channels + 1]
                  + data[y * step + x * channels + 2]) // 3 for y in range(THUMB) for x in range(THUMB))


def changed(a: bytes, b: bytes) -> bool:
    if not a or not b or len(a) != len(b):
        return True
    return sum(1 for x, y in zip(a, b) if abs(x - y) >= PIXEL_CHANGE) >= CHANGED_PIXELS


# --- the fast way: the video's description and captions -----------------------------------------------

TEXT_PROMPT = ("Below are the title, the description and the spoken words of a chess video. Write the moves of the "
               "main game in it, from the usual start position, in standard algebraic notation with move numbers "
               "(for example: 1. e4 e5 2. Nf3 Nc6), all on one line, as far as they can be told. If no game can be "
               "told, write NONE. Write nothing else.\n\n")
MIN_TEXT_MOVES = 6               # fewer moves from the text: the frames are watched instead
MAX_TEXT = 20_000                # characters of description and captions given to the agent
CAPTION_HOSTS = (".youtube.com", ".googlevideo.com")


def video_info(url: str, runner=None) -> dict:
    """A YouTube video's details without downloading it (title, description, caption tracks)."""
    import ai_core
    if not youtube_url(url) or shutil.which("yt-dlp") is None:
        return {}
    argv = ["yt-dlp", "--ignore-config", "--no-exec", "--no-playlist", "--skip-download", "--dump-single-json",
            "--no-warnings", "--", url]
    out, ok = (runner or ai_core.read_bounded_output)(argv, max_bytes=8 * 1024 * 1024, timeout=60)
    try:
        info = json.loads(out) if ok and out else {}
    except ValueError:
        return {}
    return info if isinstance(info, dict) else {}


def caption_track(info: dict):
    """(format, url) of the spoken-language captions: uploaded ones first, then YouTube's automatic
    captions in the video's own language (as Simple Recipes picks them)."""
    spoken = str(info.get("language") or "").split("-")[0].lower()
    for tracks, original in ((info.get("subtitles") or {}, None), (info.get("automatic_captions") or {}, "-orig")):
        if not isinstance(tracks, dict):
            continue
        keys = [k for k in tracks if k != "live_chat" and isinstance(tracks[k], list) and tracks[k]]
        preferred = ([k for k in keys if original and k.endswith(original)]
                     + [k for k in keys if spoken and k.split("-")[0].lower() == spoken])
        if original is None and not preferred:
            preferred = keys[:1]
        for key in preferred:
            formats = {r.get("ext"): r.get("url") for r in tracks[key] if isinstance(r, dict) and r.get("url")}
            for ext in ("json3", "vtt"):
                if formats.get(ext):
                    return ext, formats[ext]
    return None


def caption_text(info: dict, fetch=None) -> str:
    """The spoken words of the video (its captions), or ""."""
    import netfetch
    from urllib.parse import urlsplit
    track = caption_track(info)
    if not track:
        return ""
    ext, url = track
    host = urlsplit(url).hostname or ""
    if not url.startswith("https://") or not host.endswith(CAPTION_HOSTS):
        return ""
    try:
        raw = (fetch or netfetch.fetch)(url, max_bytes=4 * 1024 * 1024, timeout=20).decode("utf-8", "replace")
        if ext == "json3":
            lines = ["".join(seg.get("utf8", "") for seg in event.get("segs") or [] if isinstance(seg, dict))
                     for event in json.loads(raw).get("events", []) if isinstance(event, dict)]
        else:
            lines = [re.sub(r"<[^>]+>", "", line) for line in raw.splitlines()
                     if line.strip() and "-->" not in line and not line.startswith(("WEBVTT", "Kind:", "Language:"))]
    except (netfetch.FetchError, ValueError, AttributeError):
        return ""
    words, previous = [], None
    for line in lines:
        line = re.sub(r"\[[^\]]{1,30}\]", " ", line).strip()
        if line and line != previous:
            words.append(line)
        previous = line or previous
    return re.sub(r"\s+", " ", " ".join(words)).strip()[:MAX_TEXT]


def moves_in_text(text: str):
    """(start, moves) of the longest legal game from the start position written in a text (a PGN or
    a move list such as "1. e4 e5 2. Nf3"), or (start, [])."""
    start = rules.start()
    words = [w for w in re.findall(r"\S+", str(text or ""))[:20_000]]
    best = []
    for i, word in enumerate(words):
        if not re.fullmatch(r"1\.(?:\.\.)?|1\.[A-Za-z].*", word):
            continue                                  # a game starts at "1."
        pos, moves, rest = start, [], [word[2:]] + words[i + 1:] if len(word) > 2 else words[i + 1:]
        for token in rest:
            token = re.sub(r"^\d+\.(\.\.)?", "", token).strip(",;()")
            if not token or token in ("1-0", "0-1", "1/2-1/2", "½-½", "*"):
                if token:
                    break
                continue
            move = rules.parse(pos, token)
            if move is None:
                break
            moves.append(move)
            pos = rules.apply(pos, move)
        if len(moves) > len(best):
            best = moves
    return start, best


def reading_of(moves: list) -> "Reading":
    reading = Reading()
    reading.start = reading.pos = rules.start()
    for move in moves:
        reading.moves.append(move)
        reading.pos = rules.apply(reading.pos, move)
    return reading


PLACEMENT = re.compile(r"(?:[pnbrqkPNBRQK1-8]{1,8}/){7}[pnbrqkPNBRQK1-8]{1,8}")


def placement(answer: str):
    """The board (64 pieces) of the first well-formed FEN placement in an answer, or None. A placement
    written with spaces ("r n b q k b n r / p p …", as some local models write it) counts too."""
    text = str(answer or "")
    joined = re.sub(r"(?<=[pnbrqkPNBRQK1-8/])[ \t]+(?=[pnbrqkPNBRQK1-8/])", "", text)
    for word in (re.findall(r"\S+", text)[:50] + re.findall(r"\S+", joined)[:50]):
        found = PLACEMENT.search(word.strip("`*.,;:"))
        if not found:
            continue
        try:
            board = rules.from_fen(found.group(0) + " w - - 0 1").board
        except rules.FenError:
            continue
        if board.count("K") == 1 and board.count("k") == 1:
            return board
    return None


@dataclass
class Reading:
    start: object = None                  # rules.Position the game starts from
    moves: list = field(default_factory=list)
    unmatched: int = 0                    # boards no legal move (or two) led to
    pos: object = None                    # where it stands now
    guessed_turn: bool = False            # it started mid-game: Black may have been to move


def follow(reading: Reading, board) -> bool:
    """Add the move (or two moves) that turn the current position into this board. The first board
    starts the game (White to move if it is the start position, else whoever can explain the next
    change). False when nothing legal leads there."""
    if reading.pos is None:
        start = rules.start()
        reading.start = start if board == start.board else _position(board, "w")
        reading.pos = reading.start
        reading.guessed_turn = board != start.board
        return True
    if board == reading.pos.board:
        return True
    tries = [reading.pos]
    if reading.guessed_turn and not reading.moves:
        tries.append(_position(reading.pos.board, "b"))     # the video started with Black to move
    for pos in tries:
        for move in rules.legal_moves(pos):
            after = rules.apply(pos, move)
            if after.board == board:
                return _played(reading, pos, [move])
        for move in rules.legal_moves(pos):                  # a frame missed one move in between
            after = rules.apply(pos, move)
            for reply in rules.legal_moves(after):
                if rules.apply(after, reply).board == board:
                    return _played(reading, pos, [move, reply])
    reading.unmatched += 1
    return False


def _position(board, turn: str):
    """A position from a board seen mid-game: castling only where king and rook stand at home."""
    return rules.from_fen(rules.fen(rules.Position(board, turn, "KQkq")))


def _played(reading: Reading, pos, moves: list) -> bool:
    if not reading.moves and pos is not reading.pos:
        reading.start = pos
    for move in moves:
        reading.moves.append(move)
        pos = rules.apply(pos, move)
    reading.pos = pos
    return True


# --- saved games ------------------------------------------------------------------------------------

def games_file() -> Path:
    import desktop
    return desktop.config_dir() / "games.json"


def load_games(path: Path | None = None) -> list:
    try:
        with open(path or games_file(), "rb") as handle:
            data = json.loads(handle.read(4 * 1024 * 1024) or b"[]")
    except (OSError, ValueError):
        return []
    games = []
    for game in data if isinstance(data, list) else []:
        if isinstance(game, dict) and isinstance(game.get("title"), str) and isinstance(game.get("start"), str) \
                and isinstance(game.get("moves"), list) and all(isinstance(m, str) for m in game["moves"]):
            if not isinstance(game.get("id"), str) or not re.fullmatch(r"[0-9a-f]{32}", game["id"]):
                game["id"] = uuid.uuid4().hex                 # kept from the next save on
            games.append(game)
    return games[:GAMES_LIMIT]


def save_games(games: list, path: Path | None = None) -> None:
    """Atomic, private (0600)."""
    path = Path(path or games_file())
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(prefix=".games-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(games[:GAMES_LIMIT], handle, ensure_ascii=False, indent=1)
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def game_record(title: str, source: str, reading: Reading) -> dict:
    """A game read from a video."""
    return played_record(title, reading.start, reading.moves, source=source, kind="video")


def played_record(title: str, start, moves: list, white: str = "?", black: str = "?", result: str = "*",
                  source: str = "", kind: str = "played", game_id: str = "") -> dict:
    """A saved game: played here (you or agents) or read from a video. game_id keeps the entry of a
    game saved before (saving again updates it)."""
    return {"id": game_id or uuid.uuid4().hex, "title": title[:200], "source": source[:300], "kind": kind,
            "date": time.strftime("%Y-%m-%d %H:%M"), "start": rules.fen(start), "moves": [m.uci() for m in moves],
            "white": str(white)[:80], "black": str(black)[:80], "result": result if result in RESULTS else "*"}


RESULTS = ("1-0", "0-1", "1/2-1/2", "*")


def result_of(pos) -> str:
    """The PGN result of a position: who mated, a draw, or * while the game goes on."""
    state = rules.status(pos)
    if state == "checkmate":
        return "0-1" if pos.turn == "w" else "1-0"
    return "1/2-1/2" if state in ("stalemate", "fifty", "material") else "*"


def stored(games: list, game: dict) -> list:
    """The games with this one first: in place of the entry with its id, else new."""
    return [game] + [g for g in games if g.get("id") != game.get("id")]


YOUTUBE_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/live/)([A-Za-z0-9_-]{6,20})")


def video_key(source: str) -> str:
    """One name per video: "youtube:<id>" whatever the link looks like (youtu.be, watch?v=, &t=…,
    /shorts/), else the file's real path (through links)."""
    source = str(source or "").strip()
    if not source:
        return ""
    found = YOUTUBE_ID.search(source) if youtube_url(source) else None
    if found:
        return "youtube:" + found.group(1)
    return os.path.realpath(source) if os.path.isabs(source) else source


def same_source(games: list, source: str):
    """The saved game read from this video before (any link to it, or the same file), or None."""
    key = video_key(source)
    return next((g for g in games if key and video_key(g.get("source", "")) == key), None)


def duplicate_of(games: list, game: dict):
    """A saved game (another entry) with the same start and the same moves, or None."""
    return next((g for g in games if g.get("id") != game.get("id") and g.get("start") == game.get("start")
                 and g.get("moves") == game.get("moves")), None)


def renamed(games: list, game_id: str, title: str) -> list:
    title = " ".join(str(title or "").split())[:200]
    return [{**g, "title": title} if g.get("id") == game_id and title else g for g in games]


def without(games: list, game_id: str) -> list:
    return [g for g in games if g.get("id") != game_id]


def matching(games: list, text: str) -> list:
    """The games whose title has every word of the search text (case ignored)."""
    words = str(text or "").lower().split()
    return [g for g in games if all(w in g["title"].lower() for w in words)]


def pgn(game: dict) -> str:
    """The game in PGN, the chess programs' format (with its start position when it is not the
    usual one); the result is open (*), the players unknown."""
    start, moves = replay(game)
    if start is None:
        return ""

    def tag(name, value):
        return f'[{name} "{str(value).replace(chr(92), "").replace(chr(34), chr(39))}"]'
    date = str(game.get("date", ""))[:10].replace("-", ".") or "????.??.??"
    result = game.get("result") if game.get("result") in RESULTS else "*"
    tags = [tag("Event", game["title"]), tag("Site", game.get("source") or "?"), tag("Date", date),
            tag("White", game.get("white") or "?"), tag("Black", game.get("black") or "?"), tag("Result", result),
            tag("Annotator", "AI Playground")]
    if rules.fen(start) != rules.START_FEN:
        tags += [tag("SetUp", "1"), tag("FEN", rules.fen(start))]
    text = (rules.game_san(start, moves) + " " + result).strip()
    lines, line = [], ""
    for word in text.split():                     # movetext wrapped at 80 characters
        if len(line) + len(word) + 1 > 80:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    lines.append(line)
    return "\n".join(tags) + "\n\n" + "\n".join(lines) + "\n"


def replay(game: dict):
    """(start position, moves) of a saved game; moves that are not legal there end it."""
    try:
        start = rules.from_fen(game["start"])
    except (rules.FenError, KeyError):
        return None, []
    pos, moves = start, []
    for text in game.get("moves", [])[:2000]:
        move = rules.parse(pos, text)
        if move is None:
            break
        moves.append(move)
        pos = rules.apply(pos, move)
    return start, moves
