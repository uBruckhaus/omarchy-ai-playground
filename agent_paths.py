"""Where an agent's program is: on PATH, else in the usual folders a user installs agents into
(mise, npm, bun, cargo, deno, volta, asdf, Homebrew), which an app started from the desktop often
does not have on its PATH. Only regular, executable files owned by the user or root are taken.

resolve() gives the argv to start and the environment to start it in: the program by its full path,
and its folder put in front of PATH, because shims (mise, asdf) call other programs from there.
"""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

USER_DIRS = (".local/bin", ".local/share/mise/shims", ".npm-global/bin", ".bun/bin", ".cargo/bin", ".deno/bin",
             ".volta/bin", ".asdf/shims")
SYSTEM_DIRS = ("/home/linuxbrew/.linuxbrew/bin",)


def _usable(path: Path) -> bool:
    try:
        info = path.stat()
    except OSError:
        return False
    return (stat.S_ISREG(info.st_mode) and os.access(path, os.X_OK)
            and info.st_uid in (os.getuid(), 0))


def find(name: str, home: Path | None = None) -> str | None:
    """The program's path: as PATH finds it, else from the usual install folders; None when not there."""
    if not name or "/" in name:
        return None
    found = shutil.which(name)
    if found:
        return found
    home = Path.home() if home is None else home
    for folder in [home / d for d in USER_DIRS] + [Path(d) for d in SYSTEM_DIRS]:
        candidate = folder / name
        if _usable(candidate):
            return str(candidate)
    return None


def resolve(argv: list, env: dict | None = None, home: Path | None = None) -> tuple:
    """(argv, env) to start it with. A program PATH finds stays as it is; one found in an install
    folder is started by its full path, with that folder in front of PATH."""
    env = dict(os.environ if env is None else env)
    if not argv or "/" in argv[0] or shutil.which(argv[0], path=env.get("PATH")):
        return list(argv), env
    found = find(argv[0], home)
    if found is None:
        return list(argv), env
    env["PATH"] = os.pathsep.join(p for p in (str(Path(found).parent), env.get("PATH", "")) if p)
    return [found] + list(argv[1:]), env
