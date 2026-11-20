"""Web links: finding yt-dlp, mpv's options for a link, links in pasted or dropped text, and keeping yt-dlp
current.

mpv plays direct stream links (.m3u8, .mp4 URLs, RTMP) by itself. Links to web pages (YouTube, Twitch and
the other sites yt-dlp supports) go through mpv's built-in ytdl_hook, which runs yt-dlp to find the
stream. YouTube also needs a JavaScript runtime to solve its challenges: a pinned Deno ships beside
yt-dlp and is passed to it explicitly.

Two copies of yt-dlp, so there is always one that was tested with this release:
  - the shipped copy, app_dir\\yt-dlp\\yt-dlp.exe (pinned in build.ps1, version in version.txt beside it),
    never modified;
  - Tessera Wall's own copy in the state folder (%LOCALAPPDATA%\\TesseraWall\\yt-dlp), started as a copy of
    the shipped one and updated from yt-dlp's stable channel at most daily, and straight away (at most
    every few hours) when a link fails to open.
Links use whichever is newer. A copy that will not run after an update is deleted. A link that still fails
is tried once with the other copy before the wall gives up on it (Wall.open_failed).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time

import config as C

log = logging.getLogger("tesserawall")
UPDATE_INTERVAL = 24 * 3600          # routine check
RECOVER_INTERVAL = 3 * 3600          # update attempts triggered by a failing link
URL = re.compile(r"^(?:https?|rtmps?|rtsp|mms)://\S+$", re.IGNORECASE)
VERSION = re.compile(r"^\d{4}\.\d{1,2}\.\d{1,2}(?:\.\d+)?$")   # stable 2026.10.26, nightly 2026.11.03.233012
_state_dir: str | None = None
_lock = threading.Lock()             # one update at a time (daily check and link recoveries)


def init(state_dir: str):
    """Where Tessera Wall's own yt-dlp, its state and Deno's cache live (set once by main)."""
    global _state_dir
    _state_dir = state_dir


def state_dir() -> str:
    return _state_dir or os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "TesseraWall")


def bundled_path() -> str:
    return os.path.join(C.app_dir(), "yt-dlp", "yt-dlp.exe")


def bundled_version() -> str | None:
    try:
        with open(os.path.join(C.app_dir(), "yt-dlp", "version.txt"), encoding="utf-8") as f:
            v = f.read().strip()
        return v if VERSION.match(v) else None
    except OSError:
        return None


def deno_path() -> str:
    return os.path.join(C.app_dir(), "yt-dlp", "deno.exe")


def own_path() -> str:
    return os.path.join(state_dir(), "yt-dlp", "yt-dlp.exe")


def state_path() -> str:
    return os.path.join(state_dir(), "ytdlp.json")


def parse_version(v) -> tuple | None:
    return tuple(int(x) for x in v.split(".")) if isinstance(v, str) and VERSION.match(v) else None


def read_state() -> dict:
    try:
        with open(state_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(**changes):
    data = read_state()
    data.update(changes)
    try:
        os.makedirs(state_dir(), exist_ok=True)
        with open(state_path(), "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError as e:
        log.info("yt-dlp: could not save state: %s", e)


def candidates() -> list[str]:
    """Every yt-dlp worth trying for a link, best first: the ytdlp_path setting alone if set; else the
    newer of Tessera Wall's own copy and the shipped copy, then the other one; else one on PATH."""
    if C.YTDLP_PATH:
        return [C.YTDLP_PATH] if os.path.isfile(C.YTDLP_PATH) else []
    shipped = bundled_path() if os.path.isfile(bundled_path()) else None
    own = own_path() if os.path.isfile(own_path()) else None
    own_v = parse_version(read_state().get("version")) if own else None
    if own and not own_v:
        own = None                                       # unknown version: not verified to run
    if shipped and own:
        shipped_v = parse_version(bundled_version())
        return [own, shipped] if shipped_v is None or own_v > shipped_v else [shipped, own]
    found = [p for p in (shipped, own) if p]
    if not found and shutil.which("yt-dlp"):
        found = [shutil.which("yt-dlp")]
    return found


def find() -> str | None:
    """The yt-dlp.exe links use now."""
    found = candidates()
    return found[0] if found else None


def in_use() -> tuple:
    """(path, version) of the yt-dlp links use now, to tell whether an update changed anything."""
    exe = find()
    if exe and os.path.normcase(exe) == os.path.normcase(own_path()):
        return exe, read_state().get("version")
    return exe, bundled_version() if exe == bundled_path() else None


def mpv_args(item: str, exe: str | None = None) -> list[str]:
    """Extra mpv options for one video: for a link, which yt-dlp to use (exe, else find()), Deno for it, and
    the highest quality to ask for (video and audio are fetched separately and joined by mpv, so no ffmpeg
    is needed). Nothing for files."""
    if "://" not in item:
        return []
    height = max(144, int(C.STREAM_MAX_HEIGHT or 1080))
    args = ["--ytdl=yes", f"--ytdl-format=bestvideo[height<=?{height}]+bestaudio/best[height<=?{height}]/best"]
    exe = exe or find()
    if exe:
        args.append(f"--script-opts-append=ytdl_hook-ytdl_path={exe}")
    if os.path.isfile(deno_path()):
        args.append(f"--ytdl-raw-options-append=js-runtimes=deno:{deno_path()}")
    return args


def mpv_env(item: str) -> dict | None:
    """Environment for a link's player (passed down to yt-dlp and Deno): Deno keeps its cache in Tessera
    Wall's state folder and never checks for Deno updates itself. None (inherit) for files."""
    if "://" not in item:
        return None
    return dict(os.environ, DENO_DIR=os.path.join(state_dir(), "deno"), DENO_NO_UPDATE_CHECK="1")


def links_from_text(text: str) -> list[str]:
    """Links and existing file/folder paths in pasted or dropped text, in order, one per line or separated
    by spaces. Quotes and angle brackets around an item are removed; anything else is ignored."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip().strip('"').strip()
        if not line:
            continue
        if os.path.exists(line):                       # a path with spaces in it, on its own line
            out.append(line)
            continue
        for token in line.split():
            token = token.strip('"\'<>')
            if URL.match(token):
                out.append(token)
            elif os.path.exists(token):
                out.append(token)
    return list(dict.fromkeys(out))


# ---- keeping Tessera Wall's own copy current ----------------------------------------------------------
def run(args: list[str], timeout: float) -> tuple[int, str]:
    """(exit code, output) of a yt-dlp command; (-1, reason) if it could not run. Replaced in tests."""
    try:
        done = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW)
        return done.returncode, (done.stdout or "") + (done.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, str(e)


def version_of(exe: str) -> str | None:
    """The version a yt-dlp.exe reports, or None if it does not run properly."""
    code, out = run([exe, "--version"], 60)
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    return lines[-1] if code == 0 and lines and VERSION.match(lines[-1]) else None


def updates_allowed() -> bool:
    """Only Tessera Wall's own copy is ever updated, never a yt-dlp the user chose, and never with update
    checks off. Running from source (no shipped copy) there is nothing to start from."""
    return bool(C.UPDATE_CHECK) and not C.YTDLP_PATH and os.path.isfile(bundled_path())


def _discard(path: str):
    for p in (path, path + ".old", path + ".new", path + ".copy"):
        try:
            os.remove(p)
        except OSError:
            pass


def refresh() -> bool:
    """Bring Tessera Wall's own copy up to date (blocking; call from a worker thread). Starts it from the
    shipped copy when missing or older (e.g. after installing a newer Tessera Wall), runs yt-dlp's own
    updater on it (stable channel; it checks the download's SHA-256 and swaps the file safely even while
    another copy is running), then checks it still runs, deleting it if not. True if the yt-dlp that links
    use (path or version) changed."""
    with _lock:
        before = in_use()
        own, shipped = own_path(), bundled_path()
        shipped_v = parse_version(bundled_version())
        own_v = parse_version(read_state().get("version")) if os.path.isfile(own) else None
        if own_v is None or (shipped_v and own_v < shipped_v):
            try:
                os.makedirs(os.path.dirname(own), exist_ok=True)
                shutil.copy2(shipped, own + ".copy")
                os.replace(own + ".copy", own)
            except OSError as e:
                log.info("yt-dlp: could not set up the updatable copy: %s", e)
                return False
            write_state(version=bundled_version() or version_of(own))
        code, out = run([own, "--update-to", "stable"], 300)
        lines = [l for l in out.splitlines() if l.strip()]
        log.info("yt-dlp update: exit %s, %s", code, lines[-1] if lines else "no output")
        v = version_of(own)
        if v is None:
            log.warning("yt-dlp: the updated copy does not run; removed it (links use the shipped copy)")
            _discard(own)
        write_state(version=v, checked=time.time())
        return in_use() != before


def update_in_background() -> threading.Thread | None:
    """At most once a day, refresh() in a background thread."""
    if not updates_allowed():
        return None
    try:
        last = float(read_state().get("checked") or 0)
    except (TypeError, ValueError):
        last = 0.0
    if time.time() - last < UPDATE_INTERVAL:
        return None
    t = threading.Thread(target=refresh, daemon=True, name="yt-dlp update")
    t.start()
    return t


def recover() -> bool:
    """A link failed to open: update now unless that was tried in the last RECOVER_INTERVAL (blocking; call
    from a worker thread). True if links now use a different yt-dlp."""
    if not updates_allowed():
        return False
    with _lock:
        try:
            last = float(read_state().get("recovered") or 0)
        except (TypeError, ValueError):
            last = 0.0
        if time.time() - last < RECOVER_INTERVAL:
            return False
        write_state(recovered=time.time())
    log.info("yt-dlp: a link failed; checking for a newer yt-dlp now")
    return refresh()
