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
        elif os.path.isdir(a):
            for name in sorted(os.listdir(a), key=str.lower):
                full = os.path.join(a, name)
                if os.path.isfile(full) and os.path.splitext(name)[1].lower() in C.VIDEO_EXTS:
                    out.append(os.path.abspath(full))
        elif os.path.isfile(a):
            out.append(os.path.abspath(a))
    return out


def load_session(path: str) -> list[tuple[str, float]]:
    """Videos from the last saved session as (path, position) pairs; files that are gone are skipped."""
    try:
        with open(path, encoding="utf-8") as f:
            videos = json.load(f)["videos"]
        return [(v["path"], float(v.get("pos") or 0)) for v in videos
                if "://" in v["path"] or os.path.isfile(v["path"])]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def explorer_sorted(paths: list) -> list:
    """Paths in Explorer's name order ("clip 2" before "clip 10"), case-insensitive."""
    cmp = ctypes.windll.shlwapi.StrCmpLogicalW
    cmp.argtypes, cmp.restype = [wintypes.LPCWSTR, wintypes.LPCWSTR], ctypes.c_int
    return sorted(paths, key=functools.cmp_to_key(cmp))
