"""Turning command-line arguments into video paths, Explorer's sort order, and the saved session."""
import ctypes
import functools
import json
import logging
import os
import tempfile
from ctypes import wintypes

import config as C

log = logging.getLogger("tesserawall")


SESSION_PATH = os.path.join(os.environ.get("APPDATA") or tempfile.gettempdir(), "TesseraWall", "session.json")


def expand(args: list[str]) -> list[str]:
    out = []
    for a in args:
        if "://" in a:
            out.append(a)
        elif a.lower().startswith("ytsearch"):           # yt-dlp's spelling of a search: a search source
            import sources
            link = sources.from_search_text(a)
            if link:
                out.append(link)
        elif os.path.isdir(a):
            for name in sorted(os.listdir(a), key=str.lower):
                full = os.path.join(a, name)
                if os.path.isfile(full) and os.path.splitext(name)[1].lower() in C.VIDEO_EXTS:
                    out.append(os.path.abspath(full))
        elif os.path.isfile(a):
            out.append(os.path.abspath(a))
    return out


def walls_dir(config_path: str) -> str:
    """Saved walls live next to the settings file, so tests (with their own --config) stay separate."""
    return os.path.join(os.path.dirname(os.path.abspath(config_path)), "walls")


def save_wall(config_path: str, name: str, items: list) -> str:
    safe = "".join(ch if ch.isalnum() or ch in " -_." else "_" for ch in name).strip()[:60] or "wall"
    folder = walls_dir(config_path)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, safe + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"name": name.strip()[:60], "videos": items}, f, indent=1)
    return path


def list_walls(config_path: str, limit: int = 4) -> list[tuple[str, list]]:
    """Most recently saved walls first, as (name, items) with items ready for load_session's format."""
    folder = walls_dir(config_path)
    try:
        files = sorted((os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(".json")),
                       key=os.path.getmtime, reverse=True)
    except OSError:
        return []
    out = []
    for path in files[:limit]:
        items = load_session(path)
        if items:
            try:
                with open(path, encoding="utf-8") as f:
                    name = json.load(f).get("name") or os.path.splitext(os.path.basename(path))[0]
            except (OSError, ValueError):
                name = os.path.splitext(os.path.basename(path))[0]
            out.append((str(name), items))
    return out


def load_session(path: str) -> list[tuple]:
    """Videos from the last saved session as (path, position) pairs, or (path, position, source) for a
    video picked from a channel or playlist (sources.py); files that are gone are skipped."""
    try:
        with open(path, encoding="utf-8") as f:
            videos = json.load(f)["videos"]
        return [(v["path"], float(v.get("pos") or 0)) + ((v["pool"],) if v.get("pool") else ())
                for v in videos if "://" in v["path"] or os.path.isfile(v["path"])]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def explorer_sorted(paths: list) -> list:
    """Paths in Explorer's name order ("clip 2" before "clip 10"), case-insensitive."""
    cmp = ctypes.windll.shlwapi.StrCmpLogicalW
    cmp.argtypes, cmp.restype = [wintypes.LPCWSTR, wintypes.LPCWSTR], ctypes.c_int
    return sorted(paths, key=functools.cmp_to_key(cmp))
