"""A local LLM kept up for a whole job (a chess video read frame by frame), and whether a local model
sees pictures.

Each question to an agent is a run of its own (`pi -p …`). Between two runs no agent is running, so
the provider watcher (provider_watch.py) stops the local LLM a few seconds after each one, and the
next run starts it again: every frame then waits for the server and its model to load, and the
agent's first request fails ("Connection error") before it is up. Hold starts the server before the
first question, waits until it answers, and keeps one connection to it open (a light request every
PING seconds) until the job is done: the watcher leaves a server alone while another program is
connected to it. Afterwards the watcher stops it as before; one that Hold started itself (no
agent ran, so the watcher never took it on) Hold stops, unless another program is connected to it.

Pictures: llama.cpp's router sees them when the model's section in ~/.config/llama.cpp/models.ini
names an mmproj file; Ollama says so in /api/show (capabilities), LM Studio in /api/v0/models
(type vlm). Unknown elsewhere.

Only this user's own servers on loopback (loopback, gpu_usage.http_json). Blocking: call it off the
main thread (sees_pictures is quick: a file, or one request with a short timeout).
"""

from __future__ import annotations

import http.client
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import loopback
import local_llm

HOME = Path.home()
MODELS_INI = HOME / ".config/llama.cpp/models.ini"
MAX_INI = 1024 * 1024
START_WAIT = 120             # seconds a server may take to answer after it was started
PING = 2.0                   # seconds between two requests on the held connection (llama.cpp closes idle ones after 5)
SERVICES = {"llamacpp": "llama-server", "ollama": "ollama", "vllm": "vllm", "jan": "jan", "koboldcpp": "koboldcpp"}
READY = {"llamacpp": "/health", "ollama": "/api/version", "koboldcpp": "/api/v1/model"}   # else /v1/models
UNIT_DIRS = (HOME / ".config/systemd/user", Path("/etc/systemd/user"), Path("/usr/lib/systemd/user"))


def port_of(server: str) -> int:
    return dict((key, port) for key, _name, port in local_llm.SERVERS).get(server, 0)


def service_of(server: str) -> str:
    """The user service that starts this server ("" when there is none)."""
    name = SERVICES.get(server, "")
    return name if name and any((folder / f"{name}.service").is_file() for folder in UNIT_DIRS) else ""


def ready(server: str, port: int) -> bool:
    import gpu_usage
    return gpu_usage.http_json(port, READY.get(server, "/v1/models"), timeout=2) is not None


def runtime(server: str) -> SimpleNamespace:
    return SimpleNamespace(key=server, service=service_of(server), installed=True, address=f"127.0.0.1:{port_of(server)}")


def clients(port: int, proc: str = "/proc/net") -> int:
    """Open connections to this loopback port (ESTABLISHED, seen from the client's side)."""
    count = 0
    for name in ("tcp", "tcp6"):
        try:
            lines = Path(proc, name).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) > 3 and fields[3] == "01" and fields[2].rpartition(":")[2] == f"{port:04X}":
                count += 1
    return count


def start(server: str, wait: float = START_WAIT, clock=time.monotonic, sleep=time.sleep, started=None) -> str:
    """Start the server unless it runs, and wait until it answers. "" when it does, else why not;
    `started` (a list) gets True when it was started here."""
    import runtime_ctl
    port = port_of(server)
    if not port:
        return "unknown local LLM"
    try:
        loopback.require_own_listener(port)
    except PermissionError as error:
        return str(error)
    except ConnectionError:
        rt = runtime(server)
        if not runtime_ctl.can_control(rt):
            return "it is not running and cannot be started from here"
        ok, why = runtime_ctl.start(rt)
        if not ok:
            return why or "it did not start"
        if started is not None:
            started.append(True)
    until = clock() + wait
    while not (loopback.loopback_listeners(port) and ready(server, port)):
        if clock() > until:
            return "it did not answer"
        sleep(0.5)
    try:
        loopback.require_own_listener(port)
    except (ConnectionError, PermissionError) as error:
        return str(error)
    return ""


class Hold:
    """with Hold(server) as hold: … — hold.error says why the server is not up ("" when it is)."""

    def __init__(self, server: str):
        self.server, self.port = server, port_of(server)
        self.error = ""
        self.done = threading.Event()
        self.thread = None
        self.started = []            # [True]: started here, so stopped here too

    def __enter__(self):
        self.error = start(self.server, started=self.started)
        if not self.error:
            self.thread = threading.Thread(target=self.keep, daemon=True)
            self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.done.set()
        if self.thread is not None:
            self.thread.join(timeout=PING + 3)
        if self.started and not clients(self.port):
            import runtime_ctl
            runtime_ctl.stop(runtime(self.server))
        return False

    def keep(self):
        path = READY.get(self.server, "/v1/models")
        connection = None
        while not self.done.is_set():
            try:
                if connection is None:
                    loopback.require_own_listener(self.port)
                    connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
                connection.request("GET", path)
                connection.getresponse().read(64 * 1024)
            except (OSError, http.client.HTTPException, ConnectionError, PermissionError):
                if connection is not None:
                    connection.close()
                connection = None
            self.done.wait(PING)
        if connection is not None:
            connection.close()


def ini_sections(text: str) -> dict:
    """{section name lowered: its body} of llama.cpp's models.ini."""
    return {name.strip().lower(): body for name, body in
            re.findall(r"^\[([^\]\n]{1,120})\]\n((?:(?!\[).*\n?)*)", text, re.MULTILINE)}


def sees_pictures(server: str, model: str, ini: Path = MODELS_INI) -> bool | None:
    """True or False when it can be told whether this local model reads pictures, else None."""
    import gpu_usage
    name = model.strip()
    if server == "llamacpp":
        try:
            with open(ini, "rb") as handle:
                text = handle.read(MAX_INI).decode("utf-8", "replace")
        except OSError:
            return None
        body = ini_sections(text).get(name.lower().removesuffix(".gguf"))
        if body is None:
            return None
        return bool(re.search(r"^\s*mmproj\s*=\s*\S", body, re.MULTILINE))
    if server == "ollama":
        reply = gpu_usage.http_json(port_of(server), "/api/show", "POST", {"model": name}, timeout=2)
        caps = reply.get("capabilities") if isinstance(reply, dict) else None
        return "vision" in caps if isinstance(caps, list) else None
    if server == "lmstudio":
        reply = gpu_usage.http_json(port_of(server), "/api/v0/models", timeout=2)
        for row in (reply.get("data") or []) if isinstance(reply, dict) else ():
            if isinstance(row, dict) and str(row.get("id", "")).lower() == name.lower():
                return row.get("type") == "vlm" if row.get("type") in ("vlm", "llm") else None
    return None
