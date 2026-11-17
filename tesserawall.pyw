"""Tessera Wall: open several videos in mpv as one full-screen wall.

Hovering a video unmutes it, mutes the rest and enlarges it. See docs/DEVELOPMENT.md for the design.
Usage: tesserawall.pyw [--exit-after SECONDS] FILE_OR_FOLDER_OR_URL...

This file only starts the program; the code lives in wall.py, ui.py, instance.py, files.py and winapi.py
(plus config.py, layout.py, ipc.py and updates.py).
"""
import ctypes

try:  # must run before tkinter is imported anywhere, so tk pixels == real pixels
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

import os
import sys
import argparse
import logging
import queue
import random
import tempfile
import threading
import time
import tkinter as tk
from tkinter import messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C
import updates
from files import SESSION_PATH, expand, explorer_sorted, load_session  # expand: tests load this file by path and call it
from instance import claim_control_pipe, gather_batch, listen
from ui import enable_drop, make_root, start_window
from wall import Wall
from winapi import monitor_under_cursor

log = logging.getLogger("tesserawall")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exit-after", type=float, default=0, help="quit after N seconds (testing)")
    ap.add_argument("--resume", action="store_true", help="reopen the last session")
    ap.add_argument("--session", default=None, help="session file to use instead of the default")
    ap.add_argument("--random", type=int, default=0, metavar="N",
                    help="open only N randomly chosen videos from the given files/folders")
    ap.add_argument("--config", default=None, help="settings file to use instead of the default")
    ap.add_argument("paths", nargs="*")
    ns = ap.parse_args()

    # Settings first: opening a folder depends on video_exts.
    config_path = ns.config or C.user_config_path()
    if ns.exit_after > 0 and not ns.config:
        # Test runs (--exit-after) use the defaults and a throwaway settings file, never the user's own:
        # a test must not depend on, or change, what the person running it has chosen.
        config_path = os.path.join(tempfile.mkdtemp(prefix="tesserawall-test-"), "config.toml")
    notice = None
    if not ns.config and not ns.exit_after > 0:
        C.migrate_old_settings()      # settings from before the rename, copied once
        C.ensure_user_file(config_path)
    try:
        _, problems = C.load(config_path)
        if problems:
            notice = f"Settings file: {len(problems)} ignored - {problems[0]}"
    except Exception as e:  # unreadable or invalid file: run on defaults and say so on screen
        problems = [f"{type(e).__name__}: {e}"]
        notice = "Settings file has an error, using defaults - " + str(e)[:100]

    paths = expand(ns.paths)
    if 0 < ns.random < len(paths):
        paths = random.sample(paths, ns.random)

    listener = claim_control_pipe(paths)
    if listener is None:
        return 0  # handed to the wall that is already running
    claimed_at = time.monotonic()
    # From here on later launches queue their files here, even while the start window is open.
    incoming: queue.Queue = queue.Queue()
    threading.Thread(target=listen, args=(listener, incoming), daemon=True).start()

    logging.basicConfig(filename=C.LOG_PATH or os.path.join(tempfile.gettempdir(), "tesserawall.log"),
                        filemode="w", level=logging.INFO, format="%(asctime)s.%(msecs)03d %(message)s",
                        datefmt="%H:%M:%S", force=True)
    log.info("settings file: %s", config_path)
    for p in problems:
        log.warning("config: %s", p)
    if not C.find_mpv():
        box = tk.Tk()
        box.withdraw()
        messagebox.showerror("Tessera Wall", C.MPV_MISSING_MESSAGE.format(config=config_path))
        box.destroy()
        return 1
    log.info("mpv: %s", C.find_mpv())
    log.info("version: %s", updates.current_version())
    checker = updates.Checker(os.path.join(os.path.dirname(os.path.abspath(config_path)), "update.json"))

    session_path = ns.session or SESSION_PATH
    if ns.resume:  # reopen what was open last time, at the same positions
        paths = load_session(session_path) + paths
    if not paths:  # started with nothing to play (Start Menu, desktop icon): show the front door
        paths = start_window(load_session(session_path), incoming, checker, config_path)
        if not paths:
            log.info("start window closed without a selection")
            return 0
    root = make_root()

    root.title("Tessera Wall")
    root.configure(bg="black")
    mx, my, mw, mh = monitor_under_cursor()
    primary = (mx, my) == (0, 0) and (mw, mh) == (root.winfo_screenwidth(), root.winfo_screenheight())
    log.info("monitor under pointer: %dx%d at %d,%d%s", mw, mh, mx, my, " (primary)" if primary else "")
    if primary:
        root.attributes("-fullscreen", True)
        root.update()
    else:  # tk's own fullscreen is only trusted on the primary monitor: cover the other one ourselves
        root.overrideredirect(True)
        root.geometry(f"{mw}x{mh}+{mx}+{my}")
        root.update()
    if (root.winfo_width(), root.winfo_height(), root.winfo_rootx(), root.winfo_rooty()) != (mw, mh, mx, my):
        log.info("window is %dx%d at %d,%d, forcing the monitor rectangle", root.winfo_width(),
                 root.winfo_height(), root.winfo_rootx(), root.winfo_rooty())
        root.attributes("-fullscreen", False)
        root.overrideredirect(True)
        root.geometry(f"{mw}x{mh}+{mx}+{my}")
        root.update()
    # Windows may refuse to give a background-launched process the foreground; briefly going
    # topmost guarantees the wall is at least on top of everything, then we behave normally.
    root.attributes("-topmost", True)
    root.lift()
    root.focus_force()
    root.after(500, lambda: root.attributes("-topmost", False))

    early = []  # anything handed over while starting up goes after our own files
    while not incoming.empty():
        early.append(incoming.get_nowait())
    if len(ns.paths) == 1 and not (ns.resume or ns.random) and all(isinstance(p, str) for p in paths):
        # Possibly the first of a multi-file right-click: wait for the siblings (this overlaps
        # with the window appearing) and show the whole batch in Explorer's name order.
        early += gather_batch(incoming, claimed_at)
        if early:
            log.info("gathered %d more file(s) from simultaneous launches in %.2fs",
                     len(early), time.monotonic() - claimed_at)
            paths, early = explorer_sorted(paths + early), []
    for p in paths + early:
        incoming.put(p)
    wall = Wall(root, incoming)
    wall.config_path, wall.notice, wall.updates = config_path, notice, checker
    # Test runs (--exit-after) must not overwrite the real session unless a file is named explicitly.
    wall.session_path = None if (ns.exit_after > 0 and not ns.session) else session_path
    wall.bind_keys()
    enable_drop(root, incoming)
    root.protocol("WM_DELETE_WINDOW", wall.quit)
    root.report_callback_exception = lambda *exc: (log.error("callback error", exc_info=exc), wall.quit())
    if ns.exit_after > 0:
        root.after(int(ns.exit_after * 1000), wall.quit)
    root.after(0, wall.tick)
    try:
        root.mainloop()
    except Exception:
        log.exception("crashed")
        raise
    finally:
        wall.kill_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())
