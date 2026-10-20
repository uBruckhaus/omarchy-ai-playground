"""One question to an agent for AI Playground's tests: its answer, how long it took, and the
tokens and cost where the agent reports them; with pictures where the agent takes them.

The agent is started as its adapter's `ask` says (one prompt, one answer, no tools, no session
kept: the chess page's way), in an empty private folder, with a time and size limit. How each
agent takes pictures (each checked with its --help, Claude's also with a run, 2026-10-20):

- claude: the question as stream-json on stdin, the pictures in it as base64 image blocks; its
  json answer carries tokens and cost (asked as json without pictures too);
- codex: `-i <file>` per picture, before the question;
- pi: `@<file>` per picture, before the question;
- the others take none (they are left out of the Vision tab).

Pictures are copied into the private folder first, so a file name never reaches argv as an option.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import chess_agent
from i18n import _

MAX_ANSWER = 512 * 1024          # bytes read of an answer (claude's json carries the text twice)
MAX_IMAGE = 8 * 1024 * 1024      # bytes per picture
MAX_IMAGES = 8
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
               ".gif": "image/gif"}
IMAGE_WAYS = {"claude": "stdin", "codex": "-i", "pi": "@"}
JSON_ANSWER = {"claude"}         # asked with --output-format json: the text with tokens and cost


@dataclass
class Result:
    text: str = ""
    seconds: float = 0.0
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost: float | None = None    # US dollars, as the agent reports it
    error: str = ""


def can_ask(command: str) -> bool:
    return bool(chess_agent.ask_argv(command))


def takes_images(command: str) -> bool:
    return command in IMAGE_WAYS and can_ask(command)


def _media_type(path: Path) -> str:
    return IMAGE_TYPES.get(path.suffix.lower(), "")


def check_image(path) -> str:
    """Why this file cannot be sent as a picture ("" when it can)."""
    path = Path(path)
    if not _media_type(path):
        return _("Only PNG, JPEG, WebP or GIF pictures can be sent.")
    try:
        if not path.is_file() or path.stat().st_size > MAX_IMAGE:
            return _("The picture is missing or larger than 8 MB.")
    except OSError:
        return _("The picture cannot be read.")
    return ""


def build(command: str, prompt: str, images: list, folder: str, level: str = chess_agent.DEFAULT_THINKING):
    """(argv, stdin bytes or None) for this question; images are paths inside folder already."""
    marker = "\x00prompt\x00"
    argv = chess_agent.run_argv(command, marker, level)
    if not argv:
        return [], None
    at = argv.index(marker)
    way = IMAGE_WAYS.get(command) if images else None
    if command in JSON_ANSWER:
        if way == "stdin":
            blocks = [{"type": "image", "source": {"type": "base64", "media_type": _media_type(Path(p)),
                                                   "data": base64.b64encode(Path(p).read_bytes()).decode()}}
                      for p in images]
            message = {"type": "user", "message": {"role": "user",
                                                   "content": blocks + [{"type": "text", "text": prompt}]}}
            options = ["--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]
            return argv[:at] + options + argv[at + 1:], (json.dumps(message) + "\n").encode()
        argv = argv[:at] + ["--output-format", "json"] + argv[at:]
        at += 2
    if way == "-i":
        argv = argv[:at] + [a for p in images for a in ("-i", p)] + argv[at:]
        at += 2 * len(images)
    elif way == "@":
        argv = argv[:at] + ["@" + p for p in images] + argv[at:]
        at += len(images)
    argv[at] = prompt
    return argv, None


def parse(command: str, out: str) -> Result:
    """The answer's text, and its tokens and cost where the agent wrote them (claude's json)."""
    if command not in JSON_ANSWER:
        return Result(text=out.strip())
    final = None
    for line in [out] + out.splitlines():          # one json answer, or stream-json: one per line
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("type") == "result":
            final = data
    if final is None:
        return Result(text=out.strip())
    usage = final.get("usage") if isinstance(final.get("usage"), dict) else {}

    def number(value):
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    tokens_in = sum(v for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
                    if (v := number(usage.get(k))) is not None) if usage else None
    text = final.get("result") if isinstance(final.get("result"), str) else ""
    return Result(text=text.strip(), tokens_in=tokens_in, tokens_out=number(usage.get("output_tokens")),
                  cost=number(final.get("total_cost_usd")),
                  error=_("The agent reported an error.") if final.get("is_error") and not text else "")


def ask(command: str, prompt: str, images=(), level: str = chess_agent.DEFAULT_THINKING,
        timeout: float | None = None, runner=None) -> Result:
    """Ask once (blocking: call it off the main thread)."""
    import ai_core
    images = list(images)[:MAX_IMAGES]
    if images and not takes_images(command):
        return Result(error=_("This agent cannot be sent pictures."))
    folder = tempfile.mkdtemp(prefix="ai-playground-")          # private (0700), empty
    try:
        copies = []
        for i, image in enumerate(images):
            problem = check_image(image)
            if problem:
                return Result(error=problem)
            copy = os.path.join(folder, f"picture-{i + 1}{Path(image).suffix.lower()}")
            shutil.copyfile(image, copy)
            copies.append(copy)
        argv, stdin = build(command, prompt, copies, folder, level)
        if not argv:
            return Result(error=_("This agent cannot be asked."))
        run = runner or ai_core.read_bounded_output
        start = time.monotonic()
        out, ok = run(argv, max_bytes=MAX_ANSWER, timeout=timeout or chess_agent.seconds(level) + 15,
                      stdin=stdin, cwd=folder, env={**os.environ, "NO_COLOR": "1"})
        spent = time.monotonic() - start
    except OSError as error:
        return Result(error=str(error))
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    answer = out.decode("utf-8", "replace") if isinstance(out, bytes) else str(out or "")
    result = parse(command, answer)
    result.seconds = spent
    if not result.text and not result.error:
        result.error = _("No answer: the agent took too long or stopped.") if not ok \
            else _("The agent gave an empty answer.")
    return result
