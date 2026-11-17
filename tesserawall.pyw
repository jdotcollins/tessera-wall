"""tesserawall: open several videos in mpv as one full-screen wall.

Hovering a video unmutes it, mutes the rest and enlarges it. See HANDOFF.md for the design.
Usage: tesserawall.pyw [--exit-after SECONDS] FILE_OR_FOLDER_OR_URL...
"""
import ctypes

try:  # must run before tkinter is imported so tk pixels == real pixels
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

import argparse
import functools
import json
import logging
import os
import queue
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from ctypes import wintypes
from dataclasses import dataclass
from multiprocessing.connection import Client, Listener
from tkinter import filedialog, messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C
import updates
from ipc import MpvIpc
from layout import Rect, arrange, grid, layout, panscan_for

# The on-screen controls list (H). Keep this in step with Wall.bind_keys: it is the only place a user
# can discover the keys.
HELP_SECTIONS = [
    # (title, rows). A row's first item is either text (a mouse action) or a list of keys, drawn as key
    # chips: alternatives separated by "/", combinations written "Shift+Space".
    ("Mouse", [
        ("Point at a video", "Focus it: its sound fades in, it grows, the others dim"),
        ("Click", "Pin it, so pointing elsewhere keeps it focused (again to release)"),
        ("Double-click", "Zoom it to most of the screen (again to undo)"),
        ("Drag onto another", "Swap the two videos"),
        ("Bottom edge", "Click or drag to seek"),
        ("Wheel", "Seek; with Shift, seek every video"),
        ("Middle-click", "Close the video"),
        ("Top edge of screen", "Menu: add videos, controls, settings, close"),
    ]),
    ("Layout", [
        (["L"], "Arrangement: grow, best fit, even grid"),
        (["C"], "Black bars: crop a little, fill, never crop"),
        (["0"], "Unpin, unfocus and mute everything"),
    ]),
    ("Playback", [
        (["Space"], "Pause the focused video"),
        (["Shift+Space"], "Pause or resume all"),
        (["Left", "Right"], "Seek; with Shift, seek every video"),
        (["Up", "Down"], "Volume of the focused video"),
        (["R"], "Restart all from the beginning"),
        (["Page Down", "Page Up"], "Next / previous file from its folder"),
        (["N"], "Swap for a random file from its folder"),
        (["X", "Delete"], "Close the focused video"),
    ]),
    ("App", [
        (["H", "?", "F1"], "Show or hide this list"),
        (["S"], "Settings"),
        (["F5"], "Reload the settings file"),
        (["U"], "Open the download page, when a new version is out"),
        (["Esc"], "Close this list, release pin or zoom, else quit"),
        (["Q"], "Quit and remember this session"),
    ]),
]
HELP_COLUMNS = (("Mouse", "Layout"), ("Playback", "App"))

LAYOUT_MODES = ("grow", "flat", "grid")
CROP_MODES = ("limited", "fill", "fit")

# One wall per Windows user: a second user on the same PC gets their own.
CONTROL_PIPE = r"\\.\pipe\tesserawall-control-" + "".join(
    ch if ch.isalnum() else "_" for ch in (os.environ.get("USERNAME") or "user"))
CONNECT_TIMEOUT = 15.0   # seconds for mpv to create its IPC pipe
ASPECT_TIMEOUT = 10.0    # seconds to wait for a video size before keeping the 16:9 guess
# Gathering a multi-file right-click (see gather_batch), in seconds after this launch became the wall.
BATCH_MIN, BATCH_QUIET, BATCH_MAX = 0.5, 0.35, 3.0
log = logging.getLogger("tesserawall")

user32 = ctypes.windll.user32
user32.WindowFromPoint.restype = wintypes.HWND
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
GA_ROOT = 2


@dataclass(eq=False)
class Tile:
    path: str
    frame: tk.Frame
    proc: subprocess.Popen
    pipe: str
    born: float
    ipc: MpvIpc | None = None
    aspect: float = 16 / 9
    aspect_known: bool = False
    rect: Rect | None = None     # where the frame is right now (animated)
    start: Rect | None = None    # where the current animation began
    target: Rect | None = None   # where it is heading; hover hit-tests use this
    panscan: float | None = None
    dim: int | None = None
    placed: tuple | None = None   # last frame.place geometry (after the gutter inset)       # brightness last sent to mpv
    skip: str | None = None      # vd-lavc-skipframe last sent to mpv (economy mode)
    volume: float = 100.0        # this video's own volume (Up/Down); survives focus changes
    gain: float = 0.0            # crossfade position: 0 = silent and muted, 1 = full volume
    idle_since: float | None = None   # when mpv was first seen idle (file failed to open)
    swapped: bool = False        # file was replaced with `n`; wait for mpv to report the new one


SESSION_PATH = os.path.join(os.environ.get("APPDATA") or tempfile.gettempdir(), "TesseraWall", "session.json")


def fmt_time(s: float) -> str:
    s = int(max(0, s))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


class Wall:
    def __init__(self, root: tk.Tk, incoming: queue.Queue):
        self.root = root
        self.incoming = incoming
        self.W, self.H = root.winfo_width(), root.winfo_height()
        self.top = user32.GetAncestor(root.winfo_id(), GA_ROOT)
        self.tiles: list[Tile] = []
        self.rows: list[int] = []     # group sizes: rows, or columns when self.columns is True
        self.columns = False
        self.covered: bool | None = None
        self.focus: Tile | None = None
        self.candidate: Tile | None = None
        self.candidate_since = 0.0
        # Start "armed" at the launch position: nothing gets focus until the mouse actually moves.
        self.pos_at_focus_change: tuple[int, int] | None = self.cursor()
        self.counter = 0
        self.anim_start: float | None = None
        self.need_layout = False
        self.closing = False
        self.pin: Tile | None = None      # clicked tile: hover cannot take focus away from it
        self.zoom = False                 # pinned tile takes most of the screen
        self.click_was_zoomed = False     # state before the first click of a double-click
        self.press_in_bar = False         # current mouse press started on the seek bar
        self.press_tile: Tile | None = None
        self.press_pos = (0, 0)
        self.pressing = False             # left button is down: hover must not change focus
        self.dragging = False             # press has moved far enough to be a reorder drag
        self.skip_release = False         # this release belongs to a double-click
        self.manual_order = False         # user reordered tiles: stop regrouping them by shape
        self.last_tick = time.monotonic()
        self.bar_tile: Tile | None = None
        self.bar_next = 0.0
        self.last_pos = self.pos_at_focus_change
        self.last_move = time.monotonic()
        self.cursor_hidden = False
        self.session_path: str | None = None
        self.help: tk.Toplevel | None = None
        self.mpv = C.find_mpv()
        self.config_path = C.user_config_path()
        self.notice: str | None = None    # shown once on the first video (settings-file problems)
        self.hint_shown = False
        self.topbar: tk.Toplevel | None = None
        self.settings: tk.Toplevel | None = None
        self.ring = tk.Frame(root, bg="#6CB6FF", highlightthickness=0, bd=0)   # focus outline
        self.top_since: float | None = None   # when the pointer reached the top edge
        self.dialog_open = False
        self.failed: list[str] = []           # files mpv could not open, not yet reported
        self.updates: updates.Checker | None = None   # set by main()
        self.update_shown = False
        self.layout_mode = C.LAYOUT_MODE if C.LAYOUT_MODE in LAYOUT_MODES else "grow"
        self.crop_mode = C.CROP_MODE if C.CROP_MODE in CROP_MODES else "limited"
        log.info("start W=%d H=%d", self.W, self.H)

    @property
    def max_crop(self) -> float:
        return {"limited": C.MAX_CROP, "fill": 1.0, "fit": 0.0}[self.crop_mode]

    def cycle_mode(self, attr: str, modes: tuple, label: str):
        new = modes[(modes.index(getattr(self, attr)) + 1) % len(modes)]
        setattr(self, attr, new)
        log.info("%s mode -> %s", label.lower(), new)
        for t in self.tiles:
            if t.ipc:
                t.ipc.post("show-text", f"{label}: {new}", 1500)
        self.pos_at_focus_change = self.cursor()   # the grid may move under a still pointer
        self.relayout(rearrange=True)

    # ---- tiles -------------------------------------------------------------
    def add(self, item):
        path, start = item if isinstance(item, tuple) else (item, 0)
        if len(self.tiles) >= C.MAX_VIDEOS:
            log.info("ignored (MAX_VIDEOS reached): %s", path)
            return
        frame = tk.Frame(self.root, bg="black", highlightthickness=0, bd=0)
        frame.place(x=0, y=0, width=1, height=1)
        self.counter += 1
        pipe = rf"\\.\pipe\tesserawall-{os.getpid()}-{self.counter}"
        args = [self.mpv, f"--wid={frame.winfo_id()}", f"--input-ipc-server={pipe}",
                "--mute=yes", "--osc=no", "--input-default-bindings=no", "--input-vo-keyboard=no",
                "--input-cursor=no", "--cursor-autohide=no", "--loop-file=inf", "--keep-open=yes",
                "--force-window=yes", "--no-terminal", "--hwdec=auto-safe", "--panscan=0"]
        osd = max(16, self.H // 60)                     # 24 px on a 1440p screen, whatever the tile size
        args += ["--osd-scale-by-window=no", f"--osd-font-size={osd}", "--osd-font=Segoe UI",
                 "--osd-color=#FFE6E6E6", "--osd-border-style=background-box", "--osd-back-color=#D8101214",
                 "--osd-outline-color=#D8101214",
                 f"--osd-outline-size={max(4, osd // 3)}", f"--osd-margin-x={osd}",
                 f"--osd-margin-y={self.H // 24}"]      # clear of the top-edge menu bar
        if not C.USE_USER_MPV_CONFIG:
            args.append("--no-config")
        if start and start > 1:
            args.append(f"--start={start:.1f}")
        if C.SAFE_RENDERING and os.environ.get("TESSERAWALL_UNSAFE_RENDERING") != "1":
            # Many HDR flip-model swapchains being resized at once makes the AMD driver time out.
            args += ["--d3d11-flip=no", "--target-colorspace-hint=no",
                     "--d3d11-output-csp=srgb", "--d3d11-output-format=rgba8"]
        log_dir = os.environ.get("TESSERAWALL_MPV_LOG_DIR") or C.MPV_LOG_DIR
        if log_dir:  # one verbose mpv log per player, for diagnosing renderer/driver problems
            os.makedirs(log_dir, exist_ok=True)
            args.append("--log-file=" + os.path.join(log_dir, f"mpv-{os.getpid()}-{self.counter}.log"))
        args += list(C.EXTRA_MPV_ARGS) + ["--", path]
        try:
            proc = subprocess.Popen(args, creationflags=subprocess.CREATE_NO_WINDOW)
        except OSError as e:
            log.error("could not start mpv for %s: %s", path, e)
            frame.destroy()
            return
        self.tiles.append(Tile(path, frame, proc, pipe, time.monotonic()))
        self.need_layout = True
        log.info("tile added #%d pipe=%s path=%s", self.counter, pipe, path)

    def remove(self, tile: Tile, why: str):
        log.info("tile removed (%s): %s", why, tile.path)
        self.tiles.remove(tile)
        if tile.ipc:
            tile.ipc.close()
        if tile.proc.poll() is None:
            tile.proc.kill()
        tile.frame.destroy()
        if self.focus is tile:
            self.focus = None
        if self.candidate is tile:
            self.candidate = None
        if self.pin is tile:
            self.pin, self.zoom = None, False
        if self.bar_tile is tile:
            self.bar_tile = None
        self.need_layout = True

    # ---- layout ------------------------------------------------------------
    def relayout(self, rearrange: bool = False):
        """rearrange=True re-picks grouping/order (tiles changed); False keeps it (focus changed)."""
        self.need_layout = False
        if not self.tiles:
            return
        if rearrange or sum(self.rows) != len(self.tiles):
            order, self.columns, self.rows = arrange([t.aspect for t in self.tiles], self.W, self.H,
                                                     self.max_crop, C.BALANCE_WEIGHT,
                                                     allow_reorder=not self.manual_order)
            self.tiles = [self.tiles[i] for i in order]
        aspects = [t.aspect for t in self.tiles]
        f = self.tiles.index(self.focus) if self.focus in self.tiles else None
        zoomed = self.zoom and f is not None
        # Zoom = same reflow with "grow as far as allowed" and a much smaller floor for the others.
        scale, shrink = (100.0, C.ZOOM_MIN_SHRINK) if zoomed else (C.FOCUS_SCALE, C.MIN_SHRINK)
        if self.layout_mode == "grid" and not zoomed:
            rects = grid(aspects, self.W, self.H)            # identical cells; hover moves nothing
        else:
            grow = zoomed or self.layout_mode == "grow"       # "flat": best-fit layout, no growth on hover
            rects = layout(aspects, self.rows, self.W, self.H, focus=f if grow else None,
                           focus_scale=scale, min_shrink=shrink, columns=self.columns)
        pans = []
        for t, r in zip(self.tiles, rects):
            t.start = t.rect or r
            t.target = r
            ps = round(panscan_for(r.w, r.h, t.aspect, self.max_crop), 3)
            pans.append(ps)
            dim = -C.DIM_UNFOCUSED if (self.focus and t is not self.focus) else 0
            if t.ipc and ps != t.panscan:
                t.ipc.set_async("panscan", ps)
                t.panscan = ps
            if t.ipc and dim != t.dim:
                t.ipc.set_async("brightness", dim)
                t.dim = dim
            # Economy mode: unfocused videos skip B-frames in the decoder; the focused one decodes fully.
            mode = os.environ.get("TESSERAWALL_UNFOCUSED_SKIP", C.UNFOCUSED_SKIP)  # env override is for tests
            economy = mode and t is not self.focus and (self.focus or C.ECONOMY_WHEN_IDLE)
            skip = mode if economy else "default"
            if t.ipc and skip != t.skip:
                t.ipc.set_async("vd-lavc-skipframe", skip)
                t.skip = skip
        self.anim_start = time.monotonic()
        self.bar_next = 0.0  # redraw the seek bar at the new size
        log.info("relayout %s=%s focus=%s%s rects=%s panscan=%s", "cols" if self.columns else "rows", self.rows, f,
                 " zoom" if zoomed else "" if self.layout_mode == "grow" else f" {self.layout_mode}", [(r.x, r.y, r.w, r.h) for r in rects], pans)

    def animate(self) -> bool:
        """Move frames towards their targets. Returns True while still animating."""
        if self.anim_start is None:
            return False
        p = 1.0 if C.ANIM_MS <= 0 else min(1.0, (time.monotonic() - self.anim_start) * 1000 / C.ANIM_MS)
        e = 1 - (1 - p) ** 3
        for t in self.tiles:
            s, g = t.start, t.target
            r = g if p >= 1 else Rect(round(s.x + (g.x - s.x) * e), round(s.y + (g.y - s.y) * e),
                                      round(s.w + (g.w - s.w) * e), round(s.h + (g.h - s.h) * e))
            t.rect = r
            half = max(0, int(C.TILE_GAP)) // 2           # each tile gives up half the gap on every side
            geom = (r.x + half, r.y + half, max(1, r.w - 2 * half), max(1, r.h - 2 * half))
            if geom != t.placed:
                t.placed = geom
                t.frame.place(x=geom[0], y=geom[1], width=geom[2], height=geom[3])
        f = self.focus
        if f in self.tiles and f.rect and C.FOCUS_OUTLINE and C.TILE_GAP >= 2:
            r = f.rect                                    # shows through the gutter around the focused video
            self.ring.place(x=r.x, y=r.y, width=r.w, height=r.h)
            self.ring.lower()
        else:
            self.ring.place_forget()
        if p >= 1:
            self.anim_start = None
        return p < 1

    # ---- hover -------------------------------------------------------------
    def cursor(self) -> tuple[int, int]:
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    def tile_under_cursor(self, pos):
        hit = user32.WindowFromPoint(wintypes.POINT(*pos))
        covered = not hit or user32.GetAncestor(hit, GA_ROOT) != self.top
        if covered != self.covered:
            self.covered = covered
            log.info("pointer is over %s", "another window" if covered else "the wall")
        if covered:
            return None  # some other window is on top at this point
        x, y = pos[0] - self.root.winfo_rootx(), pos[1] - self.root.winfo_rooty()
        for t in self.tiles:
            if t.target and t.target.contains(x, y):
                return t
        return None

    def hover(self, pos):
        cand = self.tile_under_cursor(pos)
        if self.pin or self.pressing or cand is None or cand is self.focus:
            self.candidate = None
            return
        if pos == self.pos_at_focus_change:
            return  # the layout moved under a still mouse: never switch, or the grid would flicker
        now = time.monotonic()
        if cand is not self.candidate:
            self.candidate, self.candidate_since = cand, now
            return
        if (now - self.candidate_since) * 1000 >= C.HOVER_DELAY_MS:
            self.set_focus(cand, pos)

    def set_focus(self, tile: Tile | None, pos=None):
        self.focus = tile  # audio follows in update_audio(): the old video fades out, the new one in
        self.candidate = None
        self.pos_at_focus_change = pos or self.cursor()
        log.info("focus -> %s", self.tiles.index(tile) if tile else None)
        self.relayout()

    # ---- pin / zoom / seek bar ----------------------------------------------
    def set_pin(self, tile: Tile | None, zoom: bool = False):
        self.pin, self.zoom = tile, bool(tile) and zoom
        log.info("pin -> %s%s", self.tiles.index(tile) if tile else None, " zoom" if self.zoom else "")
        if tile and tile is not self.focus:
            self.set_focus(tile)
        else:
            self.pos_at_focus_change = self.cursor()  # unpinning must not make focus jump by itself
            self.relayout()

    def bar_fraction(self, e) -> float | None:
        """If the event is on the focused tile's seek-bar zone, return 0..1 along it."""
        t = self.focus
        if not t or not t.target or C.SEEKBAR_HEIGHT <= 0:
            return None
        x, y = e.x_root - self.root.winfo_rootx(), e.y_root - self.root.winfo_rooty()
        r = t.target
        if r.contains(x, y) and y >= r.y + r.h - C.SEEKBAR_ZONE:
            return min(1.0, max(0.0, (x - r.x) / max(1, r.w - 1)))
        return None

    # Mouse protocol: press records where it started; release decides what it was.
    #   release without moving      -> click: toggle pin
    #   moved > DRAG_THRESHOLD      -> drag: swap the pressed tile with the tile under the pointer
    #   press on the seek-bar zone  -> seek (and scrub while dragging)
    # A double-click arrives as press, release, <Double-Button-1> (INSTEAD of a 2nd press), release.
    def on_press(self, e):
        frac = self.bar_fraction(e)
        self.press_in_bar = frac is not None
        self.pressing, self.dragging, self.skip_release = True, False, False
        self.press_pos = (e.x_root, e.y_root)
        self.press_tile = None if self.press_in_bar else self.tile_under_cursor(self.press_pos)
        if self.press_in_bar:
            self.cmd("seek", round(frac * 100, 2), "absolute-percent")
            self.bar_next = 0.0

    def on_drag(self, e):
        if self.press_in_bar:
            if self.focus and self.focus.target:
                r = self.focus.target
                x = e.x_root - self.root.winfo_rootx()
                frac = min(1.0, max(0.0, (x - r.x) / max(1, r.w - 1)))
                self.cmd("seek", round(frac * 100, 2), "absolute-percent+keyframes")
                self.bar_next = 0.0
        elif (self.press_tile and not self.dragging and len(self.tiles) > 1
              and abs(e.x_root - self.press_pos[0]) + abs(e.y_root - self.press_pos[1]) > C.DRAG_THRESHOLD):
            self.dragging = True
            self.root.config(cursor="fleur")

    def on_release(self, e):
        tile, self.press_tile, self.pressing = self.press_tile, None, False
        if self.dragging:
            self.dragging = False
            self.root.config(cursor="")
            dest = self.tile_under_cursor((e.x_root, e.y_root))
            if tile in self.tiles and dest and dest is not tile:
                self.swap_tiles(tile, dest)
            return
        if self.skip_release:
            self.skip_release = False
            return
        if tile in self.tiles:
            self.click_was_zoomed = self.zoom and self.pin is tile
            self.set_pin(None if self.pin is tile else tile)

    def on_double(self, e):
        self.pressing, self.skip_release = True, True
        if self.press_in_bar:
            return
        tile = self.tile_under_cursor((e.x_root, e.y_root))
        if tile and not self.click_was_zoomed:   # the first click of this double-click already (un)pinned
            self.set_pin(tile, zoom=True)

    def swap_tiles(self, a: Tile, b: Tile):
        i, j = self.tiles.index(a), self.tiles.index(b)
        self.tiles[i], self.tiles[j] = b, a
        self.manual_order = True
        self.pos_at_focus_change = self.cursor()
        log.info("reorder %d <-> %d", i, j)
        self.relayout(rearrange=True)

    # ---- audio -------------------------------------------------------------
    def update_audio(self, dt: float) -> bool:
        """Crossfade: move every tile's gain towards 1 (focused) or 0. Returns True while fading."""
        step = 1.0 if C.CROSSFADE_MS <= 0 else dt * 1000 / C.CROSSFADE_MS
        fading = False
        for t in self.tiles:
            target = 1.0 if t is self.focus else 0.0
            if t.gain == target or not t.ipc:
                continue
            if t.gain == 0.0:                      # about to become audible: start from silence
                t.ipc.set_async("volume", 0)
                t.ipc.set_async("mute", False)
            t.gain = min(target, t.gain + step) if target > t.gain else max(target, t.gain - step)
            if t.gain == 0.0:                      # fully faded out: mute and put its own volume back
                t.ipc.set_async("mute", True)
                t.ipc.set_async("volume", t.volume)
            else:
                t.ipc.set_async("volume", round(t.volume * t.gain, 1))
            fading = fading or t.gain != target
        return fading

    def change_volume(self, delta):
        t = self.focus
        if t and t.ipc:
            t.volume = max(0, min(C.MAX_VOLUME, t.volume + delta))
            if t.gain == 1.0:
                t.ipc.set_async("volume", t.volume)
            t.ipc.post("show-text", f"Volume: {t.volume:.0f}%")

    def swap_random(self):
        """Replace the focused video with a random other video from the same folder."""
        t = self.focus
        if not t or not t.ipc or "://" in t.path:
            return
        open_now = {os.path.normcase(x.path) for x in self.tiles}
        pool = [p for p in expand([os.path.dirname(t.path)]) if os.path.normcase(p) not in open_now]
        if not pool:
            t.ipc.post("show-text", "No other videos in this folder")
            return
        self.load_into(t, random.choice(pool))

    def step_file(self, direction: int):
        """Page Down / Page Up: load the next / previous video from the focused tile's folder.

        Order is the folder's name order (same as opening the folder), wrapping at the ends and
        skipping files that another tile is already showing.
        """
        t = self.focus
        if not t or not t.ipc or "://" in t.path:
            return
        files = expand([os.path.dirname(t.path)])
        names = [os.path.normcase(f) for f in files]
        current = os.path.normcase(t.path)
        taken = {os.path.normcase(x.path) for x in self.tiles}
        # A file opened explicitly may not be a listed video type: start just outside the list.
        i = names.index(current) if current in names else (-1 if direction > 0 else 0)
        for k in range(1, len(files) + 1):
            j = (i + direction * k) % len(files)
            if names[j] not in taken:
                return self.load_into(t, files[j])
        t.ipc.post("show-text", "No other videos in this folder")

    def load_into(self, t: Tile, new: str):
        log.info("file swapped: %s -> %s", t.path, new)
        t.ipc.post("loadfile", new, "replace")
        t.ipc.post("show-text", os.path.basename(new), 2500)
        t.path, t.swapped, t.aspect_known, t.born = new, True, False, time.monotonic()
        self.bar_next = 0.0

    def toggle_help(self):
        """Show/hide the controls list. It is its own always-on-top window, so it is drawn above the
        videos whatever presentation mode mpv uses; while the pointer is on it, hover is suspended."""
        if self.help:
            win = self.help
            self.help = None
            fade(win, 0.0, 80, win.destroy)
            log.info("help hidden")
            return
        size = max(13, self.H // 64)                     # pixels; scales with the monitor
        bg, chip, dim, accent, pad = "#101214", "#2A3038", "#9AA4AF", "#6CB6FF", size * 2
        win = tk.Toplevel(self.root)
        win.withdraw()                                   # build it hidden, then map it once, on top
        win.overrideredirect(True)
        win.configure(bg=bg, padx=pad, pady=int(pad * 0.8))
        tk.Label(win, text="Tessera Wall controls", bg=bg, fg="white", font=("Segoe UI", -int(size * 1.5), "bold")
                 ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, size // 2))
        sections = dict(HELP_SECTIONS)

        def keys_cell(parent, keys):
            cell = tk.Frame(parent, bg=bg)
            if isinstance(keys, str):                    # a mouse action, in words
                tk.Label(cell, text=keys, bg=bg, fg="white", font=("Segoe UI", -size, "bold")).pack(side="left")
                return cell
            for i, alt in enumerate(keys):
                if i:
                    tk.Label(cell, text="/", bg=bg, fg=dim, font=("Segoe UI", -size)).pack(side="left", padx=2)
                for j, k in enumerate(alt.split("+")):
                    if j:
                        tk.Label(cell, text="+", bg=bg, fg=dim, font=("Segoe UI", -size)).pack(side="left", padx=2)
                    tk.Label(cell, text=k, bg=chip, fg="white", font=("Segoe UI", -int(size * 0.85), "bold"),
                             padx=max(4, size // 3), pady=0).pack(side="left")
            return cell

        for col, titles in enumerate(HELP_COLUMNS):
            column = tk.Frame(win, bg=bg)
            column.grid(row=1, column=col * 2, sticky="n")
            if col:
                tk.Frame(win, bg=bg, width=pad * 2).grid(row=1, column=1)      # space between the columns
            r = 0
            for title in titles:
                tk.Label(column, text=title.upper(), bg=bg, fg=accent, font=("Segoe UI", -int(size * 0.8), "bold")
                         ).grid(row=r, column=0, columnspan=2, sticky="w", pady=(size if r else 0, size // 3))
                r += 1
                for keys, action in sections[title]:
                    keys_cell(column, keys).grid(row=r, column=0, sticky="w", padx=(0, size), pady=2)
                    tk.Label(column, text=action, bg=bg, fg="#E6E6E6", font=("Segoe UI", -size)
                             ).grid(row=r, column=1, sticky="w", pady=2)
                    r += 1
        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        win.geometry(f"+{self.root.winfo_rootx() + (self.W - w) // 2}+{self.root.winfo_rooty() + (self.H - h) // 2}")
        tag = f"help{id(win)}"
        win.bind_class(tag, "<ButtonPress-1>", lambda e: (self.toggle_help(), "break")[1])

        def retag(widget):                               # a click anywhere on the list closes it
            widget.bindtags((tag,))
            for child in widget.winfo_children():
                retag(child)
        retag(win)
        opacity = float(os.environ.get("TESSERAWALL_HELP_OPACITY", C.HELP_OPACITY))  # env override is for tests:
        win.attributes("-alpha", 0.0)                    # screen captures cannot see translucent windows
        win.deiconify()
        win.attributes("-topmost", True)                 # must be set after mapping, or it sits behind the wall
        rounded = round_corners(win)
        win.lift()
        fade(win, min(1.0, opacity), 120)
        self.help = win
        self.root.focus_set()                            # keys keep going to the wall (never focus_force:
        log.info("help shown %dx%d rounded=%s", w, h, rounded)   # that would raise the wall over the list)

    # ---- menu bar at the top edge --------------------------------------------
    def update_topbar(self, pos, now):
        """Resting the pointer on the top edge of the wall shows a slim bar with the things a mouse-only
        user needs: add videos, the controls list, close. It hides when the pointer moves away."""
        x, y = pos[0] - self.root.winfo_rootx(), pos[1] - self.root.winfo_rooty()
        over_wall = 0 <= x < self.W
        if self.topbar is None:
            if over_wall and 0 <= y <= 2 and not self.covered and not self.pressing and not self.dragging:
                if self.top_since is None:
                    self.top_since = now
                elif now - self.top_since >= 0.25:
                    self.show_topbar()
            else:
                self.top_since = None
        elif not self.dialog_open and not (over_wall and -8 <= y <= self.topbar.winfo_height() + 24):
            self.hide_topbar()

    def show_topbar(self):
        u = max(13, self.H // 84)                        # 17 px on a 1440p screen
        bg, fg, hot = "#101214", "#E6E6E6", "#2A3038"
        bar = tk.Toplevel(self.root)
        bar.withdraw()
        bar.overrideredirect(True)
        bar.configure(bg=bg)
        tk.Label(bar, text="Tessera Wall", bg=bg, fg="#9AA4AF", font=("Segoe UI", -u, "bold")
                 ).pack(side="left", padx=(u, u))
        rects = {}

        def button(key, text, command, side="left", colour=fg, hover=hot):
            b = tk.Button(bar, text=text, command=command, font=("Segoe UI", -u), relief="flat", bd=0, bg=bg,
                          fg=colour, activebackground=hover, activeforeground="white", cursor="hand2",
                          takefocus=0, padx=u, pady=u // 2)
            b.bind("<Enter>", lambda e: b.config(bg=hover))
            b.bind("<Leave>", lambda e: b.config(bg=bg))
            b.pack(side=side)
            rects[key] = b

        button("add", "Add videos…", lambda: self.add_from_dialog(folder=False))
        button("folder", "Add a folder…", lambda: self.add_from_dialog(folder=True))
        button("controls", "Controls", lambda: (self.hide_topbar(), self.toggle_help()))
        button("settings", "Settings", lambda: (self.hide_topbar(), self.toggle_settings()))
        button("close", "Close  ✕", self.quit, side="right", hover="#C42B1C")
        bar.update_idletasks()
        bar.geometry(f"{self.W}x{bar.winfo_reqheight()}+{self.root.winfo_rootx()}+{self.root.winfo_rooty()}")
        bar.attributes("-alpha", 0.0)
        bar.deiconify()
        bar.attributes("-topmost", True)                 # after mapping, like the controls list
        bar.lift()
        self.topbar = bar
        bar.update()                                     # lay the buttons out now: their positions are logged
        fade(bar, 1.0, 120)
        log.info("top bar shown: %s", " ".join(
            f"{k}={b.winfo_rootx()},{b.winfo_rooty()},{b.winfo_width()},{b.winfo_height()}" for k, b in rects.items()))

    def hide_topbar(self):
        if self.topbar:
            bar = self.topbar
            self.topbar, self.top_since = None, None      # gone as far as the wall is concerned
            fade(bar, 0.0, 80, bar.destroy)
            self.root.focus_set()                        # keys go back to the wall
            log.info("top bar hidden")

    def add_from_dialog(self, folder: bool):
        self.dialog_open = True                          # keeps the bar (the dialog's parent) alive
        try:
            if folder:
                picked = filedialog.askdirectory(parent=self.topbar, title="Add a folder of videos")
                picked = [picked] if picked else []
            else:
                patterns = " ".join("*" + e for e in sorted(C.VIDEO_EXTS))
                picked = list(filedialog.askopenfilenames(parent=self.topbar, title="Add videos",
                                                          filetypes=[("Videos", patterns), ("All files", "*.*")]))
        finally:
            self.dialog_open = False
        for p in expand(picked):
            self.incoming.put(p)
        self.hide_topbar()

    # ---- files that would not play --------------------------------------------
    def report_failed(self):
        """Tell the user, in plain words, about files mpv could not open."""
        if not self.failed:
            return
        names = [os.path.basename(p) or p for p in self.failed]
        if len(names) == 1:
            msg = f"Couldn't play \"{names[0]}\". The file may be damaged or not a video."
        else:
            msg = f"Couldn't play {len(names)} files: " + ", ".join(names[:3]) + ("…" if len(names) > 3 else "")
        if not self.tiles:                               # nothing at all could be played: say so and leave
            log.info("nothing playable: %s", msg)
            self.failed.clear()
            self.closing = True
            if os.environ.get("TESSERAWALL_NO_DIALOGS") != "1":   # tests cannot click a message box
                self.root.withdraw()
                messagebox.showerror("Tessera Wall", msg)
            self.root.destroy()
            return
        target = next((t for t in self.tiles if t.ipc), None)
        if target:                                       # otherwise wait until a player can show it
            log.info("could not play: %s", "; ".join(self.failed))
            target.ipc.post("show-text", msg, 7000)
            self.failed.clear()

    def toggle_settings(self):
        if self.settings:
            self.settings.close()        # saves anything pending, then calls settings_closed
            return
        self.settings = settings_window(self.root, self.config_path, self.apply_settings, self.settings_closed,
                                        topmost=True)

    def settings_closed(self):
        self.settings = None
        self.root.focus_set()
        log.info("settings hidden")

    def apply_settings(self, names):
        """A setting was changed in the settings window: take it up now, without on-screen messages."""
        self.reload_config(quiet=True)
        if "LAYOUT_MODE" in names or "CROP_MODE" in names:   # these are otherwise only starting values
            self.layout_mode = C.LAYOUT_MODE if C.LAYOUT_MODE in LAYOUT_MODES else self.layout_mode
            self.crop_mode = C.CROP_MODE if C.CROP_MODE in CROP_MODES else self.crop_mode
            self.relayout(rearrange=True)

    def on_escape(self):
        if self.settings:
            self.toggle_settings()
        elif self.help:
            self.toggle_help()
        elif self.pin:
            self.set_pin(None)
        else:
            self.quit()

    def update_bar(self, now):
        """Draw a progress line + time on the focused tile via mpv's OSD; clear it from the old one."""
        if C.SEEKBAR_HEIGHT <= 0:
            return
        t = self.focus
        if self.bar_tile and self.bar_tile is not t and self.bar_tile.ipc:
            self.bar_tile.ipc.post({"name": "osd-overlay", "id": 1, "format": "none", "data": ""})
        self.bar_tile = t
        if not t or not t.ipc or not t.target or now < self.bar_next:
            return
        self.bar_next = now + 0.25
        pct, pos, dur = t.ipc.get("percent-pos"), t.ipc.get("time-pos"), t.ipc.get("duration")
        if pct is None:
            return
        w, h, bh = t.target.w, t.target.h, C.SEEKBAR_HEIGHT
        y0, x1 = h - bh, round(w * pct / 100)

        def box(xa, xb, colour, alpha, top=y0):
            return ("{\\an7\\pos(0,0)\\bord0\\shad0\\1c&H%s&\\1a&H%s&\\p1}m %d %d l %d %d %d %d %d %d{\\p0}"
                    % (colour, alpha, xa, top, xb, top, xb, h, xa, h))

        size = max(16, min(28, h // 28))
        text = "%s / %s%s" % (fmt_time(pos or 0), fmt_time(dur or 0), "   [pinned]" if self.pin is t else "")
        if C.SEEKBAR_SHOW_NAME:
            # File name first, shortened to what fits; { } \ would be read as ASS formatting codes.
            name = t.path if "://" in t.path else os.path.basename(t.path)
            name = name.replace("\\", "/").replace("{", "(").replace("}", ")")
            room = int((w - 20) / (size * 0.5)) - len(text) - 3   # ~0.46 of the font size per Latin letter
            if room >= 8:
                text = (name if len(name) <= room else name[:room - 1] + "…") + "   " + text
        label = "{\\an1\\pos(10,%d)\\fnSegoe UI\\fs%d\\bord1.5\\shad0\\1c&HE6E6E6&\\3c&H141210&}%s" % (y0 - 6, size, text)
        # Dark track (2 px taller, so the bar has an edge on bright video) + coloured fill (ASS colours are BGR).
        # Soft dark band behind the text: five stacked boxes, each ~15 % black, overlapping more towards
        # the bottom, so it darkens gradually instead of ending in a hard edge.
        band = int(size * 2.6)
        scrim = [box(0, w, "000000", "D8", y0 - band + i * band // 5) for i in range(5)]
        data = "\n".join(scrim + [box(0, w, "000000", "50", y0 - 2), box(0, x1, C.SEEKBAR_COLOUR, "00"), label])
        t.ipc.post({"name": "osd-overlay", "id": 1, "format": "ass-events", "data": data,
                       "res_x": w, "res_y": h})

    def update_cursor(self, pos, now):
        if pos != self.last_pos:
            self.last_pos, self.last_move = pos, now
            if self.cursor_hidden:
                self.cursor_hidden = False
                if not self.dragging:
                    self.root.config(cursor="")
        elif (C.CURSOR_HIDE_SECONDS > 0 and not self.cursor_hidden
              and now - self.last_move > C.CURSOR_HIDE_SECONDS):
            self.cursor_hidden = True
            self.root.config(cursor="none")  # tk swaps in a blank cursor; mouse input reaches tk, not mpv

    # ---- session -----------------------------------------------------------
    def save_session(self):
        if not self.session_path:
            return
        items = []
        for t in self.tiles:
            pos = t.ipc.get("time-pos") if t.ipc else None
            items.append({"path": t.path, "pos": round(pos or 0, 1)})
        if not items:
            return
        try:
            os.makedirs(os.path.dirname(self.session_path), exist_ok=True)
            with open(self.session_path, "w", encoding="utf-8") as f:
                json.dump({"videos": items}, f, indent=1)
            log.info("session saved (%d videos) to %s", len(items), self.session_path)
        except OSError as e:
            log.error("could not save session: %s", e)

    # ---- main loop ---------------------------------------------------------
    def tick(self):
        if self.closing:
            return
        now = time.monotonic()
        for t in list(self.tiles):
            if t.proc.poll() is not None:
                if t.proc.returncode != 0 and not t.aspect_known:   # never showed a picture
                    self.failed.append(t.path)
                self.remove(t, f"mpv exited with {t.proc.returncode}")
            elif t.ipc is None:
                try:
                    t.ipc = MpvIpc(t.pipe, timeout=0)
                    self.need_layout = True  # so panscan gets sent
                    if not self.hint_shown and (self.notice or C.SHOW_HELP_HINT):
                        self.hint_shown = True   # a startup problem with the settings file wins over the hint
                        t.ipc.post("show-text", self.notice or "H: controls     Esc: exit     Top edge: menu", 8000 if self.notice else 4000)
                except OSError:
                    if now - t.born > CONNECT_TIMEOUT:
                        self.remove(t, "no IPC connection")
            elif not t.aspect_known:
                # A file mpv cannot open leaves the player idle with an empty window (it does not exit,
                # because of --force-window). Seen twice, 0.6 s apart, so a brief idle moment while a
                # file is still being opened is not mistaken for a failure.
                if t.ipc.get("idle-active"):
                    if t.idle_since is None:
                        t.idle_since = now
                    elif now - t.idle_since > 0.6:
                        self.failed.append(t.path)
                        self.remove(t, "mpv could not open the file")
                    continue
                t.idle_since = None
                if t.swapped and t.ipc.get("path") != t.path:
                    continue  # mpv is still showing the previous file; its size would be wrong
                w, h = t.ipc.get("dwidth"), t.ipc.get("dheight")
                if w and h:
                    rot = t.ipc.get("video-params/rotate") or 0
                    t.aspect = h / w if rot in (90, 270) else w / h
                    t.aspect_known = True
                    self.need_layout = True
                    log.info("aspect known %.4f (%sx%s rot=%s): %s", t.aspect, w, h, rot, t.path)
                elif now - t.born > ASPECT_TIMEOUT:
                    t.aspect_known = True
                    log.info("aspect unknown, keeping 16:9: %s", t.path)
        while True:
            try:
                self.add(self.incoming.get_nowait())
            except queue.Empty:
                break
        self.report_failed()
        if self.closing:
            return
        if not self.tiles:
            return self.quit()
        if self.need_layout:
            self.relayout(rearrange=True)
        pos = self.cursor()
        self.hover(pos)
        self.update_cursor(pos, now)
        self.update_bar(now)
        self.update_topbar(pos, now)
        self.show_update_notice()
        fading =self.update_audio(min(0.05, now - self.last_tick))
        self.last_tick = now
        animating = self.animate()
        self.root.after(16 if (animating or fading) else C.TICK_MS, self.tick)

    # ---- update notice -----------------------------------------------------
    def show_update_notice(self):
        """Once per run, when the background check has found a newer release: tell the user on one
        video. Waits for the launch hint so it does not overwrite it, and avoids that video."""
        found = self.updates.result if self.updates else None
        if self.update_shown or not found or not self.hint_shown:
            return
        tiles = [t for t in self.tiles if t.ipc]
        if not tiles:
            return
        self.update_shown = True
        tile = tiles[-1]
        tile.ipc.post("show-text", f"Tessera Wall {found['version']} is available.  Press U for the download page.", 8000)
        log.info("update notice shown on tile %d: %s", self.tiles.index(tile), found["version"])

    def open_update(self):
        found = self.updates.result if self.updates else None
        if found:
            updates.open_release_page(found["url"])

    # ---- input -------------------------------------------------------------
    def cmd(self, *args):
        if self.focus and self.focus.ipc:
            self.focus.ipc.post(*args)

    def pause_all(self):
        ref = self.focus or (self.tiles[0] if self.tiles else None)
        if not ref or not ref.ipc:
            return
        state = not ref.ipc.get("pause")
        for t in self.tiles:
            if t.ipc:
                t.ipc.set_async("pause", state)

    def reload_config(self, quiet: bool = False):
        """F5: re-read the settings file without restarting. A file that cannot be parsed changes
        nothing; a single bad or unknown key is skipped and the rest apply. Launch-time settings
        (mpv_path, safe_rendering, extra_mpv_args, use_user_mpv_config, mpv_log_dir) only affect videos
        opened afterwards; layout_mode / crop_mode are starting values and do not override L / C."""
        try:
            changed, problems = C.load(self.config_path)
        except Exception as e:  # unreadable or not valid TOML: report it and change nothing
            msg = f"{type(e).__name__}: {e}"
            log.error("config reload failed: %s", msg)
            for t in self.tiles:
                if t.ipc:
                    t.ipc.post("show-text", "Settings NOT reloaded - " + msg[:120], 6000)
            return
        self.mpv = C.find_mpv() or self.mpv
        log.info("config reloaded, changed: %s", ", ".join(sorted(changed)) or "nothing")
        for p in problems:
            log.warning("config: %s", p)
        note = f"Settings reloaded ({len(changed)} changed)" + (f", {len(problems)} ignored: {problems[0]}"
                                                                 if problems else "")
        for t in self.tiles:
            if t.ipc:
                t.ipc.post({"name": "osd-overlay", "id": 1, "format": "none", "data": ""})  # bar may be off now
                if not quiet or problems:      # the settings window applies changes silently
                    t.ipc.post("show-text", note[:160], 5000 if problems else 2000)
        self.bar_tile, self.bar_next = None, 0.0
        if self.cursor_hidden:
            self.cursor_hidden = False
            self.root.config(cursor="")
        for t in self.tiles:
            t.placed = None
        self.pos_at_focus_change = self.cursor()
        self.relayout(rearrange=True)

    def seek(self, direction, everyone=False):
        seconds = direction * C.SEEK_SECONDS   # read now, so a config reload (F5) takes effect
        for t in (self.tiles if everyone else [self.focus]):
            if t and t.ipc:
                t.ipc.post("seek", seconds, "relative")
        self.bar_next = 0.0

    def restart_all(self):
        log.info("restart all")
        for t in self.tiles:
            if t.ipc:
                t.ipc.post("seek", 0, "absolute")
        self.bar_next = 0.0

    def reset(self):
        self.pin, self.zoom = None, False
        self.set_focus(None)

    def bind_keys(self):
        bind_all = self.root.bind_all

        def b(seq, fn):
            """bind_all, plus the upper-case twin of a letter key: with Caps Lock on (or Shift held)
            tk reports <R>, not <r>, and without this every letter key silently stops working."""
            bind_all(seq, fn)
            if len(seq) == 3 and seq[1].isalpha() and seq[1].islower():
                bind_all(f"<{seq[1].upper()}>", fn)

        b("<Escape>", lambda e: self.on_escape())
        b("<q>", lambda e: self.quit())
        b("<space>", lambda e: self.cmd("cycle", "pause"))
        b("<Shift-space>", lambda e: self.pause_all())
        b("<Left>", lambda e: self.seek(-1))
        b("<Right>", lambda e: self.seek(1))
        b("<Shift-Left>", lambda e: self.seek(-1, everyone=True))
        b("<Shift-Right>", lambda e: self.seek(1, everyone=True))
        b("<MouseWheel>", lambda e: self.seek(1 if e.delta > 0 else -1))
        b("<Shift-MouseWheel>", lambda e: self.seek(1 if e.delta > 0 else -1, everyone=True))
        b("<r>", lambda e: self.restart_all())
        b("<Up>", lambda e: self.change_volume(C.VOLUME_STEP))
        b("<Down>", lambda e: self.change_volume(-C.VOLUME_STEP))
        b("<n>", lambda e: self.swap_random())
        b("<F5>", lambda e: self.reload_config())
        b("<s>", lambda e: self.toggle_settings())
        b("<u>", lambda e: self.open_update())
        for seq in ("<h>", "<question>", "<F1>"):
            b(seq, lambda e: self.toggle_help())
        b("<l>", lambda e: self.cycle_mode("layout_mode", LAYOUT_MODES, "Layout"))
        b("<c>", lambda e: self.cycle_mode("crop_mode", CROP_MODES, "Crop"))
        b("<Next>", lambda e: self.step_file(1))     # Page Down
        b("<Prior>", lambda e: self.step_file(-1))   # Page Up
        b("<ButtonRelease-1>", self.on_release)
        for seq in ("<x>", "<Delete>", "<Button-2>"):
            b(seq, lambda e: self.cmd("quit"))
        b("<Key-0>", lambda e: self.reset())
        b("<ButtonPress-1>", self.on_press)
        b("<B1-Motion>", self.on_drag)
        b("<Double-Button-1>", self.on_double)

    def quit(self):
        if self.closing:
            return
        self.closing = True
        log.info("quit")
        self.save_session()
        for t in self.tiles:
            if t.ipc:
                t.ipc.command("quit", timeout=0.3)
        for t in self.tiles:
            try:
                t.proc.wait(1)
            except subprocess.TimeoutExpired:
                t.proc.kill()
        self.root.destroy()

    def kill_all(self):
        for t in self.tiles:
            if t.proc.poll() is None:
                t.proc.kill()


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


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def monitor_under_cursor() -> tuple[int, int, int, int]:
    """(x, y, width, height) in real pixels of the monitor the pointer is on."""
    user32.MonitorFromPoint.restype = wintypes.HANDLE
    user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    user32.GetMonitorInfoW(user32.MonitorFromPoint(pt, 2), ctypes.byref(info))   # 2 = nearest monitor
    r = info.rcMonitor
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def claim_control_pipe(paths: list) -> Listener | None:
    """Become the one running tesserawall, or hand `paths` to the one that is. Returns the control-pipe
    listener if this process is now the wall, or None if the paths were handed over.

    Explorer starts one process per selected file (right-click verb, Send To, drop on a shortcut),
    all at the same moment, so this must be race-free. It is because creating the listener is the
    claim: Python creates the pipe's first instance with FILE_FLAG_FIRST_PIPE_INSTANCE, so exactly
    one process succeeds and the rest get "access denied" and retry as clients. The claim is made
    before any window opens, so the winner can accept connections within milliseconds."""
    deadline = time.monotonic() + 15
    while True:
        try:  # an empty list just detects a running wall
            with Client(CONTROL_PIPE, family="AF_PIPE") as c:
                c.send_bytes(json.dumps(paths).encode("utf-8"))   # JSON, never pickle (see listen())
            return None
        except OSError:
            pass
        try:
            return Listener(CONTROL_PIPE, family="AF_PIPE")
        except OSError:  # another launch claimed it between our two attempts: send to it instead
            if time.monotonic() > deadline:
                raise
            time.sleep(0.05)


def explorer_sorted(paths: list) -> list:
    """Paths in Explorer's name order ("clip 2" before "clip 10"), case-insensitive."""
    cmp = ctypes.windll.shlwapi.StrCmpLogicalW
    cmp.argtypes, cmp.restype = [wintypes.LPCWSTR, wintypes.LPCWSTR], ctypes.c_int
    return sorted(paths, key=functools.cmp_to_key(cmp))


def gather_batch(incoming: queue.Queue, claimed_at: float) -> list:
    """Collect files that sibling launches hand over just after this one became the wall.

    Right-click on several files starts one process per file at the same moment; the others arrive
    here in a random order within a fraction of a second. Waits until nothing has arrived for
    BATCH_QUIET seconds (at least BATCH_MIN after the claim, at most BATCH_MAX)."""
    got, last = [], claimed_at
    while True:
        try:
            got.append(incoming.get(timeout=0.03))
            last = time.monotonic()
            continue
        except queue.Empty:
            pass
        now = time.monotonic()
        if now - claimed_at >= BATCH_MAX or (now - claimed_at >= BATCH_MIN and now - last >= BATCH_QUIET):
            return got


def listen(listener: Listener, incoming: queue.Queue):
    """Receive paths from later launches (Send To / shortcut drop while a wall is running)."""
    while True:
        try:
            with listener.accept() as conn:
                # Raw bytes holding a JSON list of paths. Never conn.recv(): that unpickles, and a
                # pickle from another local program could run code inside the wall.
                paths = json.loads(conn.recv_bytes(1 << 20).decode("utf-8"))
                if isinstance(paths, list):
                    for p in paths[:C.MAX_VIDEOS * 4]:
                        if isinstance(p, str):
                            incoming.put(p)
        except Exception as e:  # a broken client must not kill the listener
            log.error("listener: %s", e)


def enable_drop(root, incoming: queue.Queue):
    """Optional: accept files dropped on the running wall. Needs `pip install tkinterdnd2`."""
    try:
        from tkinterdnd2 import DND_FILES
        root.drop_target_register(DND_FILES)
        root.dnd_bind("<<Drop>>", lambda e: [incoming.put(p) for p in expand(list(root.tk.splitlist(e.data)))])
        log.info("drag-and-drop enabled")
    except Exception as e:
        log.info("drag-and-drop onto the wall disabled: %s", e)


def dark_title_bar(win) -> bool:
    """Give a window a dark title bar matching its body (#101214), through the same Windows option
    dark-mode apps use. Windows 11 honours the exact colour; Windows 10 20H1+ uses its own dark grey."""
    try:
        win.update_idletasks()
        hwnd = wintypes.HWND(user32.GetAncestor(win.winfo_id(), GA_ROOT))
        dwm = ctypes.windll.dwmapi
        on = ctypes.c_int(1)
        ok = dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on)) == 0   # immersive dark mode
        caption, text = ctypes.c_uint(0x00141210), ctypes.c_uint(0x00E6E6E6)                  # COLORREF = 0x00BBGGRR
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption), 4)
        dwm.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(text), 4)
        return ok
    except Exception as e:
        log.info("dark title bar unavailable: %s", e)
        return False


def fade(win, to: float, ms: int, then=None):
    """Change a window's opacity smoothly, then run `then`. Quietly stops if the window is gone.
    Ending at 1.0 makes the window opaque again (tk drops the layered style), which screen capture needs."""
    try:
        start = float(win.attributes("-alpha"))
    except tk.TclError:
        return
    steps = max(1, ms // 15)

    def step(i):
        try:
            win.attributes("-alpha", start + (to - start) * i / steps)
        except tk.TclError:
            return
        if i < steps:
            win.after(15, step, i + 1)
        elif then:
            then()
    step(1)


def round_corners(win) -> bool:
    """Windows 11: round a borderless window's corners like a normal one. No effect on Windows 10."""
    try:
        hwnd = wintypes.HWND(user32.GetAncestor(win.winfo_id(), GA_ROOT))
        pref = ctypes.c_int(2)                                   # DWMWCP_ROUND
        return ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4) == 0
    except Exception:
        return False


def hover(widget, normal: str, hot: str):
    """Lighten a flat button while the pointer is over it."""
    widget.bind("<Enter>", lambda e: widget.config(bg=hot), add="+")
    widget.bind("<Leave>", lambda e: widget.config(bg=normal), add="+")


def set_icon(win):
    """Window/taskbar icon. The files sit next to the program (build.ps1 copies them into packaged builds)."""
    try:
        win.iconbitmap(default=os.path.join(C.app_dir(), "tesserawall.ico"))
    except Exception:
        try:
            win._icon = tk.PhotoImage(master=win, file=os.path.join(C.app_dir(), "tesserawall.png"))
            win.iconphoto(True, win._icon)
        except Exception as e:
            log.info("no window icon: %s", e)


def make_root():
    try:
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
    except Exception:
        root = tk.Tk()
    set_icon(root)
    return root


# The settings window: the few settings worth a control, in plain words. Everything else stays in the
# settings file. Kinds: ("section", title) | ("scale", NAME, label, low, high, step)
#   | ("choice", NAME, label, [(value, text), ...]) | ("check", NAME, label)
#   | ("check", NAME, label, value_when_on, value_when_off)
# A scale's last item formats its value for display, e.g. "{} ms".
SETTINGS_UI = [
    ("section", "When you point at a video"),
    ("scale", "FOCUS_SCALE", "How much it grows", 1.0, 2.5, 0.05, "{:.2f}\u00d7"),
    ("scale", "DIM_UNFOCUSED", "How much the other videos dim", 0, 60, 1, "{} %"),
    ("scale", "CROSSFADE_MS", "Sound fade between videos", 0, 1000, 10, "{} ms"),
    ("section", "Layout"),
    ("choice", "LAYOUT_MODE", "Arrangement", [("grow", "Grow on hover"), ("flat", "Best fit"), ("grid", "Even grid")]),
    ("choice", "CROP_MODE", "Black bars", [("limited", "Crop a little"), ("fill", "Crop to fill"), ("fit", "Never crop")]),
    ("section", "Other"),
    ("check", "FOCUS_OUTLINE", "Outline the video you point at"),
    ("check", "SEEKBAR_SHOW_NAME", "Show the file name on the video you point at"),
    ("check", "CURSOR_HIDE_SECONDS", "Hide the pointer when it is not moving", 2.0, 0.0),
    ("check", "UNFOCUSED_SKIP", "Save power: lower frame rate on the other videos (they look choppy)", "nonref", ""),
    ("check", "UPDATE_CHECK", "Check for a new version once a week"),
    ("check", "SAFE_RENDERING", "Compatibility mode (recommended; applies to videos opened next)"),
]


def settings_window(parent, config_path: str, apply, on_close, topmost: bool = False) -> tk.Toplevel:
    """Build and show the settings window. Each change is written to the settings file at once and
    `apply(names)` is called, so the wall behind it updates while the user watches.

    Its widgets are taken out of the app-wide key and mouse bindings (bindtag "all"): otherwise Space
    on a checkbox would also pause a video, and clicks would reach the wall's handlers.
    """
    u = max(12, parent.winfo_screenheight() // 90)
    bg, panel, fg, dim, accent = "#101214", "#1B1F24", "#E6E6E6", "#9AA4AF", "#6CB6FF"
    win = tk.Toplevel(parent)
    win.withdraw()
    win.title("Tessera Wall settings")
    win.configure(bg=bg, padx=u * 2, pady=int(u * 1.5))
    win.resizable(False, False)

    def font(scale=1.0, bold=False):
        return ("Segoe UI", -int(u * scale), "bold") if bold else ("Segoe UI", -int(u * scale))

    pending: dict = {}
    timer = [None]
    ready = [False]          # tk.Scale fires its command when first given a value; ignore until shown
    refreshers, located = [], {}

    def flush():
        timer[0] = None
        if not pending:
            return
        values = dict(pending)
        pending.clear()
        try:
            C.set_user_values(config_path, values)
            apply(list(values))
            status.config(text="Saved.", fg=dim)
            log.info("settings saved: %s", ", ".join(f"{k.lower()}={v!r}" for k, v in values.items()))
        except Exception as e:
            status.config(text=f"Could not save: {e}"[:90], fg="#FF8A80")
            log.error("settings window: %s: %s", type(e).__name__, e)

    def change(name, value, delay=0):
        if not ready[0]:
            return
        pending[name] = value
        if timer[0]:
            win.after_cancel(timer[0])
        timer[0] = win.after(delay, flush)

    tk.Label(win, text="Settings", bg=bg, fg="white", font=font(1.7, True)).pack(anchor="w")
    for item in SETTINGS_UI:
        kind = item[0]
        if kind == "section":
            tk.Label(win, text=item[1], bg=bg, fg=accent, font=font(1.0, True)).pack(anchor="w", pady=(u, u // 4))
        elif kind == "scale":
            _, name, label, low, high, step, unit = item
            whole = isinstance(C.DEFAULTS[name], int)
            row = tk.Frame(win, bg=bg)
            row.pack(fill="x")
            tk.Label(row, text=label, bg=bg, fg=fg, font=font(), anchor="w", width=34).pack(side="left")
            # A small canvas slider: tk.Scale cannot give its handle a colour of its own, so on a dark
            # window the handle disappears. Click or drag anywhere on the track to set the value.
            sw_, sh_, pad = u * 15, int(u * 1.6), u // 2
            cv = tk.Canvas(row, width=sw_, height=sh_, bg=bg, highlightthickness=0, cursor="hand2")
            cv.pack(side="left")
            shown = tk.Label(row, bg=bg, fg=dim, font=font(0.95), width=8, anchor="e")
            shown.pack(side="left")

            def draw(value, cv=cv, shown=shown, low=low, high=high, whole=whole, sw_=sw_, sh_=sh_, pad=pad,
                     unit=unit):
                x = pad + (sw_ - 2 * pad) * (value - low) / (high - low)
                cv.delete("all")
                cv.create_rectangle(pad, sh_ // 2 - 2, sw_ - pad, sh_ // 2 + 2, fill="#2A3038", width=0)
                cv.create_rectangle(pad, sh_ // 2 - 2, x, sh_ // 2 + 2, fill=accent, width=0)
                cv.create_oval(x - pad, sh_ // 2 - pad, x + pad, sh_ // 2 + pad, fill="white", width=0)
                shown.config(text=unit.format(int(value) if whole else value))

            def pick(e, n=name, draw=draw, low=low, high=high, step=step, whole=whole, sw_=sw_, pad=pad):
                frac = min(1.0, max(0.0, (e.x - pad) / (sw_ - 2 * pad)))
                value = low + round(frac * (high - low) / step) * step
                value = int(round(value)) if whole else round(value, 2)
                draw(value)
                change(n, value, 150)

            cv.bind("<Button-1>", pick)
            cv.bind("<B1-Motion>", pick)
            refreshers.append(lambda draw=draw, n=name: draw(getattr(C, n)))
            located[name] = cv
        elif kind == "choice":
            _, name, label, options = item
            row = tk.Frame(win, bg=bg)
            row.pack(fill="x", pady=(u // 4, 0))
            tk.Label(row, text=label, bg=bg, fg=fg, font=font(), anchor="w", width=14).pack(side="left")
            var = tk.StringVar(win)
            group = {}
            for value, text in options:
                b = tk.Radiobutton(row, text=text, value=value, variable=var, indicatoron=False, font=font(0.95),
                                   bg=panel, fg=fg, selectcolor=accent, activebackground="#2A3038",
                                   activeforeground=fg, relief="flat", bd=0, padx=u, pady=u // 3, cursor="hand2",
                                   command=lambda n=name, v=var: change(n, v.get()))
                b.pack(side="left", padx=(0, u // 3))
                hover(b, panel, "#2A3038")                 # the selected one shows its blue select colour
                located[f"{name}:{value}"] = group[value] = b
            # dark text on the selected (blue) button, light text on the others
            var.trace_add("write", lambda *a, v=var, g=group: [b.config(fg="#0B0D0F" if k == v.get() else fg)
                                                                for k, b in g.items()])
            refreshers.append(lambda v=var, n=name: v.set(getattr(C, n)))
        elif kind == "check":
            name, label = item[1], item[2]
            on, off = (item[3], item[4]) if len(item) > 3 else (True, False)
            var = tk.BooleanVar(win)
            c = tk.Checkbutton(win, text=label, variable=var, bg=bg, fg=fg, selectcolor=panel, font=font(),
                               activebackground=bg, activeforeground=fg, anchor="w", cursor="hand2",
                               command=lambda n=name, v=var, a=on, b=off: change(n, a if v.get() else b))
            c.pack(anchor="w")
            refreshers.append(lambda v=var, n=name, b=off: v.set(getattr(C, n) != b))
            located[name] = c

    def refresh():
        ready[0] = False
        for fn in refreshers:
            fn()
        win.after(250, lambda: ready.__setitem__(0, True))

    def reset():
        names = [item[1] for item in SETTINGS_UI if item[0] != "section"]
        ready[0] = True
        for n in names:
            pending[n] = None
        flush()
        refresh()

    def close():
        if timer[0]:
            win.after_cancel(timer[0])
        flush()
        on_close()
        fade(win, 0.0, 80, win.destroy)

    status = tk.Label(win, text="Changes are saved as you make them.", bg=bg, fg=dim, font=font(0.9))
    status.pack(anchor="w", pady=(u, u // 2))
    row = tk.Frame(win, bg=bg)
    row.pack(fill="x")

    def button(key, text, command, primary=False, side="left"):
        b = tk.Button(row, text=text, command=command, font=font(1.0, primary), relief="flat", bd=0, cursor="hand2",
                      bg=accent if primary else panel, fg="#0B0D0F" if primary else fg, padx=u, pady=u // 3,
                      activebackground="#8FC8FF" if primary else "#2A3038", activeforeground="#0B0D0F" if primary else fg)
        b.pack(side=side, padx=(0, u // 2) if side == "left" else (u // 2, 0))
        hover(b, accent if primary else panel, "#8FC8FF" if primary else "#2A3038")
        located[key] = b

    button("reset", "Reset these to defaults", reset)
    button("file", "Open the settings file", lambda: os.startfile(config_path)
           if os.environ.get("TESSERAWALL_NO_DIALOGS") != "1" else log.info("would open %s", config_path))
    button("done", "Done", close, primary=True, side="right")

    def detach(widget):      # no app-wide bindings inside this window (see docstring)
        widget.bindtags(tuple(t for t in widget.bindtags() if t != "all"))
        for child in widget.winfo_children():
            detach(child)
    detach(win)
    win.close = close
    win.bind("<Escape>", lambda e: close())
    win.protocol("WM_DELETE_WINDOW", close)

    refresh()
    win.update_idletasks()
    w, h = win.winfo_reqwidth(), win.winfo_reqheight()
    mx, my, mw, mh = monitor_under_cursor()
    win.geometry(f"+{mx + (mw - w) // 2}+{my + (mh - h) // 2}")
    win.attributes("-alpha", 0.0)
    win.deiconify()
    log.info("settings dark title bar: %s", dark_title_bar(win))
    if topmost:              # above the full-screen wall; must be set after the window is mapped
        win.attributes("-topmost", True)
    win.lift()
    win.focus_force()
    win.update()
    fade(win, 1.0, 120)
    log.info("settings shown: %s", " ".join(
        f"{k}={b.winfo_rootx()},{b.winfo_rooty()},{b.winfo_width()},{b.winfo_height()}" for k, b in located.items()))
    return win


def start_window(last_session: list, incoming: queue.Queue, checker: "updates.Checker | None" = None,
                 config_path: str | None = None) -> list | None:
    """The front door: shown when tesserawall is started with nothing to play.

    Returns the chosen items (paths, or (path, position) pairs for a resumed session), or None if the
    window was closed. It is a separate Tk root that is destroyed before the wall is created.
    """
    root = make_root()
    root.withdraw()                                       # built hidden and shown once, finished
    root.title("Tessera Wall")
    chosen: list = []
    u = max(12, root.winfo_screenheight() // 90)          # layout unit in pixels; 16 on a 1440p screen
    bg, panel, fg, dim, accent = "#101214", "#1B1F24", "#E6E6E6", "#9AA4AF", "#6CB6FF"
    w, h = u * 42, u * 31
    mx, my, mw, mh = monitor_under_cursor()
    root.geometry(f"{w}x{h}+{mx + (mw - w) // 2}+{my + (mh - h) // 2}")
    root.configure(bg=bg, padx=u * 2, pady=u * 2)
    root.resizable(False, False)

    def font(scale=1.0, bold=False):
        return ("Segoe UI", -int(u * scale), "bold") if bold else ("Segoe UI", -int(u * scale))

    def finish(items):
        if items:
            chosen.extend(items)
            root.destroy()
        else:
            status.config(text="No videos found in that selection.")

    def pick_files():
        patterns = " ".join("*" + e for e in sorted(C.VIDEO_EXTS))
        files = filedialog.askopenfilenames(parent=root, title="Choose videos",
                                            filetypes=[("Videos", patterns), ("All files", "*.*")])
        if files:
            finish(expand(list(files)))

    def pick_folder():
        folder = filedialog.askdirectory(parent=root, title="Choose a folder of videos")
        if folder:
            finish(expand([folder]))

    tk.Label(root, text="Tessera Wall", bg=bg, fg="white", font=font(2.0, True)).pack(anchor="w")
    tk.Label(root, text="Play several videos at once, side by side.", bg=bg, fg=dim, font=font(1.05)
             ).pack(anchor="w", pady=(0, u))
    can_drop = hasattr(root, "drop_target_register")
    drop = tk.Canvas(root, bg=bg, highlightthickness=0, cursor="hand2", height=u * 8)
    drop.pack(fill="both", expand=True, pady=(0, u))
    zone = {"state": "idle"}

    def draw_zone(state=None):
        zone["state"] = state or zone["state"]
        w_, h_ = drop.winfo_width(), drop.winfo_height()
        if w_ < 10:
            return
        lit = zone["state"] == "drag"
        warm = zone["state"] == "hover"
        drop.delete("all")
        drop.create_rectangle(1, 1, w_ - 2, h_ - 2, dash=(6, 5), width=2,
                              outline=accent if lit else ("#55606C" if warm else "#3A424C"),
                              fill="#18263A" if lit else ("#1F242B" if warm else panel))
        drop.create_text(w_ // 2, h_ // 2 - u // 2, fill=accent if lit else fg, font=font(1.3),
                         text="Release to play" if lit else
                         ("Drop videos or folders here" if can_drop else "Choose what to play"))
        drop.create_text(w_ // 2, h_ // 2 + u, fill=dim, font=font(0.9),
                         text="" if lit else "or click to choose videos")

    drop.bind("<Configure>", lambda e: draw_zone())
    drop.bind("<Enter>", lambda e: draw_zone("hover") if zone["state"] != "drag" else None)
    drop.bind("<Leave>", lambda e: draw_zone("idle") if zone["state"] != "drag" else None)
    drop.bind("<Button-1>", lambda e: pick_files())
    buttons = {}

    def button(key, text, command, primary=False):
        b = tk.Button(root, text=text, command=command, font=font(1.05, primary), relief="flat", bd=0,
                      bg=accent if primary else panel, fg="#0B0D0F" if primary else fg, cursor="hand2",
                      activebackground="#8FC8FF" if primary else "#2A3038", activeforeground="#0B0D0F" if primary else fg,
                      pady=u // 2)
        b.pack(fill="x", pady=(0, u // 2))
        hover(b, accent if primary else panel, "#8FC8FF" if primary else "#2A3038")
        buttons[key] = b
        return b

    button("videos", "Choose videos…", pick_files, primary=True).focus_set()
    button("folder", "Choose a folder…", pick_folder)
    if last_session:
        n = len(last_session)
        button("resume", f"Reopen last session ({n} video{'s' if n != 1 else ''})", lambda: finish(last_session))
    footer = tk.Frame(root, bg=bg)
    footer.pack(fill="x", pady=(u // 2, 0))
    status = tk.Label(footer, text="In the wall: H shows the controls, Esc exits.", bg=bg, fg=dim, font=font(0.9))
    status.pack(side="left")
    open_settings: list = []

    def show_settings():
        if not open_settings and config_path:
            open_settings.append(settings_window(root, config_path, lambda names: C.load(config_path),
                                                 lambda: open_settings.clear()))

    settings_link = tk.Label(footer, text="Settings", bg=bg, fg=accent, font=font(0.9, True), cursor="hand2")
    settings_link.pack(side="right")
    settings_link.bind("<Button-1>", lambda e: show_settings())
    buttons["settings"] = settings_link

    if can_drop:
        try:
            from tkinterdnd2 import DND_FILES
            root.drop_target_register(DND_FILES)
            def drag_enter(e):
                draw_zone("drag")
                log.info("drop zone highlighted")
                return e.action

            def drag_leave(e):
                draw_zone("idle")
                return e.action

            def dropped(e):
                draw_zone("idle")
                finish(expand(list(root.tk.splitlist(e.data))))
                return e.action

            root.dnd_bind("<<DropEnter>>", drag_enter)
            root.dnd_bind("<<DropLeave>>", drag_leave)
            root.dnd_bind("<<Drop>>", dropped)
        except Exception as e:
            log.info("start window: drag-and-drop unavailable: %s", e)
    root.bind("<Escape>", lambda e: root.destroy())

    update_link: list = []

    def show_update(found):
        link = tk.Label(root, text=f"Tessera Wall {found['version']} is available – click here to download it",
                        bg=bg, fg=accent, font=font(0.95, True), cursor="hand2")
        link.pack(anchor="w", pady=(u // 3, 0))
        link.bind("<Button-1>", lambda e: updates.open_release_page(found["url"]))
        root.geometry(f"{w}x{h + u * 2}")                    # make room without moving the window
        root.update_idletasks()
        update_link.append(link)
        log.info("start window update at %d,%d %dx%d: %s", link.winfo_rootx(), link.winfo_rooty(),
                 link.winfo_width(), link.winfo_height(), found["version"])

    def check_handed_over():  # files sent by a later launch (right-click, Send To) open the wall
        items = []
        while not incoming.empty():
            items.append(incoming.get_nowait())
        if items:
            finish(items)
            return
        if checker and checker.result and not update_link:
            show_update(checker.result)
        root.after(150, check_handed_over)
    root.after(150, check_handed_over)
    root.attributes("-alpha", 0.0)
    root.deiconify()
    log.info("start window dark title bar: %s", dark_title_bar(root))
    root.update()
    for key, b in {**buttons, "drop": drop}.items():      # positions, so tests can click the real buttons
        log.info("start window %s at %d,%d %dx%d", key, b.winfo_rootx(), b.winfo_rooty(),
                 b.winfo_width(), b.winfo_height())
    root.attributes("-topmost", True)                     # come to the front even if launched in the background
    root.lift()
    root.focus_force()
    fade(root, 1.0, 120)
    root.after(400, lambda: root.attributes("-topmost", False))
    root.mainloop()
    return chosen or None


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
