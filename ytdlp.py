"""Web links: finding yt-dlp, mpv's options for a link, links in pasted or dropped text, and keeping the
bundled yt-dlp current.

mpv plays direct stream links (.m3u8, .mp4 URLs, RTMP) by itself. Links to web pages (YouTube, Twitch and
the other sites yt-dlp supports) go through mpv's built-in ytdl_hook, which runs yt-dlp to find the
stream. Tessera Wall ships a pinned yt-dlp (build.ps1) next to mpv and lets it update itself once a week,
because sites change faster than Tessera Wall releases.
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
UPDATE_INTERVAL = 7 * 24 * 3600
URL = re.compile(r"^(?:https?|rtmps?|rtsp|mms)://\S+$", re.IGNORECASE)


def bundled_path() -> str:
    return os.path.join(C.app_dir(), "yt-dlp", "yt-dlp.exe")


def find() -> str | None:
    """yt-dlp.exe: the ytdlp_path setting, then the copy shipped with Tessera Wall, then one on PATH."""
    if C.YTDLP_PATH:
        return C.YTDLP_PATH if os.path.isfile(C.YTDLP_PATH) else None
    if os.path.isfile(bundled_path()):
        return bundled_path()
    return shutil.which("yt-dlp")


def mpv_args(item: str) -> list[str]:
    """Extra mpv options for one video: for a link, which yt-dlp to use and the highest quality to ask for
    (video and audio are fetched separately and joined by mpv, so no ffmpeg is needed). Nothing for files."""
    if "://" not in item:
        return []
    height = max(144, int(C.STREAM_MAX_HEIGHT or 1080))
    args = ["--ytdl=yes", f"--ytdl-format=bestvideo[height<=?{height}]+bestaudio/best[height<=?{height}]/best"]
    exe = find()
    if exe:
        args.append(f"--script-opts-append=ytdl_hook-ytdl_path={exe}")
    return args


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


def update_in_background(state_path: str) -> threading.Thread | None:
    """Once a week, let the bundled yt-dlp update itself (it downloads its own new version from its GitHub
    releases). Only the copy shipped with Tessera Wall, never one the user installed; never when update
    checks are off. Runs in a background thread; failures are logged and otherwise ignored."""
    if not C.UPDATE_CHECK or C.YTDLP_PATH or not os.path.isfile(bundled_path()):
        return None
    try:
        with open(state_path, encoding="utf-8") as f:
            last = float(json.load(f).get("checked", 0))
    except (OSError, ValueError, AttributeError):
        last = 0.0
    if time.time() - last < UPDATE_INTERVAL:
        return None

    def run():
        try:
            done = subprocess.run([bundled_path(), "-U"], capture_output=True, text=True, timeout=180,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
            lines = [l for l in (done.stdout + done.stderr).splitlines() if l.strip()]
            log.info("yt-dlp update: exit %s, %s", done.returncode, lines[-1] if lines else "no output")
        except (OSError, subprocess.TimeoutExpired) as e:
            log.info("yt-dlp update failed: %s", e)
            return
        try:
            os.makedirs(os.path.dirname(state_path) or ".", exist_ok=True)
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump({"checked": time.time()}, f)
        except OSError:
            pass

    t = threading.Thread(target=run, daemon=True, name="yt-dlp update")
    t.start()
    return t
