"""Can this agent read a chess video's frames with the local model it uses? Set up what can be set
up, then try it once (AI Playground's video import).

An agent on a local LLM reads a frame only when every link works: the server's model sees
pictures (llama.cpp: an mmproj vision file in its models.ini section; Ollama and LM Studio say so
themselves), the agent sends pictures to that model (Pi: "input": ["text", "image"] in
~/.pi/agent/models.json), and it answers in seconds, not after thousands of tokens of thinking (Pi:
the model's chat-template switch enable_thinking, so `--thinking off` reaches it). check() sets up
what it can by itself:

- llama.cpp: an mmproj file beside the model's file (the user's own, in a folder only they can
  write to) goes into its models.ini section (a copy of
  models.ini is kept first; the server is restarted when nobody is connected to it); with none
  there, the model's Hugging Face repository (its folder <publisher>/<repo>) is asked for one, and
  the dialog offers to download it (size shown, SHA-256 checked) — never without a click;
- Pi: the model's entry gets "image" and the thinking switch (a copy of models.json is kept);

then asks the agent one test picture (a red square: "which colour?") with the server held up
(local_hold.Hold). What cannot be set up (an Ollama or LM Studio model without vision) is said.

Blocking (files, loopback, one agent run): call it off the main thread.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import agent_run
import gpu_check
import local_hold
from i18n import _

HOME = Path.home()
PI_MODELS = HOME / ".pi/agent/models.json"
LLAMA_ENV = HOME / ".config/llama.cpp/server.env"
BACKUPS = HOME / ".local/state/ai-playground/backups"
HF = "https://huggingface.co"
REPO_RE = re.compile(r"[A-Za-z0-9][\w.-]{0,95}/[A-Za-z0-9][\w.-]{0,95}")
SAFE_PATH = re.compile(r"/[\w./+@ -]{1,1000}")       # a vision file path written into models.ini: one plain line
SAFE_NAME = re.compile(r"[^\w.-]+")
MAX_JSON = 4 * 1024 * 1024
MAX_MMPROJ = 8 * 1024 ** 3          # bytes a vision file may have
FORGET_NO = 120.0                  # seconds a "cannot" is kept (a ready agent stays ready this session)
SLOW = 15.0                         # seconds per frame from which it is called slow
PROBE_PROMPT = "What colour is the square in this picture? Answer with one English word."
THINKING_SWITCH = {"thinkingFormat": "chat-template", "chatTemplateKwargs": {"enable_thinking": {"$var": "thinking.enabled"}}}
PROVIDER_OF = {server: provider for provider, server in gpu_check.PROVIDER_KEYS.items()}
_cache: dict = {}
_one_at_a_time = threading.Lock()   # a second check waits (and finds the first one's result)


@dataclass
class Download:
    repo: str
    path: str                        # the file in the repository
    size: int
    sha256: str
    target: Path                     # where it goes: beside the model's own file
    section: str                     # the models.ini section it is for


@dataclass
class Result:
    state: str                       # "ready", "download" (a vision file can be fetched), "no", "cloud"
    message: str = ""
    seconds: float = 0.0             # the test picture's answer time
    done: list = field(default_factory=list)      # what was set up
    download: Download | None = None


# --- llama.cpp --------------------------------------------------------------------------------------

def _read(path: Path, limit: int = MAX_JSON) -> str:
    try:
        with open(path, "rb") as handle:
            return handle.read(limit).decode("utf-8", "replace")
    except OSError:
        return ""


def backup(path: Path) -> None:
    """A copy of a file before it is changed, under ~/.local/state/ai-playground/backups/<time>/."""
    folder = BACKUPS / time.strftime("%Y%m%d-%H%M%S")
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copy2(path, folder / path.name)


def _write(path: Path, text: str) -> None:
    """Atomically, keeping the file's mode (never looser)."""
    mode = path.stat().st_mode & 0o777
    tmp = path.with_name(f".{path.name}.ai-playground")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)


def model_folders() -> list:
    """Where llama.cpp's model files are: its router folder (server.env), then the usual ones."""
    roots = []
    for line in _read(LLAMA_ENV, 64 * 1024).splitlines():
        key, _sep, value = line.partition("=")
        if key.strip() in ("LLAMA_MODELS_DIR", "LLAMA_ARG_MODELS_DIR") and value.strip().startswith("/"):
            roots.append(Path(value.strip()))
    return roots + gpu_check._models_root() + list(gpu_check.MODEL_FOLDERS)


def model_file(model: str, roots=None) -> Path | None:
    """The model's .gguf (links followed), looked for up to three folders deep."""
    wanted = model.lower().removesuffix(".gguf") + ".gguf"
    for root in roots if roots is not None else model_folders():
        if not root.is_dir():
            continue
        base = len(root.parts)
        for folder, dirs, files in os.walk(root, followlinks=False):
            if len(Path(folder).parts) - base >= 3:
                dirs[:] = []
            for name in files:
                if name.lower() == wanted:
                    path = Path(folder, name).resolve()
                    if path.is_file():
                        return path
    return None


def own_folder(folder: Path) -> bool:
    """A folder of this user's that nobody else can write to (a model folder may be shared)."""
    try:
        info = os.stat(folder)
    except OSError:
        return False
    return info.st_uid == os.getuid() and not info.st_mode & 0o022


def own_file(path: Path) -> bool:
    """A plain file of this user's, not a link, with a name that fits on one models.ini line."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    import stat
    return stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and bool(SAFE_PATH.fullmatch(str(path)))


def local_mmproj(model_path: Path) -> Path | None:
    """A vision file beside the model's own file: only the user's own (llama.cpp loads it), in a
    folder only they can write to."""
    if not own_folder(model_path.parent):
        return None
    try:
        found = sorted(p for p in model_path.parent.iterdir()
                       if p.suffix.lower() == ".gguf" and "mmproj" in p.name.lower() and own_file(p))
    except OSError:
        return None
    return found[0] if found else None


def ini_with_mmproj(text: str, section: str, mmproj: Path) -> str | None:
    """models.ini with an mmproj line in this section (None when it has no such section)."""
    match = re.search(rf"^\[{re.escape(section)}\][ \t]*\n", text, re.MULTILINE | re.IGNORECASE)
    if match is None or not SAFE_PATH.fullmatch(str(mmproj)):
        return None
    return text[:match.end()] + f"mmproj = {mmproj}\n" + text[match.end():]


def section_of(model: str, ini: Path = local_hold.MODELS_INI) -> str | None:
    text = _read(ini, local_hold.MAX_INI)
    for name in re.findall(r"^\[([^\]\n]{1,120})\]", text, re.MULTILINE):
        if name.strip().lower() == model.lower().removesuffix(".gguf"):
            return name.strip()
    return None


def add_mmproj(section: str, mmproj: Path, ini: Path = local_hold.MODELS_INI) -> str:
    """Write the line; restart llama.cpp when it runs and nobody uses it. What to say ("" when it
    is ready now)."""
    text = _read(ini, local_hold.MAX_INI)
    new = ini_with_mmproj(text, section, mmproj)
    if new is None:
        return _("its section is missing in llama.cpp's models.ini")
    backup(ini)
    _write(ini, new)
    port = local_hold.port_of("llamacpp")
    import loopback
    if loopback.loopback_listeners(port):
        if local_hold.clients(port):
            return _("llama.cpp is in use now; it reads the vision file when it starts next time")
        service = local_hold.service_of("llamacpp")
        if service:
            subprocess.run(["systemctl", "--user", "restart", f"{service}.service"], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60, check=False)
    return ""


def repo_of(model_path: Path) -> str:
    """<publisher>/<repo> from the model's folder (the layout of downloaded models), else ""."""
    repo = f"{model_path.parent.parent.name}/{model_path.parent.name}"
    return repo if REPO_RE.fullmatch(repo) else ""


def hf_mmproj(repo: str, folder: Path, section: str, fetch=None) -> Download | None:
    """The repository's vision file (BF16, else F16, else the smallest), with its size and SHA-256."""
    import netfetch
    try:
        raw = (fetch or netfetch.fetch)(f"{HF}/api/models/{repo}/tree/main", max_bytes=MAX_JSON, timeout=20)
        rows = json.loads(raw)
    except (OSError, ValueError, netfetch.FetchError):
        return None
    files = []
    for row in rows if isinstance(rows, list) else ():
        path = row.get("path") if isinstance(row, dict) else None
        lfs = row.get("lfs") if isinstance(row, dict) else None
        if not (isinstance(path, str) and "/" not in path and path.lower().endswith(".gguf")
                and "mmproj" in path.lower() and isinstance(lfs, dict)):
            continue
        size, sha = lfs.get("size"), lfs.get("oid")
        if isinstance(size, int) and 0 < size <= MAX_MMPROJ and isinstance(sha, str) \
                and re.fullmatch(r"[0-9a-f]{64}", sha):
            files.append((path, size, sha))
    if not files:
        return None
    rank = {"bf16": 0, "f16": 1}
    path, size, sha = min(files, key=lambda f: (next((r for k, r in rank.items() if k in f[0].lower()), 2), f[1]))
    if not own_folder(folder):
        return None
    stem = SAFE_NAME.sub("-", path[:-5].replace("mmproj", "")).strip("-_.") or "vision"
    name = SAFE_NAME.sub("-", section).strip("-_.") or "model"
    target = folder / (f"mmproj-{name}-{stem}"[:190] + ".gguf")
    return Download(repo, path, size, sha, target, section)


def fetch_mmproj(d: Download, progress=None, cancelled=lambda: False) -> str:
    """Download it beside the model (curl into an exclusive staging file, size bound, SHA-256
    checked before it gets its name), then into models.ini. What to say ("" when done)."""
    import hashlib
    part = d.target.with_name(d.target.name + ".part")
    try:
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    except OSError as error:
        return str(error)
    try:
        url = f"{HF}/{d.repo}/resolve/main/{quote(d.path)}"
        curl = subprocess.Popen(["curl", "-fsSL", "--proto", "=https", "--max-filesize", str(d.size), "--", url],
                                stdin=subprocess.DEVNULL, stdout=fd, stderr=subprocess.DEVNULL)
        while curl.poll() is None:
            if cancelled():
                curl.kill()
                curl.wait()
                raise OSError(_("cancelled"))
            if progress:
                progress(min(1.0, os.fstat(fd).st_size / d.size))
            time.sleep(0.5)
        if curl.returncode or os.fstat(fd).st_size != d.size:
            raise OSError(_("the download stopped"))
        digest = hashlib.sha256()
        offset = 0
        while chunk := os.pread(fd, 8 * 1024 * 1024, offset):
            digest.update(chunk)
            offset += len(chunk)
        if digest.hexdigest() != d.sha256:
            raise OSError(_("its checksum does not match"))
        os.replace(part, d.target)
    except (OSError, subprocess.SubprocessError) as error:
        try:
            os.unlink(part)
        except OSError:
            pass
        return str(error)
    finally:
        os.close(fd)
    return add_mmproj(d.section, d.target)


# --- Pi ---------------------------------------------------------------------------------------------

def thinking_switch(model: str) -> bool:
    """llama.cpp's chat template of this model has the enable_thinking switch."""
    import gpu_usage
    props = gpu_usage.http_json(local_hold.port_of("llamacpp"), f"/props?model={quote(model)}", timeout=120)
    template = props.get("chat_template") if isinstance(props, dict) else None
    return isinstance(template, str) and "enable_thinking" in template


def pi_entry_fixed(entry: dict, switch: bool) -> bool:
    """Pictures on, and the thinking switch where the template has one. True when it changed."""
    changed = False
    if "image" not in (entry.get("input") or []):
        entry["input"] = ["text", "image"]
        changed = True
    compat = entry.get("compat") if isinstance(entry.get("compat"), dict) else {}
    if switch and not compat.get("thinkingFormat"):
        entry["reasoning"] = True
        entry["compat"] = {**compat, **THINKING_SWITCH}
        changed = True
    return changed


def fix_pi(server: str, model: str, path: Path = PI_MODELS, switch=thinking_switch) -> bool:
    """Pi's entry for this local model sends pictures and can be asked not to think. True when changed."""
    provider = PROVIDER_OF.get(server)
    try:
        data = json.loads(_read(path) or "{}")
    except ValueError:
        return False
    entries = ((data.get("providers") or {}).get(provider) or {}).get("models") if isinstance(data, dict) else None
    mine = [e for e in entries or () if isinstance(e, dict) and str(e.get("id", "")).lower() == model.lower()]
    if not mine:
        return False
    has_switch = server == "llamacpp" and any(not (e.get("compat") or {}).get("thinkingFormat") for e in mine) \
        and switch(model)
    changed = [pi_entry_fixed(e, has_switch) for e in mine]
    if not any(changed):
        return False
    backup(path)
    _write(path, json.dumps(data, indent=2) + "\n")
    return True


# --- the test picture ------------------------------------------------------------------------------

def probe_png(path: str, size: int = 96) -> str:
    """A red square on white, as a PNG (written here: no picture library needed)."""
    edge = size // 4
    rows = b"".join(b"\0" + b"".join(b"\xd0\x10\x10" if edge <= x < size - edge and edge <= y < size - edge
                                     else b"\xff\xff\xff" for x in range(size)) for y in range(size))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
                           + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
    return path


def probe(command: str, runner=None, model: str = "") -> agent_run.Result:
    folder = tempfile.mkdtemp(prefix="ai-playground-probe-")
    try:
        return agent_run.ask(command, PROBE_PROMPT, [probe_png(os.path.join(folder, "probe.png"))], level="quick",
                             think=False, timeout=120, runner=runner, model=model)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


# --- the check -------------------------------------------------------------------------------------

def check(agent, fresh: bool = False, local=None, model: str = "") -> Result:
    """Whether `agent` (name, command) reads frames with its local model; set up what can be.
    local, model: another local model (server key, name) than its own, asked with --model `model`
    (the Analyze tab)."""
    used = local if model else gpu_check.agent_model(agent.command)
    if used is None:
        return Result("cloud")
    key = (agent.command, *used, model)
    with _one_at_a_time:
        when, result = _cache.get(key, (0.0, None))
        if not fresh and result is not None and (result.state == "ready" or time.monotonic() - when < FORGET_NO):
            return result
        result = _check(agent, *used, model)
        if result.state in ("ready", "no"):
            _cache[key] = (time.monotonic(), result)
        return result


def forget(agent) -> None:
    for key in [k for k in _cache if k[0] == agent.command]:
        del _cache[key]


def no(agent, model: str, why: str, done=()) -> Result:
    return Result("no", _("{0} cannot read the frames with {1}: {2}.").format(agent.name, model, why), done=list(done))


def _check(agent, server: str, model: str, model_arg: str = "") -> Result:
    done = []
    if server == "llamacpp" and local_hold.sees_pictures(server, model) is not True:
        section = section_of(model)
        path = model_file(model)
        if section is None or path is None:
            return no(agent, model, _("its file or its section in llama.cpp's models.ini was not found"))
        mmproj = local_mmproj(path)
        if mmproj is None:
            download = hf_mmproj(repo_of(path), path.parent, section) if repo_of(path) else None
            if download is None:
                return no(agent, model, _("it has no vision file (mmproj), and none was found for it"))
            return Result("download", _("{0} needs its vision file to see pictures: {1} ({2:.1f} GB) from "
                                        "{3}.").format(model, download.path, download.size / 1024 ** 3, download.repo),
                          download=download)
        why = add_mmproj(section, mmproj)
        if why:
            return no(agent, model, why)
        done.append(_("the vision file in llama.cpp's models.ini"))
    with local_hold.Hold(server) as hold:
        if hold.error:
            return no(agent, model, _("the local LLM did not start ({0})").format(hold.error), done)
        if server in ("ollama", "lmstudio") and local_hold.sees_pictures(server, model) is False:
            return no(agent, model, _("{0} says this model does not see pictures; choose a vision model "
                                      "there").format({"ollama": "Ollama", "lmstudio": "LM Studio"}[server]), done)
        if agent.command == "pi" and fix_pi(server, model):
            done.append(_("Pi's settings for this model (pictures, thinking off)"))
        answer = probe(agent.command, model=model_arg)
    if "red" not in answer.text.lower():
        return no(agent, model, _("it did not answer the test picture ({0})").format(
            answer.error or answer.text[:80] or _("no answer")), done)
    text = (_("{0} reads pictures with {1}: about {2:.0f} s per frame.") if answer.seconds < SLOW
            else _("{0} reads pictures with {1}, but slowly: about {2:.0f} s per frame.")).format(
        agent.name, model, answer.seconds)
    if done:
        text += " " + _("Set up for it: {0}.").format("; ".join(done))
    return Result("ready", text, answer.seconds, done)
