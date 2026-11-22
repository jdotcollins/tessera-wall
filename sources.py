"""Link sources: a YouTube channel, one of its tabs (Videos, Shorts, Live) or a playlist, used like a folder.

The wall opens N random videos from a source and picks further ones from it for N, +, Page Down/Up, Move on
and "when a video ends". A source is listed with yt-dlp's flat mode (ids, titles and lengths only, no
format lookups: NASA's 42 Shorts in ~2 s, its 5,562 videos in ~50 s, so at most POOL_LIMIT newest are
listed) and the list is cached for a day in the state folder (ytdlp.state_dir()/pools).

A link that is one video (watch?v=..., even with &list=..., shorts/ID, youtu.be/ID) is not a source.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import time

import ytdlp

log = logging.getLogger("tesserawall")
POOL_LIMIT = 500                   # newest entries listed per source
MAX_AGE = 24 * 3600                # a cached list is used for a day
_CHANNEL = re.compile(r"^https?://(?:www\.|m\.)?youtube\.com/(@[^/?#]+|channel/[^/?#]+|c/[^/?#]+|user/[^/?#]+)"
                      r"(?:/(videos|shorts|streams|featured))?/?(?:[?#].*)?$", re.IGNORECASE)
_PLAYLIST = re.compile(r"^https?://(?:www\.|m\.)?youtube\.com/playlist\?(?:.*&)?list=([\w-]+)", re.IGNORECASE)


def source_of(link: str) -> str | None:
    """The canonical source URL if `link` is a channel, a channel tab or a playlist; else None.
    A bare channel (or its Home tab) means its Videos tab."""
    m = _CHANNEL.match(link or "")
    if m:
        tab = (m.group(2) or "videos").lower()
        return f"https://www.youtube.com/{m.group(1)}/{'videos' if tab == 'featured' else tab}"
    m = _PLAYLIST.match(link or "")
    if m:
        return f"https://www.youtube.com/playlist?list={m.group(1)}"
    return None


def is_short(url: str) -> bool:
    return "/shorts/" in (url or "")


def label(source: str) -> str:
    """'@NASA Shorts', '@NASA videos', 'the playlist' for messages."""
    m = re.match(r"https://www\.youtube\.com/(@[^/]+|channel/[^/]+|c/[^/]+|user/[^/]+)/(\w+)$", source or "")
    if m:
        return f"{m.group(1)} {'Shorts' if m.group(2) == 'shorts' else m.group(2)}"
    return "the playlist"


def parse_listing(text: str) -> list[dict]:
    """Lines of yt-dlp's --print "%(id)s\\t%(url)s\\t%(duration)s\\t%(title)s" -> entries
    {url, title, duration}; entries without an id, and deleted or private videos, are dropped."""
    out, seen = [], set()
    for line in (text or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 4 or not re.fullmatch(r"[\w-]{6,}", parts[0]):
            continue
        vid, url, dur, title = parts[0], parts[1], parts[2], "\t".join(parts[3:])
        if not url.startswith("http"):
            url = f"https://www.youtube.com/watch?v={vid}"
        if vid in seen or title in ("[Private video]", "[Deleted video]"):
            continue
        seen.add(vid)
        try:
            duration = float(dur)
        except ValueError:
            duration = None
        out.append({"url": url, "title": title, "duration": duration})
    return out


def cache_path(source: str) -> str:
    return os.path.join(ytdlp.state_dir(), "pools", hashlib.sha1(source.encode()).hexdigest()[:16] + ".json")


def cached(source: str, max_age: float = MAX_AGE) -> list[dict] | None:
    try:
        with open(cache_path(source), encoding="utf-8") as f:
            data = json.load(f)
        if data.get("source") == source and time.time() - float(data.get("fetched", 0)) < max_age:
            return data["entries"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def load(source: str, limit: int = POOL_LIMIT) -> list[dict]:
    """The source's videos, newest first: from the cache if under a day old, else listed now with yt-dlp
    (blocking, call from a worker thread). Raises RuntimeError when it cannot be listed."""
    entries = cached(source)
    if entries:
        log.info("source %s: %d videos (cached)", source, len(entries))
        return entries
    exe = ytdlp.find()
    if not exe:
        raise RuntimeError("yt-dlp is missing")
    t = time.monotonic()
    code, out = ytdlp.run([exe, "--flat-playlist", "--no-warnings", "--playlist-items", f"1:{limit}",
                           "--print", "%(id)s\t%(url)s\t%(duration)s\t%(title)s", source], 180)
    entries = parse_listing(out)
    log.info("source %s: %d videos listed in %.1f s (yt-dlp exit %s)", source, len(entries),
             time.monotonic() - t, code)
    if not entries:
        lines = [l for l in out.splitlines() if l.strip()]
        raise RuntimeError(lines[-1][:160] if lines else f"yt-dlp exit {code}")
    try:
        os.makedirs(os.path.dirname(cache_path(source)), exist_ok=True)
        with open(cache_path(source), "w", encoding="utf-8") as f:
            json.dump({"source": source, "fetched": time.time(), "entries": entries}, f)
    except OSError as e:
        log.info("source cache not saved: %s", e)
    return entries


def pick(entries: list[dict], n: int, taken: set = frozenset()) -> list[dict]:
    """Up to n random entries whose url is not in `taken`."""
    free = [e for e in entries if e["url"] not in taken]
    return random.sample(free, min(n, len(free)))


def step(entries: list[dict], current: str, direction: int, taken: set = frozenset()) -> dict | None:
    """The next (direction 1) or previous (-1) entry after `current` in the source's order, wrapping,
    skipping ones in `taken`."""
    if not entries:
        return None
    urls = [e["url"] for e in entries]
    i = urls.index(current) if current in urls else (-1 if direction > 0 else 0)
    for k in range(1, len(entries) + 1):
        e = entries[(i + direction * k) % len(entries)]
        if e["url"] not in taken:
            return e
    return None


def height_for(n: int, screen_h: int, cap: int) -> int:
    """Stream quality for one of n videos on a screen screen_h tall: the smallest common height at least
    as tall as a video's likely share of the screen (screen_h / sqrt(n)), never above `cap`. Six videos on
    a 1440-pixel screen ask for 720p instead of 1080p: about half the data."""
    need = screen_h / max(1.0, n) ** 0.5
    for h in (360, 480, 720, 1080, 1440, 2160):
        if h >= need:
            return min(h, max(144, cap))
    return max(144, cap)
