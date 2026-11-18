"""Video shapes before anything is opened, for templates.

One hidden mpv per batch runs probe.lua over a playlist: each file is opened paused with no output, its
first video track's size, rotation and pixel shape are read from the container (no decoding), and the
next file is opened. About 2 ms a file after mpv's start-up, so a few hundred files take a second or two.
Results are cached in shapes.json next to the settings file, keyed by path and checked against the
file's size and modification time, so a folder is only read once.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import time

import config as C

log = logging.getLogger("tesserawall")
CACHE_VERSION = 1
BATCHES = 4                      # mpv processes run side by side
SECONDS_PER_FILE = 1.0           # a batch that takes longer than this per file (plus 10 s) is stopped


def display_aspect(w: int, h: int, rotation: int = 0, par: float = 1.0) -> float | None:
    """Width / height as shown: pixel shape applied, and turned for videos recorded rotated (phones)."""
    if w <= 0 or h <= 0:
        return None
    a = w * (par or 1.0) / h
    return 1 / a if rotation % 180 == 90 else a


def parse_output(text: str) -> dict[int, float | None]:
    """probe.lua's lines -> {playlist index: aspect}. Index -1 lines (should not happen) are dropped."""
    out: dict[int, float | None] = {}
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) != 5:
            continue
        try:
            i, w, h, rot, par = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]), float(parts[4])
        except ValueError:
            continue
        if i >= 0:
            out[i] = display_aspect(w, h, rot, par)
    return out


def cache_path_for(config_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(config_path)), "shapes.json")


def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _stamp(path: str):
    try:
        st = os.stat(path)
        return [st.st_size, st.st_mtime_ns]
    except OSError:
        return None


def load_cache(cache_path: str) -> dict:
    try:
        with open(cache_path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("version") == CACHE_VERSION and isinstance(data.get("shapes"), dict):
            return data["shapes"]
    except (OSError, ValueError, AttributeError):
        pass
    return {}


def save_cache(cache_path: str, shapes: dict) -> None:
    try:
        os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
        tmp = cache_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": CACHE_VERSION, "shapes": shapes}, f)
        os.replace(tmp, cache_path)
    except OSError as e:
        log.info("shape cache not saved: %s", e)


def cached(paths: list[str], cache_path: str) -> dict[str, float | None]:
    """Shapes already known for `paths` (unchanged files only); nothing is probed."""
    cache = load_cache(cache_path)
    out = {}
    for p in paths:
        entry = cache.get(_key(p))
        if entry and entry[:2] == _stamp(p):
            out[p] = entry[2]
    return out


def probe(paths: list[str], cache_path: str, progress=None, mpv: str | None = None) -> dict[str, float | None]:
    """{path: width/height as displayed, or None if it is not a playable video} for every path.

    progress(done, total) is called from this thread as batches report; it must not touch tk directly
    if this runs in a worker thread. URLs are skipped (None).
    """
    cache = load_cache(cache_path)
    result: dict[str, float | None] = {}
    todo = []
    for p in paths:
        if "://" in p or not os.path.isfile(p):
            result[p] = None
            continue
        entry = cache.get(_key(p))
        if entry and entry[:2] == _stamp(p):
            result[p] = entry[2]
        else:
            todo.append(p)
    total, started = len(paths), time.monotonic()
    if progress:
        progress(len(result), total)
    mpv = mpv or C.find_mpv()
    script = os.path.join(C.app_dir(), "probe.lua")
    if todo and mpv and os.path.isfile(script):
        work = tempfile.mkdtemp(prefix="tesserawall-probe-")
        size = -(-len(todo) // BATCHES)
        batches = [todo[i:i + size] for i in range(0, len(todo), size)]
        running = []
        for n, batch in enumerate(batches):
            playlist, out = os.path.join(work, f"list{n}.txt"), os.path.join(work, f"out{n}.txt")
            with open(playlist, "w", encoding="utf-8") as f:
                f.write("\n".join(batch) + "\n")
            # --idle=no / --keep-open=no: started without a console, mpv applies its pseudo-gui profile
            # (idle=once) and would otherwise stay running after the last file.
            # Less work per file, which matters on real (large) files: no audio or subtitle tracks, no
            # read-ahead while paused (it would read megabytes of each file), and no listing the folder
            # for matching subtitle / audio / cover files. The video track must stay selected: with
            # nothing selected mpv gives up on the file before file-loaded.
            args = [mpv, "--no-config", "--vo=null", "--ao=null", "--pause", "--idle=no", "--keep-open=no",
                    "--ytdl=no", "--aid=no", "--sid=no", "--cache=no", "--demuxer-readahead-secs=0",
                    "--sub-auto=no", "--audio-file-auto=no", "--cover-art-auto=no",
                    "--msg-level=all=no", "--load-scripts=no", f"--script={script}",
                    f"--script-opts=tw-out={out}", f"--playlist={playlist}"]
            try:
                proc = subprocess.Popen(args, creationflags=subprocess.CREATE_NO_WINDOW)
            except OSError as e:
                log.error("shape probe could not start mpv: %s", e)
                continue
            running.append((proc, batch, out, time.monotonic() + 10 + SECONDS_PER_FILE * len(batch)))
        done_lines = {}
        while running:
            time.sleep(0.05)
            for item in list(running):
                proc, batch, out, deadline = item
                finished = proc.poll() is not None
                if not finished and time.monotonic() > deadline:
                    proc.kill()
                    proc.wait()
                    log.info("shape probe: a batch took too long and was stopped")
                    finished = True
                    item = (proc, batch, out, -1)            # -1: stopped, the rest of the batch is unknown
                try:
                    with open(out, encoding="utf-8", errors="replace") as f:
                        found = parse_output(f.read())
                except OSError:
                    found = {}
                done_lines[out] = len(found)
                if finished:
                    running.remove(next(r for r in running if r[2] == out))
                    last = max(found, default=-1)
                    for i, p in enumerate(batch):
                        if i in found:
                            result[p] = found[i]
                        elif item[3] != -1 or i < last:  # skipped by mpv: not a playable video
                            result[p] = None
                        if p in result:
                            stamp = _stamp(p)
                            if stamp:
                                cache[_key(p)] = stamp + [result[p]]
            if progress:
                progress(len(paths) - len(todo) + sum(done_lines.values()), total)
        save_cache(cache_path, cache)
    for p in paths:
        result.setdefault(p, None)
    log.info("shape probe: %d files, %d read, %d cached, %d not videos, %.2fs", total, len(todo),
             total - len(todo), sum(1 for v in result.values() if v is None), time.monotonic() - started)
    if progress:
        progress(total, total)
    return result
