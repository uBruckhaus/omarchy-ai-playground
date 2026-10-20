"""Before AI Playground uses two or more local models at once: do they fit in the free GPU memory?

A run that asks several local models together (two models on the Local LLMs tab, or agents that
each use a local LLM) loads each of them unless it is loaded already. Each needs about its file
size plus room for its context; when the sum is more than the GPU has free, loading fails or the
computer stalls. The tabs then offer to ask one after the other.

- free GPU memory: gpu_usage.gpu_totals() (sysfs on AMD, else nvidia-smi);
- loaded models: the servers' own APIs (gpu_usage.loaded_models, own listeners only); a loaded
  model needs nothing more;
- a model's size: Ollama's /api/tags, else the .gguf file found in the usual model folders (and
  the models folder set in AI Agents Map or Asterism), read only.

Blocking (file system and loopback, bounded): call it off the main thread.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import loopback
import local_llm

HOME = Path.home()
GIB = 1024 ** 3
CONTEXT_ROOM = GIB // 2          # each model's context and buffers besides its weights (a cautious guess)
WEIGHT_FACTOR = 1.1              # the weights on the GPU are a little more than the file
MAX_FILES = 20_000               # files looked at per model folder
# The local LLM provider ids in agent settings (providers.Provider.connect_key) -> server key.
PROVIDER_KEYS = {"llama-local": "llamacpp", "ollama-local": "ollama", "lmstudio-local": "lmstudio",
                 "vllm-local": "vllm", "jan-local": "jan", "koboldcpp-local": "koboldcpp"}
SETTINGS_FILES = (HOME / ".config/omarchy/ai-agents-map/settings.json", HOME / ".config/asterism/settings.json")
MODEL_FOLDERS = (HOME / ".lmstudio/models", HOME / ".cache/lm-studio/models", HOME / ".cache/llama.cpp",
                 HOME / "models", HOME / "Models", HOME / ".local/share/jan/models")


@dataclass
class Need:
    label: str                   # what the dialog names: the model, or "Pi: <model>"
    server: str                  # local_llm server key
    model: str
    size: int | None = None      # bytes of its weights; None: not found
    loaded: bool = False

    @property
    def bytes_needed(self) -> int:
        if self.loaded or self.size is None:
            return 0
        return int(self.size * WEIGHT_FACTOR) + CONTEXT_ROOM


@dataclass
class Check:
    needs: list = field(default_factory=list)
    free: int = 0
    total: int = 0

    @property
    def needed(self) -> int:
        return sum(n.bytes_needed for n in self.needs)

    @property
    def unknown(self) -> list:
        return [n for n in self.needs if not n.loaded and n.size is None]

    @property
    def fits(self) -> bool | None:
        """True: fits; False: does not; None: cannot tell (no GPU figures, or a size not found)."""
        if not self.total:
            return None
        if self.needed > self.free:
            return False
        return None if self.unknown else True


def agent_model(command: str):
    """(server key, model name) of the local LLM the agent uses now, else None."""
    try:
        import agent_details
        details = agent_details.details_for(command)
    except Exception:
        return None
    text = (details.model or "").strip()
    if not text.endswith(")") or " (" not in text:
        return None
    model, _sep, provider = text[:-1].rpartition(" (")
    server = PROVIDER_KEYS.get(provider.strip())
    return (server, model.strip()) if server and model.strip() else None


def _models_root() -> list:
    roots = []
    for path in SETTINGS_FILES:
        try:
            with open(path, "rb") as handle:
                data = json.loads(handle.read(256 * 1024) or b"{}")
        except (OSError, ValueError):
            continue
        root = data.get("models_root") if isinstance(data, dict) else None
        if isinstance(root, str) and root.startswith(("/", "~")):
            roots.append(Path(os.path.expanduser(root)))
    return roots


SPLIT = re.compile(r"^(.*)-\d{5}-of-\d{5}$")        # model-00001-of-00003.gguf: one part of a model


def gguf_sizes(roots=None) -> dict:
    """lower-case model name (the file name without .gguf) -> size in bytes, of the weights files
    in the model folders (mmproj files left out; a split model counts all its parts; a file reached
    through links in several folders counts once)."""
    parts = {}                                              # name -> {real path: size}
    for root in roots if roots is not None else _models_root() + list(MODEL_FOLDERS):
        if not root.is_dir():
            continue
        seen = 0
        for folder, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if not d.startswith(".")][:500]
            for name in files:
                seen += 1
                lower = name.lower()
                if seen > MAX_FILES or not lower.endswith(".gguf") or lower.startswith("mmproj"):
                    continue
                path = Path(folder, name)
                try:
                    real, size = os.path.realpath(path), path.stat().st_size     # a link: its file
                except OSError:
                    continue
                stem = lower[:-5]
                split = SPLIT.match(stem)
                parts.setdefault(split.group(1) if split else stem, {})[real] = size
            if seen > MAX_FILES:
                break
    return {name: sum(files.values()) for name, files in parts.items()}


def ollama_sizes(port: int) -> dict:
    import netfetch
    try:
        loopback.require_own_listener(port)
        data = netfetch.fetch_json(f"http://127.0.0.1:{port}/api/tags", max_bytes=1024 * 1024, timeout=5,
                                   local=True)
    except (ConnectionError, PermissionError, netfetch.FetchError):
        return {}
    rows = data.get("models") if isinstance(data, dict) else None
    return {str(r["name"]).lower(): r["size"] for r in rows or () if isinstance(r, dict)
            and isinstance(r.get("name"), str) and isinstance(r.get("size"), int)}


def loaded(server: str) -> set:
    import gpu_usage
    port = dict((k, p) for k, _n, p in local_llm.SERVERS).get(server, 0)
    if not port or not loopback.loopback_listeners(port):
        return set()
    return set(gpu_usage.loaded_models(SimpleNamespace(key=server, address=f"127.0.0.1:{port}")))


def check(wanted: list, totals=None, files=None) -> Check | None:
    """wanted: [(label, server key, model)]. None when fewer than two different models are in it."""
    distinct = {(s, m.lower()) for _l, s, m in wanted}
    if len(distinct) < 2:
        return None
    import gpu_usage
    used, total, _busy = totals if totals is not None else gpu_usage.gpu_totals()
    sizes = files if files is not None else gguf_sizes()
    ollama = {}
    loaded_by_server = {}
    result = Check(free=max(0, total - used), total=total)
    done = set()
    for label, server, model in wanted:
        key = (server, model.lower())
        if key in done:
            continue
        done.add(key)
        if server not in loaded_by_server:
            loaded_by_server[server] = loaded(server) if files is None else set()
        if server == "ollama" and not ollama and files is None:
            ollama = ollama_sizes(11434)
        name = model.lower()
        size = (ollama.get(name) if server == "ollama" else None) or sizes.get(name) \
            or sizes.get(name.removesuffix(".gguf"))
        result.needs.append(Need(label, server, model, size, name in loaded_by_server[server]))
    return result


def unload(server: str, model: str) -> None:
    """Make room after an answer, where the server can be asked to (Ollama: keep_alive 0)."""
    if server != "ollama":
        return
    import netfetch
    try:
        loopback.require_own_listener(11434)
        netfetch.fetch("http://127.0.0.1:11434/api/generate", max_bytes=65536, timeout=30, local=True,
                       headers={"Content-Type": "application/json"},
                       data=json.dumps({"model": model, "keep_alive": 0}).encode(), method="POST")
    except (ConnectionError, PermissionError, netfetch.FetchError):
        pass
