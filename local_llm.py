"""AI Playground's Local LLMs tab: which local LLM servers run here, their models, and one
question to a model with its time and tokens per second.

Every server is asked over its OpenAI-compatible API on 127.0.0.1 at its usual port, and only
when the socket listening there belongs to this user (loopback.require_own_listener): another
account could have put a program on that port to read the question. Requests go through
netfetch (curl, bounded in size and time, no redirects).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

import loopback
import netfetch

# key, name, usual port (the providers' defaults)
SERVERS = (("llamacpp", "llama.cpp", 8080), ("ollama", "Ollama", 11434), ("lmstudio", "LM Studio", 1234),
           ("vllm", "vLLM", 8000), ("jan", "Jan", 1337), ("koboldcpp", "KoboldCpp", 5001))
MAX_MODELS = 200
MAX_ANSWER = 1024 * 1024


@dataclass
class Server:
    key: str
    name: str
    port: int
    models: tuple = ()
    error: str = ""


@dataclass
class Answer:
    text: str = ""
    seconds: float = 0.0
    tokens_in: int | None = None
    tokens_out: int | None = None
    error: str = ""

    @property
    def per_second(self) -> float | None:
        if self.tokens_out and self.seconds > 0:
            return self.tokens_out / self.seconds
        return None


def model_ids(data) -> tuple:
    """The model ids of a /v1/models answer ({"data": [{"id": …}, …]})."""
    items = data.get("data") if isinstance(data, dict) else None
    ids = [item["id"] for item in items or () if isinstance(item, dict) and isinstance(item.get("id"), str)]
    return tuple(i for i in ids if 0 < len(i) <= 300 and i.isprintable())[:MAX_MODELS]


def running(listeners=loopback.loopback_listeners) -> list[Server]:
    """The servers whose port is listened on by this user (blocking: off the main thread)."""
    import os
    found = []
    for key, name, port in SERVERS:
        owners = listeners(port)
        if not owners:
            continue
        server = Server(key, name, port)
        if any(uid != os.getuid() for uid in owners):
            server.error = "another account"
        else:
            try:
                server.models = model_ids(netfetch.fetch_json(f"http://127.0.0.1:{port}/v1/models",
                                                              max_bytes=MAX_ANSWER, timeout=5, local=True))
            except (netfetch.FetchError, ValueError) as error:
                server.error = str(error)[:200]
        found.append(server)
    return found


def chat_answer(data) -> Answer:
    """The text and token counts of a /v1/chat/completions answer."""
    if not isinstance(data, dict):
        return Answer(error="no answer")
    choices = data.get("choices") if isinstance(data.get("choices"), list) else []
    message = choices[0].get("message") if choices and isinstance(choices[0], dict) else None
    text = message.get("content") if isinstance(message, dict) else None
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}

    def count(key):
        value = usage.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
    return Answer(text=text.strip() if isinstance(text, str) else "", tokens_in=count("prompt_tokens"),
                  tokens_out=count("completion_tokens"))


def ask(port: int, model: str, prompt: str, timeout: float = 180) -> Answer:
    """One question to one model (blocking). Its time includes loading the model if it is not
    loaded yet."""
    try:
        loopback.require_own_listener(port)
    except (ConnectionError, PermissionError) as error:
        return Answer(error=str(error))
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                       "stream": False}).encode()
    start = time.monotonic()
    try:
        raw = netfetch.fetch(f"http://127.0.0.1:{port}/v1/chat/completions", max_bytes=MAX_ANSWER,
                             timeout=timeout, local=True, headers={"Content-Type": "application/json"},
                             data=body, method="POST")
        answer = chat_answer(json.loads(raw))
    except (netfetch.FetchError, ValueError) as error:
        answer = Answer(error=str(error)[:300])
    answer.seconds = time.monotonic() - start
    return answer
