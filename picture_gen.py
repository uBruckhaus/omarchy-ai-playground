"""Pictures from text for AI Playground's Generate tab, made on this computer's GPU with
stable-diffusion.cpp (sd-cli, its Vulkan build) and the Z-Image-Turbo model.

Setting up downloads two things, each checked against its published SHA-256 before it is kept:

- stable-diffusion.cpp's newest Linux Vulkan release from GitHub (the release lists a digest for
  every asset; without one nothing is downloaded), unpacked into <data>/sd/<tag>/ (flat files only);
- the model: Z-Image-Turbo (GGUF), its VAE and the Qwen3 4B text encoder from Hugging Face, pinned
  here by size and SHA-256 (all Apache-2.0, none gated), into <data>/models/z-image-turbo/.

Nothing goes into the models folder of a local LLM: a GGUF there would be taken for a chat model.
Each download goes to `<name>.part` opened exclusively and never through a link (as install_ctl
does it), curl writing to that open file; it becomes the real name only when its bytes match.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

HOME = Path.home()
DATA = Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local/share") / "ai-playground"
RELEASES = "https://api.github.com/repos/leejet/stable-diffusion.cpp/releases/latest"
ASSET_RE = re.compile(r"^sd-[A-Za-z0-9.-]+-bin-Linux-Ubuntu-[0-9.]+-x86_64-vulkan\.zip$")
TAG_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
MAX_ZIP = 512 * 1024 * 1024
MAX_UNPACKED = 1024 * 1024 * 1024
HF = "https://huggingface.co"
MODEL_DIR = "z-image-turbo"
# (repository, path in it, size, SHA-256): checked 2026-10-20.
MODEL_FILES = (
    ("leejet/Z-Image-Turbo-GGUF", "z_image_turbo-Q8_0.gguf", 6577440704,
     "df1c5baa86d1398c979495a6072dbcee79444fdb884a2445582ba0769c44e9a1"),
    ("Comfy-Org/z_image_turbo", "split_files/vae/ae.safetensors", 335304388,
     "afc8e28272cd15db3919bacdb6918ce9c1ed22e96cb12c4d5ed0fba823529e38"),
    ("unsloth/Qwen3-4B-Instruct-2507-GGUF", "Qwen3-4B-Instruct-2507-Q8_0.gguf", 4280405600,
     "391c1e410fd9f4cf2de2b510273b56a84c19ce18f4fa3bfb3774031dac4ef068"),
)
SIZES = {"square": (1024, 1024), "landscape": (1280, 768), "portrait": (768, 1280)}
STEPS = 8                        # Z-Image-Turbo is made for 8 steps without guidance
MAX_PROMPT = 2000
GENERATE_TIMEOUT = 15 * 60
PROGRESS_RE = re.compile(rb"\|\s*(\d+)/(\d+)\s*[-|]")


class Cancelled(Exception):
    pass


_RUNNING: set = set()            # curl and sd-cli processes started here (stop_all ends them)


def stop_all() -> None:
    """End every download and picture still running (the app is closing: none outlives it)."""
    for proc in list(_RUNNING):
        if proc.poll() is None:
            proc.kill()


@dataclass
class Status:
    binary: Path | None          # sd-cli, when set up
    missing: list                # model files not there yet: [(repo, path, size, sha256)]

    @property
    def ready(self) -> bool:
        return self.binary is not None and not self.missing

    @property
    def to_download(self) -> int:
        """Bytes still to download (the program's ~40 MB not counted)."""
        return sum(f[2] for f in self.missing)


def own_regular_file(path: Path) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and st.st_nlink == 1


def model_path(entry, data: Path = None) -> Path:
    return (data or DATA) / "models" / MODEL_DIR / Path(entry[1]).name


def binary(data: Path = None) -> Path | None:
    """sd-cli of the release set up last (the `current` link names its folder)."""
    link = (data or DATA) / "sd" / "current"
    try:
        tag = os.readlink(link)
    except OSError:
        return None
    if not TAG_RE.fullmatch(tag):
        return None
    path = link.parent / tag / "sd-cli"
    return path if own_regular_file(path) and os.access(path, os.X_OK) else None


def status(data: Path = None) -> Status:
    missing = [f for f in MODEL_FILES
               if not (own_regular_file(model_path(f, data)) and model_path(f, data).stat().st_size == f[2])]
    return Status(binary(data), missing)


# --- downloading ---------------------------------------------------------------------------------

def _open_staging(partial: Path) -> int:
    flags = os.O_RDWR | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        return os.open(partial, flags | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        pass
    fd = os.open(partial, flags)                    # a link there fails (ELOOP)
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_nlink != 1:
        os.close(fd)
        raise RuntimeError(f"{partial.name} is not this account's own download; remove it and try again")
    return fd


def _sha256_of_fd(fd: int) -> str:
    digest = hashlib.sha256()
    offset = 0
    while block := os.pread(fd, 4 * 1024 * 1024, offset):
        digest.update(block)
        offset += len(block)
    return digest.hexdigest()


def download(url: str, target: Path, size: int, sha256: str, progress, cancelled, done_before: int = 0,
             total: int = 0) -> None:
    """curl url into target (resuming an own `.part`), kept only when size and SHA-256 match.
    progress(fraction) as it goes; cancelled() stops it (the .part stays, to resume)."""
    if not re.fullmatch(r"[0-9a-f]{64}", sha256 or "") or size <= 0:
        raise RuntimeError("no checksum: nothing is downloaded")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    fd = _open_staging(partial)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)     # one download per file at a time
        except OSError:
            raise RuntimeError(f"{target.name} is being downloaded already") from None
        have = os.fstat(fd).st_size
        if have > size:
            os.ftruncate(fd, 0)
            have = 0
        argv = ["curl", "-fsSL", "--proto", "=https", "--proto-redir", "=https", "--max-redirs", "5",
                "--max-filesize", str(size)]
        if have:
            argv += ["-C", str(have)]
        if have < size:
            proc = subprocess.Popen([*argv, "--", url], stdin=subprocess.DEVNULL, stdout=fd,
                                    stderr=subprocess.PIPE)
            _RUNNING.add(proc)
            try:
                while proc.poll() is None:
                    if cancelled():
                        raise Cancelled()
                    have = os.fstat(fd).st_size
                    if have > size:
                        raise RuntimeError(f"{target.name} is larger than announced")
                    progress((done_before + have) / max(1, total or size))
                    time.sleep(0.4)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
                _RUNNING.discard(proc)
            if proc.returncode != 0:
                raise RuntimeError(f"download failed: {proc.stderr.read(400).decode('utf-8', 'replace').strip()}")
        st = os.fstat(fd)
        if st.st_size != size:
            raise RuntimeError(f"{target.name}: download incomplete")
        if _sha256_of_fd(fd) != sha256:
            os.ftruncate(fd, 0)
            raise RuntimeError(f"{target.name} does not match its checksum; it was not kept")
        os.fsync(fd)
        now = os.lstat(partial)
        if (now.st_dev, now.st_ino) != (st.st_dev, st.st_ino):
            raise RuntimeError(f"{partial.name} was replaced while downloading; nothing was kept")
        os.replace(partial, target)
    finally:
        os.close(fd)


def latest_release() -> tuple:
    """(tag, asset name, url, size, sha256) of the newest Linux Vulkan build."""
    out = subprocess.run(["curl", "-fsSL", "--proto", "=https", "--max-time", "20", "--max-filesize", "4000000",
                          "-H", "Accept: application/vnd.github+json", RELEASES],
                         capture_output=True, timeout=30, stdin=subprocess.DEVNULL)
    if out.returncode != 0:
        raise RuntimeError("GitHub could not be reached")
    data = json.loads(out.stdout.decode("utf-8", "replace"))
    tag = data.get("tag_name") if isinstance(data, dict) else None
    if not isinstance(tag, str) or not TAG_RE.fullmatch(tag):
        raise RuntimeError("unexpected release list")
    for asset in data.get("assets") or ():
        name = asset.get("name") if isinstance(asset, dict) else None
        if not isinstance(name, str) or not ASSET_RE.fullmatch(name):
            continue
        digest = asset.get("digest") or ""
        url = asset.get("browser_download_url") or ""
        size = asset.get("size") or 0
        if not digest.startswith("sha256:") or not url.startswith("https://github.com/") \
                or not isinstance(size, int) or not 0 < size <= MAX_ZIP:
            raise RuntimeError("the release lists no checksum for its Linux build")
        return tag, name, url, size, digest[7:]
    raise RuntimeError("the release has no Linux Vulkan build")


def unpack(archive: Path, folder: Path) -> None:
    """The release zip: flat, plain files only (no folders, links or odd names), bounded in size."""
    folder.mkdir(parents=True, exist_ok=True)
    total = 0
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            name = info.filename
            if info.is_dir() or "/" in name or "\\" in name or name.startswith(".") \
                    or not re.fullmatch(r"[A-Za-z0-9._+-]{1,128}", name):
                raise RuntimeError(f"unexpected entry in the release: {name[:80]}")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise RuntimeError(f"a link in the release: {name}")
            total += info.file_size
            if total > MAX_UNPACKED:
                raise RuntimeError("the release unpacks too large")
            fd = os.open(folder / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o755 if name in ("sd-cli", "sd-server") else 0o644)
            with os.fdopen(fd, "wb") as out, zf.open(info) as src:
                shutil.copyfileobj(src, out, 1024 * 1024)


def install_program(progress, cancelled, data: Path = None) -> str:
    """stable-diffusion.cpp's newest Vulkan build into <data>/sd/<tag> (the tag)."""
    data = data or DATA
    tag, name, url, size, sha256 = latest_release()
    root = data / "sd"
    if (root / tag / "sd-cli").is_file() and binary(data) == root / tag / "sd-cli":
        return tag
    archive = root / name
    download(url, archive, size, sha256, progress, cancelled)
    staging = root / f".{tag}.partial"
    shutil.rmtree(staging, ignore_errors=True)
    try:
        unpack(archive, staging)
        shutil.rmtree(root / tag, ignore_errors=True)
        os.replace(staging, root / tag)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        archive.unlink(missing_ok=True)
    link = root / "current"
    fresh = root / ".current.new"
    fresh.unlink(missing_ok=True)
    fresh.symlink_to(tag)
    os.replace(fresh, link)
    return tag


def install_model(progress, cancelled, data: Path = None) -> None:
    missing = status(data).missing
    total = sum(f[2] for f in missing)
    done = 0
    for entry in missing:
        repo, path, size, sha256 = entry
        url = f"{HF}/{repo}/resolve/main/{path}"
        download(url, model_path(entry, data), size, sha256, progress, cancelled, done, total)
        done += size


# --- making a picture ----------------------------------------------------------------------------

def build(prompt: str, out: Path, size: str = "square", seed: int = -1, data: Path = None) -> list:
    """sd-cli's argv for one picture (the prompt is one argument after -p: never an option)."""
    exe = binary(data)
    if exe is None:
        raise RuntimeError("stable-diffusion.cpp is not set up")
    prompt = " ".join((prompt or "").split())[:MAX_PROMPT]
    if not prompt:
        raise ValueError("empty prompt")
    width, height = SIZES.get(size, SIZES["square"])
    diffusion, vae, llm = (model_path(f, data) for f in MODEL_FILES)
    return [str(exe), "--diffusion-model", str(diffusion), "--vae", str(vae), "--llm", str(llm),
            "--cfg-scale", "1.0", "--steps", str(STEPS), "-W", str(width), "-H", str(height),
            "-s", str(int(seed)), "--offload-to-cpu", "--flash-attn", "off,diffusion=on",
            "-o", str(out), "-p", prompt]


def generate(prompt: str, out: Path, progress, cancelled, size: str = "square", seed: int = -1,
             data: Path = None) -> float:
    """Make one picture at out (PNG). progress(fraction, text); returns the seconds it took.
    Raises Cancelled, or RuntimeError with the end of sd-cli's output."""
    argv = build(prompt, out, size, seed, data)
    start = time.monotonic()
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            cwd=str(out.parent), env={**os.environ, "NO_COLOR": "1"})
    _RUNNING.add(proc)
    tail = b""
    os.set_blocking(proc.stdout.fileno(), False)
    try:
        while True:
            chunk = proc.stdout.read(65536)
            if chunk:
                tail = (tail + chunk)[-8192:]
                found = PROGRESS_RE.findall(tail)
                if found:
                    step, steps = (int(x) for x in found[-1])
                    progress(min(1.0, step / max(1, steps)), f"{step}/{steps}")
            elif proc.poll() is not None:
                break
            else:
                time.sleep(0.2)
            if cancelled():
                raise Cancelled()
            if time.monotonic() - start > GENERATE_TIMEOUT:
                raise RuntimeError("it took longer than 15 minutes")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        _RUNNING.discard(proc)
    if proc.returncode != 0 or not out.is_file():
        last = [line for line in tail.decode("utf-8", "replace").splitlines() if line.strip()][-3:]
        raise RuntimeError(" / ".join(last)[:400] or f"sd-cli ended with {proc.returncode}")
    return time.monotonic() - start
