"""Link sources: a YouTube channel, one of its tabs (Videos, Shorts, Live), a playlist or a search, used like
a folder.

The wall opens N random videos from a source and picks further ones from it for N, +, Page Down/Up, Move on
and "when a video ends". A source is listed with yt-dlp's flat mode (ids, titles and lengths only, no
format lookups: NASA's 42 Shorts in ~2 s, its 5,562 videos in ~50 s, so at most POOL_LIMIT newest are
listed) and the list is cached for a day in the state folder (ytdlp.state_dir()/pools).

A search is a YouTube results link (youtube.com/results?search_query=...), or yt-dlp's own spelling
"ytsearch:words", which is turned into one; its first SEARCH_LIMIT results are listed.

Videos are picked at random, or in the source's order (SOURCE_ORDER "newest": a channel's newest first, a
playlist's top first, a search's best matches first). Either way, videos already shown are passed over
until every one has been.

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
import urllib.parse

import ytdlp

log = logging.getLogger("tesserawall")
POOL_LIMIT = 500                   # newest entries listed per source
SEARCH_LIMIT = 100                 # results listed per search (YouTube sends about 20 per page)
MAX_AGE = 24 * 3600                # a cached list is used for a day...
FRESH_AGE = 3600                   # ...or an hour when the newest are wanted
LISTING = "%(id)s\t%(url)s\t%(duration)s\t%(live_status)s\t%(title)s"   # per entry; the title last (tabs)
TITLE_LINE = "PLAYLIST TITLE\t"    # printed once after the entries, before the list's own title
RECENT_KEEP = 20                   # unpinned sources remembered in sources.json; pinned ones are kept
RECENT_SHOWN = 6                   # listed in the start window: every pinned one, then recent ones to make 6
_CHANNEL = re.compile(r"^https?://(?:www\.|m\.)?youtube\.com/(@[^/?#]+|channel/[^/?#]+|c/[^/?#]+|user/[^/?#]+)"
                      r"(?:/(videos|shorts|streams|featured))?/?(?:[?#].*)?$", re.IGNORECASE)
_PLAYLIST = re.compile(r"^https?://(?:www\.|m\.)?youtube\.com/playlist\?(?:.*&)?list=([\w-]+)", re.IGNORECASE)
_RESULTS = re.compile(r"^https?://(?:www\.|m\.)?youtube\.com/results\?", re.IGNORECASE)
_SEARCH_TEXT = re.compile(r"^ytsearch\d*:\s*(\S.*)$", re.IGNORECASE)


def search_url(query: str) -> str:
    """The YouTube results link for a search, the form a search source is kept in."""
    return "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(" ".join(query.split()))


def from_search_text(text: str) -> str | None:
    """'ytsearch:lofi hip hop' (yt-dlp's spelling, also 'ytsearch50:...') -> its results link; else None."""
    m = _SEARCH_TEXT.match((text or "").strip())
    return search_url(m.group(1)) if m else None


def query_of(source: str) -> str | None:
    """The words of a search source (a results link); None for other sources."""
    if not _RESULTS.match(source or ""):
        return None
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(source).query).get("search_query", [""])[0]
    return " ".join(q.split()) or None


def source_of(link: str) -> str | None:
    """The canonical source URL if `link` is a channel, a channel tab, a playlist or a search; else None.
    A bare channel (or its Home tab) means its Videos tab. A search's filters (&sp=...) are dropped."""
    if _RESULTS.match(link or ""):
        q = query_of(link)
        return search_url(q) if q else None
    if _SEARCH_TEXT.match((link or "").strip()):
        return from_search_text(link)
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


def _shorten(text: str, n: int = 40) -> str:
    return text if len(text) <= n else text[:n - 1].rstrip() + "\u2026"


def label(source: str, title: str | None = None) -> str:
    """'@NASA Shorts', '@NASA videos', 'the playlist "Hits"', 'the search "lofi"' for messages. A playlist's
    title is the one given, else the one remembered from listing it."""
    q = query_of(source)
    if q:
        return "the search \u201c" + _shorten(q) + "\u201d"
    m = re.match(r"https://www\.youtube\.com/(@[^/]+|channel/[^/]+|c/[^/]+|user/[^/]+)/(\w+)$", source or "")
    if m:
        return f"{m.group(1)} {'Shorts' if m.group(2) == 'shorts' else m.group(2)}"
    title = title or cached_title(source)
    return "the playlist \u201c" + _shorten(title) + "\u201d" if title else "the playlist"


def menu_name(source: str, title: str | None = None) -> str:
    """The name in the recent list: '@NASA Shorts', 'Search: lofi hip hop', 'Playlist: Hits'."""
    q = query_of(source)
    if q:
        return "Search: " + _shorten(q, 48)
    if _CHANNEL.match(source or ""):
        return label(source)
    title = title or cached_title(source)
    return "Playlist: " + _shorten(title, 46) if title else "A playlist"


def parse_listing(text: str) -> list[dict]:
    """Lines of yt-dlp's --print LISTING ("%(id)s\\t%(url)s\\t%(duration)s\\t%(live_status)s\\t%(title)s")
    -> entries {url, title, duration}. Entries without an id, deleted or private videos, and premieres or
    streams that have not started (live_status is_upcoming: nothing to play yet) are dropped."""
    out, seen = [], set()
    for line in (text or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 5 or not re.fullmatch(r"[\w-]{6,}", parts[0]):
            continue
        vid, url, dur, status, title = parts[0], parts[1], parts[2], parts[3], "\t".join(parts[4:])
        if status == "is_upcoming":
            continue
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


def cached_title(source: str) -> str:
    """The list's own title saved when it was last listed (a playlist's name), whatever its age; else ''."""
    try:
        with open(cache_path(source), encoding="utf-8") as f:
            data = json.load(f)
        return str(data.get("title") or "") if data.get("source") == source else ""
    except (OSError, ValueError, AttributeError):
        return ""


def load(source: str, limit: int = POOL_LIMIT, max_age: float = MAX_AGE) -> list[dict]:
    """The source's videos in its order (a channel's newest first): from the cache if younger than
    max_age, else listed now with yt-dlp (blocking, call from a worker thread). A search lists its first
    SEARCH_LIMIT results. Raises RuntimeError when it cannot be listed."""
    entries = cached(source, max_age)
    if entries:
        log.info("source %s: %d videos (cached)", source, len(entries))
        return entries
    exe = ytdlp.find()
    if not exe:
        raise RuntimeError("yt-dlp is missing")
    q = query_of(source)
    if q:
        limit = min(limit, SEARCH_LIMIT)
    target = f"ytsearch{limit}:{q}" if q else source
    t = time.monotonic()
    code, out = ytdlp.run([exe, "--flat-playlist", "--no-warnings", "--playlist-items", f"1:{limit}",
                           "--print", LISTING, "--print", "playlist:" + TITLE_LINE + "%(title)s", target], 180)
    entries = parse_listing(out)
    title = next((l[len(TITLE_LINE):].strip() for l in out.splitlines() if l.startswith(TITLE_LINE)), "")
    log.info("source %s: %d videos listed in %.1f s (yt-dlp exit %s)", source, len(entries),
             time.monotonic() - t, code)
    if not entries:
        lines = [l for l in out.splitlines() if l.strip()]
        raise RuntimeError(lines[-1][:160] if lines else f"yt-dlp exit {code}")
    try:
        os.makedirs(os.path.dirname(cache_path(source)), exist_ok=True)
        with open(cache_path(source), "w", encoding="utf-8") as f:
            json.dump({"source": source, "fetched": time.time(), "title": title, "entries": entries}, f)
    except OSError as e:
        log.info("source cache not saved: %s", e)
    return entries


def pick(entries: list[dict], n: int, taken: set = frozenset(), order: str = "random",
         seen: set = frozenset()) -> list[dict]:
    """Up to n entries whose url is not in `taken` (on the wall now): at random, or the first ones in the
    source's order ("newest"). Entries in `seen` (shown before) come only once the others run out."""
    free = [e for e in entries if e["url"] not in taken]
    fresh = [e for e in free if e["url"] not in seen]
    rest = [e for e in free if e["url"] in seen]
    if order == "newest":
        return (fresh + rest)[:n]
    got = random.sample(fresh, min(n, len(fresh)))
    return got + random.sample(rest, min(n - len(got), len(rest)))


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


# ---- recent and pinned sources (sources.json next to the settings file) --------------------------------
def recent_path(config_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(config_path)), "sources.json")


def _read_recent(config_path: str) -> list[dict]:
    try:
        with open(recent_path(config_path), encoding="utf-8") as f:
            data = json.load(f)
        return [d for d in data.get("sources", []) if isinstance(d, dict) and source_of(str(d.get("source", "")))]
    except (OSError, ValueError, AttributeError):
        return []


def _write_recent(config_path: str, items: list[dict]):
    path = recent_path(config_path)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".new", "w", encoding="utf-8") as f:
            json.dump({"sources": items}, f, ensure_ascii=False, indent=1)
        os.replace(path + ".new", path)
    except OSError as e:
        log.info("recent sources not saved: %s", e)


def recent(config_path: str, limit: int | None = None) -> list[dict]:
    """Remembered sources as {source, title, pinned, used}: pinned ones first, then the most recently
    opened. `limit` keeps every pinned one plus recent ones up to that many in all."""
    items = sorted(_read_recent(config_path), key=lambda d: (not d.get("pinned"), -float(d.get("used") or 0)))
    if limit is not None:
        pinned = [d for d in items if d.get("pinned")]
        items = pinned + [d for d in items if not d.get("pinned")][:max(0, limit - len(pinned))]
    return items


def remember(config_path: str, source: str, title: str = ""):
    """A source was opened: put it at the top of the recent list (keeping its pin), with the list's title if
    known. Only the RECENT_KEEP latest unpinned sources are kept."""
    items = [d for d in _read_recent(config_path) if d.get("source") != source]
    old = next((d for d in _read_recent(config_path) if d.get("source") == source), {})
    items.insert(0, {"source": source, "title": title or old.get("title", ""),
                     "pinned": bool(old.get("pinned")), "used": time.time()})
    unpinned = [d for d in items if not d.get("pinned")]
    drop = {id(d) for d in sorted(unpinned, key=lambda d: -float(d.get("used") or 0))[RECENT_KEEP:]}
    _write_recent(config_path, [d for d in items if id(d) not in drop])


def set_pinned(config_path: str, source: str, pinned: bool):
    items = _read_recent(config_path)
    for d in items:
        if d.get("source") == source:
            d["pinned"] = bool(pinned)
    _write_recent(config_path, items)


def forget(config_path: str, source: str):
    _write_recent(config_path, [d for d in _read_recent(config_path) if d.get("source") != source])
