"""AI Playground's Analyze tab: what an agent can do with one of its models — answer text, see
pictures, read a chess video, think more or less, play chess, how much it keeps in mind — each
found out by a short test question where one tells, else read from what the agent or model says
about itself.

The models an agent can be asked with for one run (chess_agent.MODEL_FLAG), each from the agent's
own list (nothing guessed):

- Claude Code: the aliases its --help names (online_models);
- Codex: ~/.codex/models_cache.json, with each model's input kinds, reasoning levels and context;
- Pi: `pi --list-models` (provider, model, context, thinking, images), local providers included;
- any other agent: its own model only.

A local model (Pi on llama.cpp, …) is set up for pictures where it can be (frame_check) and its
server is held up during the tests (local_hold). Blocking: run() is called off the main thread.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import agent_run
import chess_agent
import chess_rules as rules
import frame_check
import gpu_check
import harnesses
import local_hold
import online_models
from i18n import N_, _

TEXT_PROMPT = "What is 17 times 23? Answer with the number only."
TEXT_ANSWER = "391"
MAX_LIST = 64 * 1024
PI_ROW = re.compile(r"^(\S+)\s+(\S+)\s+([\d.]+[KM]?)\s+\S+\s+(yes|no)\s+(yes|no)\s*$")
# The capability cards, in order: key, title, what it means.
CARDS = (
    ("text", N_("Text"), N_("Answers a question in writing: the base of everything else. One small sum is asked; "
                            "the time, and tokens and cost where the agent reports them, are shown.")),
    ("pictures", N_("Pictures"), N_("Sees a picture sent with the question (screenshots, photos, the Vision tab). "
                                    "One test picture is sent: a red square, and the agent is asked for its colour. "
                                    "A local model is set up for it first where it can be.")),
    ("video", N_("Chess videos"), N_("Reads the moves from a chess video frame by frame (Chess tab › Import video). "
                                     "Each frame is one question with a picture, so it needs pictures and quick "
                                     "answers; the time of the test picture tells.")),
    ("thinking", N_("Thinking time"), N_("Can be told to think briefly or long (Thinking time on the Chess tab, quick "
                                         "answers for video frames). Read from the agent's settings and the model's "
                                         "own description.")),
    ("chess", N_("Chess"), N_("Plays chess on the Chess tab: one legal opening move is asked.")),
    ("context", N_("Context"), N_("How much text it keeps in mind at once (prompt, files, the conversation), in "
                                  "tokens; a page of text is about 500. Read from the model's description.")),
    ("making", N_("Making pictures"), N_("Draws a new picture from a text. Agents asked for one answer here have no "
                                         "drawing tool; the Generate tab makes pictures on this computer's graphics "
                                         "card.")),
)


@dataclass
class Choice:
    label: str                       # shown in the model list
    arg: str = ""                    # the --model value ("" the agent's own model)
    local: tuple | None = None       # (server key, model) of a local model
    images: bool | None = None       # what the model's description says
    thinking: bool | None = None
    levels: list = field(default_factory=list)
    context: int | None = None


@dataclass
class Finding:
    key: str
    state: str                       # "yes", "no", "slow", "unknown", "waiting", "skipped"
    summary: str = ""
    detail: str = ""
    download: object = None          # frame_check.Download: a vision file to fetch


MARKS = {"yes": "✓", "no": "✗", "slow": "!", "unknown": "?", "waiting": "…", "skipped": "–"}


def _tokens(text: str) -> int | None:
    match = re.fullmatch(r"([\d.]+)([KM]?)", text)
    if not match:
        return None
    return int(float(match.group(1)) * {"": 1, "K": 1000, "M": 1_000_000}[match.group(2)])


def pi_models(runner=None) -> list:
    """`pi --list-models` as Choices (provider/model)."""
    import ai_core
    out, ok = (runner or ai_core.read_bounded_output)(["pi", "--list-models"], max_bytes=MAX_LIST, timeout=30)
    found = []
    for line in (out.decode("utf-8", "replace") if isinstance(out, bytes) else str(out or "")).splitlines()[1:]:
        match = PI_ROW.match(line.strip())
        if not match:
            continue
        provider, model, context, thinking, images = match.groups()
        arg = f"{provider}/{model}"
        if not chess_agent.MODEL_NAME.fullmatch(arg):
            continue
        server = gpu_check.PROVIDER_KEYS.get(provider)
        found.append(Choice(f"{model} ({provider})", arg, (server, model) if server else None, images == "yes",
                            thinking == "yes", [], _tokens(context)))
    return found


def codex_models(home=None) -> list:
    path = (home or online_models.HOME) / ".codex/models_cache.json"
    try:
        data = json.loads(online_models._read(path) or "{}")
    except ValueError:
        return []
    found = []
    for row in data.get("models") or () if isinstance(data, dict) else ():
        if not isinstance(row, dict) or row.get("visibility") != "list" or not isinstance(row.get("slug"), str) \
                or not online_models.NAME.fullmatch(row["slug"]):
            continue
        kinds = row.get("input_modalities") if isinstance(row.get("input_modalities"), list) else None
        levels = [x.get("effort") for x in row.get("supported_reasoning_levels") or ()
                  if isinstance(x, dict) and isinstance(x.get("effort"), str)]
        context = row.get("context_window") if isinstance(row.get("context_window"), int) else None
        found.append(Choice(str(row.get("display_name") or row["slug"])[:60], row["slug"], None,
                            ("image" in kinds) if kinds is not None else None, bool(levels) or None, levels, context))
    return found[:online_models.MAX_CHOICES]


def current_model(command: str) -> str:
    try:
        import agent_details
        model = (agent_details.details_for(command).model or "").strip()
    except Exception:
        model = ""
    if not model and command == "codex":           # the top-level `model = "…"` of its config.toml
        head = re.split(r"^\s*\[", online_models._read(online_models.HOME / ".codex/config.toml", 256 * 1024),
                        maxsplit=1, flags=re.MULTILINE)[0]
        match = re.search(r'^model\s*=\s*"([^"\n]{1,100})"', head, re.MULTILINE)
        model = match.group(1) if match and online_models.NAME.fullmatch(match.group(1)) else ""
    return model


def choices(agent, runner=None) -> list:
    """The agent's own model first, then the others it can be asked with for one run."""
    own = current_model(agent.command)
    first = Choice(_("Its own model ({0})").format(own) if own else _("Its own model"), "",
                   gpu_check.agent_model(agent.command))
    if agent.command == "pi":
        listed = pi_models(runner)
        for item in listed:                       # the own model's description, where Pi lists it
            if first.local and item.local == first.local:
                first.images, first.thinking, first.context = item.images, item.thinking, item.context
        return [first] + [item for item in listed if not (first.local and item.local == first.local)]
    if agent.command == "codex":
        listed = codex_models()
        for item in listed:
            if own and item.arg == own.split(" (")[0]:
                first.images, first.thinking, first.levels, first.context = \
                    item.images, item.thinking, item.levels, item.context
        return [first] + [item for item in listed if item.arg != own]
    if agent.command == "claude":
        return [first] + [Choice(alias.capitalize(), alias) for alias in online_models.CLAUDE_ALIASES]
    return [first]


def _seconds(value: float) -> str:
    return f"{value:.1f} s"


def run(agent, choice: Choice, report, cancelled=lambda: False) -> None:
    """The tests in CARDS order; report(Finding) after each (also while it waits)."""
    command, model = agent.command, choice.arg
    harness = harnesses.get(command)
    hold = local_hold.Hold(choice.local[0]) if choice.local else None
    if hold is not None:
        hold.__enter__()
    try:
        _run(agent, choice, command, model, harness, hold, report, cancelled)
    finally:
        if hold is not None:
            hold.__exit__(None, None, None)


def _run(agent, choice, command, model, harness, hold, report, cancelled):
    # Facts first: they need no question.
    levels = list(dict(harness.effort)) if harness is not None and harness.effort else []
    if choice.levels:
        report(Finding("thinking", "yes", ", ".join(choice.levels),
                       _("The model has these reasoning levels; AI Playground uses {0} of the agent's: {1}.").format(
                           len(levels), ", ".join(levels)) if levels else ""))
    elif choice.thinking is False:
        report(Finding("thinking", "no", _("the model does not think"),
                       _("It answers at once; there is no thinking time to set.")))
    elif levels:
        local_note = " " + _("For a local model, the switch in Pi's settings makes “off” reach it (set up by the "
                             "Pictures test).") if choice.local and command == "pi" else ""
        report(Finding("thinking", "yes", ", ".join(levels),
                       _("{0} takes a thinking time for each question: {1}.").format(agent.name, ", ".join(levels))
                       + local_note))
    else:
        report(Finding("thinking", "unknown", _("not stated"), _("The agent's settings name no thinking time.")))
    if choice.context:
        report(Finding("context", "yes", _("{0} tokens").format(f"{choice.context:,}"),
                       _("About {0} pages of text.").format(f"{choice.context // 500:,}")))
    else:
        report(Finding("context", "unknown", _("not stated"),
                       _("The agent does not say how much this model keeps in mind.")))
    report(Finding("making", "no", _("not with one answer"),
                   _("Use the Generate tab: it makes pictures on this computer, without an agent.")))

    if hold is not None and hold.error:
        why = _("The local LLM did not start: {0}").format(hold.error)
        for key in ("text", "pictures", "video", "chess"):
            report(Finding(key, "no", why))
        return

    # Text.
    report(Finding("text", "waiting", _("asking…")))
    answer = agent_run.ask(command, TEXT_PROMPT, level="quick", model=model, think=not choice.local)
    parts = [_seconds(answer.seconds)]
    if answer.tokens_out is not None:
        parts.append(_("{0} tokens").format(answer.tokens_out))
    if answer.cost is not None:
        parts.append(f"${answer.cost:.4f}")
    if TEXT_ANSWER in answer.text:
        report(Finding("text", "yes", " · ".join(parts), _("Answer: {0}").format(answer.text.strip()[:80])))
    else:
        report(Finding("text", "no", answer.error or _("a wrong answer"),
                       _("Answer: {0}").format(answer.text.strip()[:200]) if answer.text else answer.error))
        for key in ("pictures", "video", "chess"):
            report(Finding(key, "skipped", _("not tested: no text answer")))
        return
    if cancelled():
        return

    # Pictures, and from them the chess videos.
    report(Finding("pictures", "waiting", _("one test picture…")))
    report(Finding("video", "waiting", _("after the picture test…")))
    if not agent_run.takes_images(command):
        why = _("{0} cannot be sent pictures from here (Claude Code, Codex and Pi can).").format(agent.name)
        report(Finding("pictures", "no", _("no pictures"), why))
        report(Finding("video", "no", _("needs pictures"), why))
    elif choice.images is False:
        why = _("The model's description says it takes text only.")
        report(Finding("pictures", "no", _("text only"), why))
        report(Finding("video", "no", _("needs pictures"), why))
    elif choice.local:
        seen = frame_check.check(agent, local=choice.local, model=model) if model else frame_check.check(agent)
        if seen.state == "ready":
            slow = seen.seconds >= frame_check.SLOW
            report(Finding("pictures", "yes", _seconds(seen.seconds), seen.message))
            report(Finding("video", "slow" if slow else "yes",
                           _("about {0} per frame").format(_seconds(seen.seconds)),
                           _("Usable, but a long video takes a while.") if slow else
                           _("A video frame is larger than the test picture: a few seconds each. Local models misread busy "
                             "positions more often than Claude Code or Codex.")))
        else:
            report(Finding("pictures", "no", _("not yet") if seen.state == "download" else _("no"), seen.message,
                           seen.download))
            report(Finding("video", "no", _("needs pictures"), seen.message))
    else:
        seen = frame_check.probe(command, model=model)
        if "red" in seen.text.lower():
            slow = seen.seconds >= frame_check.SLOW
            report(Finding("pictures", "yes", _seconds(seen.seconds), _("It named the colour of the test picture.")))
            report(Finding("video", "slow" if slow else "yes", _("about {0} per frame").format(_seconds(seen.seconds)),
                           _("Usable, but a long video takes a while.") if slow else
                           _("A video frame is larger than the test picture: it may take a little longer.")))
        else:
            why = _("It did not name the test picture's colour: {0}").format(
                seen.error or seen.text.strip()[:120] or _("no answer"))
            report(Finding("pictures", "no", _("no"), why))
            report(Finding("video", "no", _("needs pictures"), why))
    if cancelled():
        return

    # Chess.
    if not chess_agent.plays(command):
        report(Finding("chess", "no", _("not offered"), _("Its adapter does not offer chess.")))
        return
    report(Finding("chess", "waiting", _("one move…")))
    pos = rules.start()
    reply = agent_run.ask(command, chess_agent.prompt(pos, [], "w", level="quick"), level="quick", model=model,
                          think=not choice.local)
    move, written = rules.find_move(pos, reply.text) if reply.text else (None, "")
    if move is not None:
        report(Finding("chess", "yes", f"1. {written} · {_seconds(reply.seconds)}",
                       _("It answered with a legal opening move.")))
    else:
        report(Finding("chess", "no", reply.error or _("no legal move"),
                       _("Answer: {0}").format(reply.text.strip()[:200]) if reply.text else reply.error))
