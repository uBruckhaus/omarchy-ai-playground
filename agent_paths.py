"""Where an agent's program is: on PATH, else in the usual folders a user installs agents into
(mise, npm, bun, cargo, deno, volta, asdf, Homebrew), which an app started from the desktop often
does not have on its PATH. Only regular, executable files owned by the user or root are taken,
in folders no other account can change, as a plain file or through one link straight to it.

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


def _closed(folder: Path) -> bool:
    """`folder` and every folder above it: no link, owned by this user or root, changeable only by
    its owner (a root-owned sticky folder like /tmp counts as closed: nobody can rename or remove
    what belongs to someone else there)."""
    for part in [folder, *folder.parents]:
        try:
            info = os.lstat(part)
        except OSError:
            return False
        if stat.S_ISLNK(info.st_mode) or info.st_uid not in (os.getuid(), 0):
            return False
        if info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX):
            return False
    return True


def _program(path: Path) -> bool:
    """A regular, executable file of this user or root in closed folders, reached without a link."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return (stat.S_ISREG(info.st_mode) and info.st_mode & 0o111 != 0 and info.st_uid in (os.getuid(), 0)
            and not info.st_mode & 0o022 and _closed(path.parent))


def _usable(path: Path) -> bool:
    """The program as a plain file, or one link (of this user or root) straight to such a file; links
    reached through another link are never followed, so nothing can be swapped in between."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if not stat.S_ISLNK(info.st_mode):
        return _program(path)
    if info.st_uid not in (os.getuid(), 0) or not _closed(path.parent):
        return False
    try:
        written = os.readlink(path)
    except OSError:
        return False
    # Taken as written: no "..", no "." (the kernel would resolve each part before it), so the path
    # checked is exactly the path the start goes through.
    if any(part in ("..", ".") for part in written.split("/")):
        return False
    return _program(Path(path.parent, written))


def absolute_path(path_value: str | None) -> str:
    """PATH without relative entries (they would mean something else in the private folder an agent
    is started in). Agents on PATH are trusted as the user's own shell trusts them."""
    return os.pathsep.join(p for p in (path_value or "").split(os.pathsep) if os.path.isabs(p))


def on_path(name: str, path_value: str | None = None) -> str | None:
    """The program's absolute path as PATH finds it (absolute entries only), or None."""
    found = shutil.which(name, path=absolute_path(os.environ.get("PATH") if path_value is None else path_value))
    return os.path.abspath(found) if found else None


def find(name: str, home: Path | None = None) -> str | None:
    """The program's path: as PATH finds it, else from the usual install folders; None when not there."""
    if not name or "/" in name:
        return None
    found = on_path(name)
    if found:
        return found
    home = Path.home() if home is None else home
    for folder in [home / d for d in USER_DIRS] + [Path(d) for d in SYSTEM_DIRS]:
        candidate = folder / name
        if _usable(candidate):
            return str(candidate)
    return None


def resolve(argv: list, env: dict | None = None, home: Path | None = None) -> tuple:
    """(argv, env) to start it with: the program always by the absolute path that was checked (PATH's
    absolute entries first, else an install folder, which then goes in front of PATH), so the start
    makes no second lookup."""
    env = dict(os.environ if env is None else env)
    if not argv or "/" in argv[0]:
        return list(argv), env
    found = on_path(argv[0], env.get("PATH", ""))
    if found:
        return [found] + list(argv[1:]), env
    found = find(argv[0], home)
    if found is None:
        return list(argv), env
    env["PATH"] = os.pathsep.join(p for p in (str(Path(found).parent), env.get("PATH", "")) if p)
    return [found] + list(argv[1:]), env
