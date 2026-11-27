"""The wall itself: the tiles, layout and animation, hover and focus, input, audio, overlays."""
import ctypes
import json
import logging
import math
import os
import queue
import random
import re
import subprocess
import threading
import time
import tkinter as tk
from ctypes import wintypes
from dataclasses import dataclass, field
from tkinter import filedialog
from tkinter import messagebox

from ipc import MpvIpc
from layout import Rect
from layout import arrange
from layout import grid
from layout import grid_rows
from layout import layout
from layout import clamp_pan, drawn_size, magnify_at, panscan_for
import config as C
import updates
from files import expand, read_state, save_wall, write_state
import probe
import resources
import sources
import templates
import ytdlp
from ui import (ACCENT, BG, CTL, DIM, FG, Button, ask_text, fade, hover, icon_font, key_chip, metrics_for, motion,
                paste_async, popup_list, round_corners, settings_window)
from winapi import GA_ROOT, end_with_app, pictures_folder, user32

log = logging.getLogger("tesserawall")


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
        ("Ctrl+wheel", "Magnify the video at the pointer (Z: normal size)"),
        ("Drag a magnified video", "Look around it"),
        ("Right-click", "Menu for the video: pause, pin, sound, next, snapshot, close…"),
        ("Middle-click", "Close the video"),
        ("Top edge of screen", "Menu: add videos, controls, settings, close"),
    ]),
    ("Layout", [
        (["L"], "Arrangement: grow, best fit, even grid"),
        (["C"], "Black bars: crop a little, fill, never crop"),
        (["Tab", "Shift+Tab"], "Focus the next / previous video"),
        (["1", "…", "9"], "Focus video 1-9 (again to release)"),
        (["+", "-"], "One more video from the same folder / one fewer"),
        (["Z"], "Focused video back to normal size (after Ctrl+wheel)"),
        (["I"], "Clean mode: hide outline, progress line, menus and messages"),
        (["0"], "Unpin, unfocus, release K and mute everything"),
    ]),
    ("Playback", [
        (["Space"], "Pause the focused video"),
        (["Shift+Space"], "Pause or resume all"),
        (["Left", "Right"], "Seek; with Shift, seek every video"),
        (["Up", "Down"], "Volume of the video you hear"),
        (["K"], "Keep the sound on this video while you point at others (again to release)"),
        (["R"], "Restart all from the beginning"),
        (["Page Down", "Page Up"], "Next / previous file from its folder"),
        (["N"], "Swap for a random file from its folder"),
        (["E"], "When a video ends: play again, next, random, stop (its own for channel videos)"),
        (["Ctrl+E"], "Move on to another file every 30 s / 1 min / 2 min / never"),
        (["G"], "Sync: every video follows the focused one (again to stop)"),
        (["Ctrl+G"], "Sync, keeping each video's current time difference"),
        (["[", "]"], "Slower / faster (Backspace: normal speed)"),
        ([",", "."], "Step one frame back / forward"),
        (["B"], "Loop a section: set start, set end, clear"),
        (["T"], "Next subtitle track"),
        (["A"], "Next audio track"),
        (["P"], "Save a snapshot to Pictures"),
        (["X", "Delete"], "Close the focused video"),
    ]),
    ("App", [
        (["H", "?", "F1"], "Show or hide this list"),
        (["S"], "Settings"),
        (["Ctrl+S"], "Save this wall, to open it again from the start window"),
        (["Ctrl+V"], "Open the links (YouTube, Twitch...) or files on the clipboard"),
        (["Ctrl+B"], "Black out: hide, pause and mute everything (again or Esc to return)"),
        (["F5"], "Reload the settings file"),
        (["U"], "Open the download page, when a new version is out"),
        (["Esc"], "Close this list, release pin or zoom, else quit"),
        (["Q"], "Quit and remember this session"),
    ]),
]
HELP_SECTIONS.append(("Menu bar", [
    (["F10"], "Open the menu bar from the keyboard (arrows move, Esc closes)"),
    (["Ctrl+O"], "Add videos"),
    (["Ctrl+Shift+O"], "Add a folder"),
    (["Ctrl+F"], "Search YouTube"),
]))
HELP_COLUMNS = (("Mouse", "Layout", "Menu bar"), ("Playback", "App"))

LAYOUT_MODES = ("grow", "flat", "grid")
CROP_MODES = ("limited", "fill", "fit")
MODE_WORDS = {"Arrangement": {"grow": "grow on hover", "flat": "best fit", "grid": "even grid"},   # as in Settings
              "Black bars": {"limited": "crop a little", "fill": "crop to fill", "fit": "never crop"}}
END_MODES = ("loop", "next", "random", "stop")
END_LABELS = {"loop": "play it again", "next": "next file in its folder", "random": "random file from its folder",
              "stop": "stop at the end"}
MOVES_ON = ("next", "random")   # end modes that load another video; "loop" and "stop" keep the same one
HINT_TEXT = "Point at a video to hear it     H: controls     Top edge: menu     Esc: exit"
HINT_WALLS = 5           # the launch hint shows on this many walls, or until H is pressed
CONNECT_TIMEOUT = 15.0   # seconds for mpv to create its IPC pipe
LOST_PLAYER_S = 5.0      # a player still running this long after its connection ended is restarted
ASPECT_TIMEOUT = 10.0    # seconds to wait for a video size before keeping the 16:9 guess
MAG_STEP = 0.25          # Ctrl+wheel: magnification per notch, as mpv's video-zoom (log2): x1.19
MAG_MAX = 3.0            # at most 8x


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
    dim: int | None = None       # brightness last sent to mpv
    placed: tuple | None = None  # last frame.place geometry (after the gutter inset)
    skip: str | None = None      # vd-lavc-skipframe last sent to mpv (economy mode)
    osd: int | None = None       # osd-level last sent to mpv (0 = no messages: clean mode / show_messages off)
    volume: float = 100.0        # this video's own volume (Up/Down); survives focus changes
    gain: float = 0.0            # crossfade position: 0 = silent and muted, 1 = full volume
    idle_since: float | None = None   # when mpv was first seen idle (file failed to open)
    swapped: bool = False        # file was replaced with `n`; wait for mpv to report the new one
    swap_seq: int = 0            # ipc.seq when the replacement was requested; only newer news counts
    restarts: list = field(default_factory=list)   # times this tile's player was restarted after a crash
    size: tuple | None = None    # video width, height once known (for the memory estimate)
    switch_due: float | None = None   # when "move on" replaces this video (None: not timed)
    mag: float = 0.0             # magnification inside the video (mpv video-zoom, log2; 0 = normal)
    opened_at: float = 0.0       # position it was opened at (session / crash restart), for link_stalled
    pool: str | None = None      # the channel / playlist (sources.py) it was picked from: N etc. pick from it
    ready: dict | None = None    # a source's next video, looked up ahead: {url, how, r (ytdlp.resolve), at}
    preparing: bool = False
    prepare_id: int = 0          # bumped by each swap and each new lookup: an older lookup is out of date
    lost_at: float | None = None  # when its IPC connection ended while mpv kept running
    fade: object = None          # None, "hold" (black while the next video loads) or the fade-in start time
    pan: tuple = (0.0, 0.0)      # mpv video-pan-x / -y while magnified
    note: object = None          # tk.Frame over the tile: "Loading…" (a link on its way) or why it failed
    failed: bool = False         # could not play: its player is gone and the note says why (see fail_in_place)
    gone_at: float | None = None  # a failed tile gives up its place then (None while a retry is on its way)


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
        self.audio_lock: Tile | None = None   # K: this tile keeps the sound while focus moves elsewhere
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
        self.sync = False             # G: every video follows the focused one
        self.sync_checked = 0.0
        self.sync_offsets: dict | None = None   # Ctrl+G: each tile's position when sync began (kept apart)
        self.blanked: dict | None = None        # Ctrl+B: each tile's pause state before the blackout
        self.refused: list[str] = []  # reasons videos were not opened (memory guard), not yet shown
        # Template (set by main): rows used while the wall holds exactly that many videos, and the ratio
        # group of each file it opened, so N swaps within the same shape.
        self.template_rows: list[int] | None = None
        self.shape_of: dict[str, float] = {}
        self.ring = tk.Frame(root, bg="#6CB6FF", highlightthickness=0, bd=0)   # focus outline
        self.top_since: float | None = None   # when the pointer reached the top edge
        self.dialog_open = False
        self.failed: list[str] = []           # files mpv could not open, not yet reported
        self.bar_keys = False             # the menu bar was opened with F10: it has the keys until Esc or a pick
        self.bar_building = False
        self.failed_why: dict[str, str] = {}  # sources (in failed) that could not be listed: why
        self.failed_shown: set[str] = set()   # failures their own tile showed (fail_in_place): not announced
        self.retry_tile: dict[str, Tile] = {}  # a link being tried again -> the tile keeping its place
        # Links that fail to open are retried (see open_failed): which yt-dlp each was tried with, the one
        # to use next, links that already had their update check, and retries still being prepared.
        self.link_tried: dict[str, list[str]] = {}
        self.link_next: dict[str, str] = {}
        self.link_checked: set[str] = set()
        self.link_retries: queue.Queue = queue.Queue()
        self.retrying = 0
        # Link sources (sources.py): each source's videos, which source a video came from, video titles,
        # lists still being fetched, and videos picked but not yet opened (they start two at a time).
        # Videos already shown from a source are picked again only once all the others have been.
        self.pools: dict[str, list] = {}
        self.seen: set[str] = set()
        self.pool_of: dict[str, str] = {}
        self.title_of: dict[str, str] = {}
        self.pool_results: queue.Queue = queue.Queue()
        self.listing: dict[str, int] = {}          # source -> how many videos it will open once listed
        self.pending_links = 0
        self.status: tk.Label | None = None
        self.prepared: queue.Queue = queue.Queue()        # (tile, url, how, resolve result) from workers
        self.prepare_slots = threading.Semaphore(2)       # lookups at once, so YouTube gets no burst
        self.clean = bool(C.CLEAN_MODE)      # I: just the videos (see set_clean)
        self.last_shown: list = []           # tiles' (dim, osd level) last logged by relayout
        self.panning: Tile | None = None      # magnified video being dragged, with where the drag began
        self.pan_from: tuple | None = None
        self.pan_moved = False
        self.updates: updates.Checker | None = None   # set by main()
        self.update_shown = False
        self.layout_mode = C.LAYOUT_MODE if C.LAYOUT_MODE in LAYOUT_MODES else "grow"
        self.crop_mode = C.CROP_MODE if C.CROP_MODE in CROP_MODES else "limited"
        self.end_mode = C.WHEN_FINISHED if C.WHEN_FINISHED in END_MODES else "loop"
        self.source_end = C.SOURCE_WHEN_FINISHED if C.SOURCE_WHEN_FINISHED in END_MODES else "random"
        self.switch_seconds = max(0, C.SWITCH_SECONDS)
        log.info("start W=%d H=%d", self.W, self.H)

    @property
    def max_crop(self) -> float:
        return {"limited": C.MAX_CROP, "fill": 1.0, "fit": 0.0}[self.crop_mode]

    def cycle_mode(self, attr: str, modes: tuple, label: str):
        new = modes[(modes.index(getattr(self, attr)) + 1) % len(modes)]
        setattr(self, attr, new)
        log.info("%s mode -> %s", {"Arrangement": "layout", "Black bars": "crop"}.get(label, label.lower()), new)
        self.announce(f"{label}: {MODE_WORDS.get(label, {}).get(new, new)}", 1500)
        self.pos_at_focus_change = self.cursor()   # the grid may move under a still pointer
        self.relayout(rearrange=True)

    def message_tile(self, avoid: str | None = None):
        """Where a wall-wide message goes: the focused video, else the one at the middle of the screen
        (not the top-left one, which the menu bar covers), else any that is playing."""
        if self.focus and self.focus.ipc and self.focus.path != avoid:
            return self.focus
        live = [t for t in self.tiles if t.ipc and t.rect and t.path != avoid]
        if not live:
            return None
        cx, cy = self.W / 2, self.H / 2
        return min(live, key=lambda t: (t.rect.x + t.rect.w / 2 - cx) ** 2 + (t.rect.y + t.rect.h / 2 - cy) ** 2)

    def announce(self, text: str, ms: int = 2000, problem: bool = False):
        """One message for the whole wall, on message_tile(). A problem is shown at OSD level 0, so it
        appears even with Messages off or in clean mode: it is not decoration."""
        t = self.message_tile()
        if t:
            if problem:
                t.ipc.post("show-text", text, ms, 0)
            else:
                t.ipc.post("show-text", text, ms)
        return t

    # ---- tiles -------------------------------------------------------------
    def add(self, item):
        path, start, pool = (tuple(item) + (0, None))[:3] if isinstance(item, (tuple, list)) else (item, 0, None)
        source = sources.source_of(path) if "://" in path else None
        if source:                                   # a channel or playlist: open some of its videos
            self.open_source(source, C.SOURCE_COUNT)
            return
        if pool and sources.source_of(pool) != pool:  # sessions and saved walls are files anyone could hand over
            log.info("ignored a source that is not a channel, playlist or search: %r", pool)
            pool = None
        if pool:                                     # reopened from a session: keep picking from its source
            self.pool_of[path] = pool
            if pool not in self.pools and pool not in self.listing:
                self.open_source(pool, 0)
        if len(self.tiles) >= C.MAX_VIDEOS:
            log.info("ignored (MAX_VIDEOS reached): %s", path)
            self.refused.append(f"at most {C.MAX_VIDEOS} videos")
            return
        costs = [resources.video_cost_mb(*t.size) if t.size else resources.video_cost_mb() for t in self.tiles]
        ok, why = resources.room_for_one_more(costs, resources.gpu_memory_mb(), resources.free_ram_mb())
        if not ok:
            log.info("not opened (%s): %s", why, path)
            self.refused.append(why)
            return
        frame = tk.Frame(self.root, bg="black", highlightthickness=0, bd=0)
        frame.place(x=0, y=0, width=1, height=1)
        started = self.spawn(frame, path, start)
        if not started:
            frame.destroy()
            return
        proc, pipe = started
        self.tiles.append(Tile(path, frame, proc, pipe, time.monotonic(), opened_at=start or 0.0,
                               pool=self.pool_of.get(path), aspect=9 / 16 if sources.is_short(path) else 16 / 9))
        if "://" in path:                            # a link takes seconds to look up: say so in its place
            self.show_loading(self.tiles[-1])
        self.clear_status()
        self.schedule_switch(self.tiles[-1])
        self.need_layout = True
        log.info("tile added #%d pipe=%s path=%s", self.counter, pipe, path)

    def spawn(self, frame, path: str, start: float = 0):
        """Start an mpv player drawing into `frame`. Returns (process, pipe name), or None on failure."""
        self.counter += 1
        pipe = rf"\\.\pipe\tesserawall-{os.getpid()}-{self.counter}"
        args = [self.mpv, f"--wid={frame.winfo_id()}", f"--input-ipc-server={pipe}",
                "--mute=yes", "--osc=no", "--input-default-bindings=no", "--input-vo-keyboard=no",
                "--input-cursor=no", "--cursor-autohide=no", f"--loop-file={self.loop_file(self.pool_of.get(path))}", "--keep-open=yes",
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
        elif C.RANDOM_START and "://" not in path:
            pct = random.randint(5, 80)
            args.append(f"--start={pct}%")
            log.info("random start %d%%: %s", pct, path)
        if C.SAFE_RENDERING and os.environ.get("TESSERAWALL_UNSAFE_RENDERING") != "1":
            # Many HDR flip-model swapchains being resized at once makes the AMD driver time out.
            args += ["--d3d11-flip=no", "--target-colorspace-hint=no",
                     "--d3d11-output-csp=srgb", "--d3d11-output-format=rgba8"]
        log_dir = os.environ.get("TESSERAWALL_MPV_LOG_DIR") or C.MPV_LOG_DIR
        if log_dir:  # one verbose mpv log per player, for diagnosing renderer/driver problems
            os.makedirs(log_dir, exist_ok=True)
            args.append("--log-file=" + os.path.join(log_dir, f"mpv-{os.getpid()}-{self.counter}.log"))
        if "://" in path:                         # links: which yt-dlp (remembered for retries), the quality
            exe = self.link_next.pop(path, None) or ytdlp.find()
            if exe:
                self.link_tried.setdefault(path, []).append(exe)
            # Every video on its way counts: picked from a source, still queued (a session reopening), or
            # to come from a source still being listed (a search and a channel opened together).
            coming = len(self.tiles) + 1 + self.pending_links + self.incoming.qsize() + sum(self.listing.values())
            height = sources.height_for(coming, self.H, C.STREAM_MAX_HEIGHT)
            log.info("link uses yt-dlp %s, up to %dp", exe, height)
            args += ytdlp.mpv_args(path, exe, height)
            ytdlp.warm_in_background(path)        # Windows fetches any missing root certificate meanwhile
        args += list(C.EXTRA_MPV_ARGS) + ["--", path]
        try:
            proc = subprocess.Popen(args, creationflags=subprocess.CREATE_NO_WINDOW, env=ytdlp.mpv_env(path))
        except OSError as e:
            log.error("could not start mpv for %s: %s", path, e)
            return None
        end_with_app(proc.pid)                   # ends with the app, even if the app is killed (winapi.py)
        return proc, pipe

    def restart(self, t: Tile, code) -> bool:
        """A player that was showing video exited with an error: start a new one in the same tile at
        the same moment. At most 3 times a minute per tile, so a file that keeps crashing is dropped."""
        now = time.monotonic()
        t.restarts = [x for x in t.restarts if now - x < 60]
        if len(t.restarts) >= 3:
            log.info("not restarting again (3 crashes in a minute): %s", t.path)
            return False
        pos = (t.ipc.value("time-pos") if t.ipc else None) or 0
        if t.ipc:
            t.ipc.close()
        started = self.spawn(t.frame, t.path, pos)
        if not started:
            return False
        t.proc, t.pipe = started
        t.ipc, t.born, t.restarts, t.opened_at = None, now, t.restarts + [now], pos
        t.swap_seq, t.lost_at = 0, None                # the new connection counts its messages from 0
        t.panscan = t.dim = t.skip = t.osd = None      # resend to the new player
        t.gain = 0.0                                   # starts muted; the crossfade brings sound back
        self.need_layout = True
        log.info("tile restarted (mpv exited with %s) at %.1fs pipe=%s: %s", code, pos, t.pipe, t.path)
        return True

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
        if self.audio_lock is tile:
            self.audio_lock = None
            log.info("audio lock off (its video closed)")
        if self.retry_tile.get(tile.path) is tile:
            del self.retry_tile[tile.path]
        if self.bar_tile is tile:
            self.bar_tile = None
        self.need_layout = True

    # ---- a tile's own note: loading, or why it could not play ------------------------------------------
    FAILED_SHOW_S = 5.0          # a failed tile keeps its place this long, saying why, before the wall closes up

    def tile_name(self, t: Tile) -> str:
        name = self.title_of.get(t.path) if "://" in t.path else os.path.basename(t.path)
        if not name and "://" in t.path:
            from urllib.parse import urlparse
            name = urlparse(t.path).netloc.removeprefix("www.") or t.path
        name = name or t.path
        return name if len(name) <= 70 else name[:67] + "…"

    def set_note(self, t: Tile, lines: list[tuple[str, str]]):
        """Text in the middle of t's frame, for while it has no picture: [(text, colour), ...]. The player's
        own window covers it once it shows video; a failed tile's player is gone, so it shows there."""
        self.clear_note(t)
        size = max(12, self.H // 72)
        width = max(120, int((t.frame.winfo_width() if t.frame.winfo_width() > 1 else
                              (t.target.w if t.target else 400)) * 0.85))
        box = tk.Frame(t.frame, bg="black")
        for text, colour in lines:
            tk.Label(box, text=text, bg="black", fg=colour, font=("Segoe UI", -size), wraplength=width,
                     justify="center").pack()
        box.place(relx=0.5, rely=0.5, anchor="center")
        t.note = box

    def clear_note(self, t: Tile):
        if t.note is not None:
            try:
                t.note.destroy()
            except tk.TclError:
                pass
            t.note = None

    def show_loading(self, t: Tile):
        """A link on its way: its name (or site) and "Loading…", until the picture arrives. Information, so
        not in clean mode or with messages off."""
        if self.clean or not C.SHOW_MESSAGES:
            self.clear_note(t)
            return
        self.set_note(t, [(self.tile_name(t), FG), ("Loading…", DIM)])
        log.info("loading note: %s", t.path)

    def fail_in_place(self, t: Tile, why: str):
        """t could not play. Its place is kept and says so, instead of the wall closing up at once around a
        tile that just vanished: the player is stopped, the note names the video and the likely cause, and
        FAILED_SHOW_S later the tile goes. A link is first tried again in the same place (open_failed)."""
        log.info("tile failed (%s): %s", why, t.path)
        if t.ipc:
            t.ipc.close()
        t.ipc = None
        if t.proc.poll() is None:
            t.proc.kill()
        t.failed, t.gain = True, 0.0
        if self.open_failed(t.path, tile=t):
            t.gone_at = None
            self.set_note(t, [(self.tile_name(t), FG), ("Didn't open. Trying again…", DIM)])
        else:
            self.say_failed(t)

    def say_failed(self, t: Tile):
        link = "://" in t.path
        self.set_note(t, [(f"Couldn't play {self.tile_name(t)}", FG),
                          ("It may be offline, private or not a video." if link else
                           "The file may be damaged or not a video.", DIM)])
        t.gone_at = time.monotonic() + self.FAILED_SHOW_S
        self.failed_shown.add(t.path)
        log.info("could not play: %s (shown in its place)", t.path)

    def respawn(self, t: Tile) -> bool:
        """A failed link's retry, in the place it already has (see take_link_retries)."""
        started = self.spawn(t.frame, t.path, t.opened_at)
        if not started:
            return False
        t.proc, t.pipe = started
        t.ipc, t.born, t.failed, t.gone_at = None, time.monotonic(), False, None
        t.aspect_known, t.idle_since, t.swapped, t.swap_seq = False, None, False, 0
        t.panscan = t.dim = t.skip = t.osd = None
        self.show_loading(t)
        self.need_layout = True
        log.info("tile retried in place pipe=%s: %s", t.pipe, t.path)
        return True

    # ---- layout ------------------------------------------------------------
    def relayout(self, rearrange: bool = False):
        """rearrange=True re-picks grouping/order (tiles changed); False keeps it (focus changed)."""
        self.need_layout = False
        if not self.tiles:
            return
        if self.template_rows and sum(self.template_rows) == len(self.tiles):
            if self.rows != self.template_rows or self.columns:
                log.info("template rows %s", self.template_rows)
            self.rows, self.columns = list(self.template_rows), False   # tiles keep the template's order
        elif rearrange or sum(self.rows) != len(self.tiles):
            order, self.columns, self.rows = arrange([t.aspect for t in self.tiles], self.W, self.H,
                                                     self.max_crop, C.BALANCE_WEIGHT,
                                                     allow_reorder=not self.manual_order)
            self.tiles = [self.tiles[i] for i in order]
        aspects = [t.aspect for t in self.tiles]
        f = self.tiles.index(self.focus) if self.focus in self.tiles else None
        zoomed = self.zoom and f is not None
        # Zoom = same reflow with "grow as far as allowed" and a much smaller floor for the others.
        scale, shrink = (100.0, C.ZOOM_MIN_SHRINK) if zoomed else (C.FOCUS_SCALE, C.MIN_SHRINK)
        templated = bool(self.template_rows) and self.rows == self.template_rows and not self.columns
        if self.layout_mode == "grid" and not zoomed and templated:
            rects = grid_rows(self.rows, self.W, self.H)      # equal cells within the template's rows
        elif self.layout_mode == "grid" and not zoomed:
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
            dim = -C.DIM_UNFOCUSED if (self.focus and t is not self.focus and not self.clean) else 0
            osd = 0 if (self.clean or not C.SHOW_MESSAGES) else 1
            if t.ipc and osd != t.osd:              # 0 hides show-text and mpv's own messages alike
                t.ipc.set_async("osd-level", osd)
                t.osd = osd
            if t.ipc and ps != t.panscan:
                t.ipc.set_async("panscan", ps)
                t.panscan = ps
            if t.ipc and dim != t.dim and t.fade is None:
                t.ipc.set_async("brightness", dim)
                t.dim = dim
            # Economy mode: unfocused videos skip B-frames in the decoder; the focused one decodes fully.
            mode = os.environ.get("TESSERAWALL_UNFOCUSED_SKIP", C.UNFOCUSED_SKIP)  # env override is for tests
            economy = mode and t is not self.focus and (self.focus or C.ECONOMY_WHEN_IDLE)
            skip = mode if economy else "default"
            if t.ipc and skip != t.skip:
                t.ipc.set_async("vd-lavc-skipframe", skip)
                t.skip = skip
        shown = [(t.dim, t.osd) for t in self.tiles]
        if shown != self.last_shown:                     # dimming and message level, logged when they change
            self.last_shown = shown
            log.info("on screen: dim=%s osd=%s", [d for d, _ in shown], [o for _, o in shown])
        self.anim_start = time.monotonic()
        self.bar_next = 0.0  # redraw the seek bar at the new size
        log.info("relayout %s=%s focus=%s%s rects=%s panscan=%s", "cols" if self.columns else "rows", self.rows, f,
                 " zoom" if zoomed else "" if self.layout_mode == "grow" else f" {self.layout_mode}", [(r.x, r.y, r.w, r.h) for r in rects], pans)

    def animate(self) -> bool:
        """Move frames towards their targets. Returns True while still animating."""
        if self.anim_start is None or self.blanked is not None:   # blacked out: frames stay hidden
            return False
        p = 1.0 if C.ANIM_MS <= 0 or not motion() \
            else min(1.0, (time.monotonic() - self.anim_start) * 1000 / C.ANIM_MS)
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
        if f in self.tiles and f.rect and C.FOCUS_OUTLINE and C.TILE_GAP >= 2 and not self.clean:
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
        t = self.press_tile
        self.panning = t if t and t.mag > 0 else None      # dragging a magnified video looks around it
        self.pan_from, self.pan_moved = ((self.press_pos, t.pan) if self.panning else None), False
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
        elif self.panning:
            self.drag_pan(e)
        elif (self.press_tile and not self.dragging and len(self.tiles) > 1
              and abs(e.x_root - self.press_pos[0]) + abs(e.y_root - self.press_pos[1]) > C.DRAG_THRESHOLD):
            self.dragging = True
            self.root.config(cursor="fleur")

    def on_release(self, e):
        tile, self.press_tile, self.pressing = self.press_tile, None, False
        panned, self.panning = self.pan_moved, None
        if panned:                                   # a look-around drag is not a click
            self.pan_moved = False
            self.root.config(cursor="")
            return
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

    # ---- magnify: zoom and pan inside one video ------------------------------------------------------
    def cell(self, t: Tile) -> tuple | None:
        """(x, y, w, h) of the tile's player window, relative to the wall."""
        if t.placed:
            return t.placed
        return (t.rect.x, t.rect.y, t.rect.w, t.rect.h) if t.rect else None

    def send_magnify(self, t: Tile):
        if t.ipc:
            t.ipc.set_async("video-zoom", round(t.mag, 3))
            t.ipc.set_async("video-pan-x", round(t.pan[0], 4))
            t.ipc.set_async("video-pan-y", round(t.pan[1], 4))

    def magnify(self, e, direction: int):
        """Ctrl+wheel: magnify the video under the pointer, keeping the spot under the pointer in place
        (zooming out stops at normal size). Pan is clamped so no edge of the picture comes into view."""
        t = self.tile_under_cursor((e.x_root, e.y_root))
        g = self.cell(t) if t else None
        if not g or not t.ipc:
            return
        new = max(0.0, min(MAG_MAX, t.mag + direction * MAG_STEP))
        if new == t.mag:
            return
        ux = e.x_root - self.root.winfo_rootx() - (g[0] + g[2] / 2)
        uy = e.y_root - self.root.winfo_rooty() - (g[1] + g[3] / 2)
        t.pan = (0.0, 0.0) if new == 0 else magnify_at(g[2], g[3], t.aspect, t.panscan or 0.0, t.mag, t.pan,
                                                        new, ux, uy)
        t.mag = new
        self.send_magnify(t)
        t.ipc.post("show-text", f"Magnify {2 ** new:.1f}x" if new else "Normal size", 1000)
        log.info("magnify %.2f (x%.2f) pan=(%.4f, %.4f): %s", new, 2 ** new, t.pan[0], t.pan[1], t.path)

    def drag_pan(self, e):
        """Dragging a magnified video moves the picture with the pointer."""
        t, (start, pan0) = self.panning, self.pan_from
        g = self.cell(t) if t in self.tiles else None
        dx, dy = e.x_root - start[0], e.y_root - start[1]
        if not g or (not self.pan_moved and abs(dx) + abs(dy) <= C.DRAG_THRESHOLD):
            return
        if not self.pan_moved:
            self.pan_moved = True
            self.root.config(cursor="fleur")
        dw, dh = drawn_size(g[2], g[3], t.aspect, t.panscan or 0.0, t.mag)
        if dw and dh:
            t.pan = clamp_pan(g[2], g[3], t.aspect, t.panscan or 0.0, t.mag,
                              (pan0[0] + dx / dw, pan0[1] + dy / dh))
            self.send_magnify(t)

    def unmagnify(self, t: Tile | None, announce: bool = True):
        """Z (and swapping in another file): the video back to normal size."""
        if not t or not t.mag:
            return
        t.mag, t.pan = 0.0, (0.0, 0.0)
        self.send_magnify(t)
        if announce and t.ipc:
            t.ipc.post("show-text", "Normal size", 1000)
        log.info("magnify off: %s", t.path)

    def swap_tiles(self, a: Tile, b: Tile):
        i, j = self.tiles.index(a), self.tiles.index(b)
        self.tiles[i], self.tiles[j] = b, a
        self.manual_order = True
        self.pos_at_focus_change = self.cursor()
        log.info("reorder %d <-> %d", i, j)
        self.relayout(rearrange=True)

    # ---- audio -------------------------------------------------------------
    def heard(self) -> Tile | None:
        """The video whose sound plays: the locked one (K), else the focused one."""
        return self.audio_lock or self.focus

    def toggle_audio_lock(self):
        """K: keep the sound on the focused video (or the locked one) while pointing at others; again to
        let it follow the pointer."""
        if self.audio_lock:
            t, self.audio_lock = self.audio_lock, None
            log.info("audio lock off")
            target = self.focus or t
            if target.ipc:
                target.ipc.post("show-text", "Sound follows the pointer", 1500)
            return
        t = self.focus
        if not t or not t.ipc:
            self.announce("Point at a video first, then K keeps its sound")
            return
        self.audio_lock = t
        log.info("audio lock -> %s", self.tiles.index(t))
        t.ipc.post("show-text", "Sound stays on this video (K to release)", 2000)

    def update_audio(self, dt: float) -> bool:
        """Crossfade: move every tile's gain towards 1 (the one heard) or 0. Returns True while fading."""
        step = 1.0 if C.CROSSFADE_MS <= 0 else dt * 1000 / C.CROSSFADE_MS
        fading = False
        heard = self.heard()
        for t in self.tiles:
            target = 1.0 if t is heard and self.blanked is None else 0.0
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
        t = self.heard()
        if t and t.ipc:
            t.volume = max(0, min(C.MAX_VOLUME, t.volume + delta))
            if t.gain == 1.0:
                t.ipc.set_async("volume", t.volume)
            t.ipc.post("show-text", f"Volume: {t.volume:.0f}%")

    def pick_file(self, t: Tile, how: str) -> str | None:
        """Another video from t's folder: "next" / "prev" in the folder's name order (wrapping), or
        "random". Files another tile shows are skipped. In a template wall only files of the tile's
        shape count (from the shape cache), so the rows keep fitting. None if there is no other.
        A video from a channel or playlist picks from that instead."""
        if t.pool:
            entries = self.pools.get(t.pool) or []
            taken = {x.path for x in self.tiles}
            e = (sources.pick(entries, 1, taken, C.SOURCE_ORDER, self.seen) or [None])[0] if how == "random" \
                else sources.step(entries, t.path, 1 if how == "next" else -1, taken)
            if e:
                self.pool_of[e["url"]] = t.pool
                self.seen.add(e["url"])
            return e["url"] if e else None
        if "://" in t.path:
            return None
        files = expand([os.path.dirname(t.path)])
        shape = self.shape_of.get(os.path.normcase(t.path))
        if shape:
            known = probe.cached(files, probe.cache_path_for(self.config_path))
            files = [p for p in files if os.path.normcase(p) == os.path.normcase(t.path) or (
                known.get(p) and abs(math.log(known[p] / shape)) <= math.log(1 + templates.TOLERANCE))]
        names = [os.path.normcase(f) for f in files]
        taken = {os.path.normcase(x.path) for x in self.tiles}
        if how == "random":
            pool = [f for f, n in zip(files, names) if n not in taken]
            return random.choice(pool) if pool else None
        direction = 1 if how == "next" else -1
        current = os.path.normcase(t.path)
        # A file opened explicitly may not be a listed video type: start just outside the list.
        i = names.index(current) if current in names else (-1 if direction > 0 else 0)
        for k in range(1, len(files) + 1):
            j = (i + direction * k) % len(files)
            if names[j] not in taken:
                return files[j]
        return None

    def no_other_message(self, t: Tile) -> str:
        if t.pool:
            return (f"Still listing {sources.label(t.pool)}" if t.pool in self.listing
                    else f"No other videos in {sources.label(t.pool)}")
        return ("No other videos of this shape in this folder" if os.path.normcase(t.path) in self.shape_of
                else "No other videos in this folder")

    def swap_random(self):
        """N: replace the focused video with a random other video from the same folder."""
        t = self.focus
        if not t or not t.ipc or ("://" in t.path and not t.pool):
            return
        ready = self.take_ready(t, "random")
        new = ready["url"] if ready else self.pick_file(t, "random")
        if new:
            self.load_into(t, new, ready=ready)
        else:
            t.ipc.post("show-text", self.no_other_message(t))

    def step_file(self, direction: int):
        """Page Down / Page Up: load the next / previous video from the focused tile's folder, in the
        folder's name order (same as opening the folder), wrapping at the ends."""
        t = self.focus
        if not t or not t.ipc or ("://" in t.path and not t.pool):
            return
        new = self.pick_file(t, "next" if direction > 0 else "prev")
        if new:
            self.load_into(t, new)
        else:
            t.ipc.post("show-text", self.no_other_message(t))

    def advance(self, t: Tile):
        """A video ended with "when finished" set to next or random: load that file and play it. With no
        other file to go to, play the same one again."""
        mode = self.mode_of(t)
        ready = self.take_ready(t, mode)
        new = ready["url"] if ready else self.pick_file(t, mode)
        log.info("finished (%s): %s", mode, t.path)
        if new:
            self.load_into(t, new, announce=t is self.focus, ready=ready)
        else:
            t.swap_seq = t.ipc.seq                 # this end has been handled
            t.ipc.post("seek", 0, "absolute")
        t.ipc.set_async("pause", False)            # keep-open paused it on its last frame

    SWITCH_STEPS = (0, 30, 60, 120)

    def schedule_switch(self, t: Tile, now: float | None = None):
        """Set when "move on" replaces t: about switch_seconds from now, +-25 % so the wall does not
        change all at once."""
        now = time.monotonic() if now is None else now
        t.switch_due = now + self.switch_seconds * random.uniform(0.75, 1.25) if self.switch_seconds else None

    def timed_switch(self, t: Tile, now: float):
        """t's time is up: load the next or a random file (as when_finished says; random if that is loop).
        Not the video being pointed at or listened to (K), and not in sync mode: those get checked again a
        little later."""
        if t is self.focus or t is self.audio_lock or self.sync or self.blanked is not None or not t.ipc:
            t.switch_due = now + 5
            return
        how = self.mode_of(t) if self.mode_of(t) in MOVES_ON else "random"
        ready = self.take_ready(t, how)
        new = ready["url"] if ready else self.pick_file(t, how)
        if new:
            log.info("moved on after %ds: %s", self.switch_seconds, t.path)
            self.load_into(t, new, announce=False, ready=ready)
        else:
            self.schedule_switch(t, now)

    def cycle_switch(self, to: int | None = None):
        """Ctrl+E (or the Settings window): how often videos move on. Restarts every video's timer."""
        if to is None:
            steps = self.SWITCH_STEPS
            to = steps[(steps.index(self.switch_seconds) + 1) % len(steps)] if self.switch_seconds in steps else 0
        self.switch_seconds = max(0, to)
        log.info("move on -> %s", self.switch_seconds or "never")
        now = time.monotonic()
        for t in self.tiles:
            self.schedule_switch(t, now)
        target = self.focus or (self.tiles[0] if self.tiles else None)
        if target and target.ipc:
            target.ipc.post("show-text", f"Move on: after {self.switch_seconds} s" if self.switch_seconds
                            else "Move on: never", 2000)

    def mode_of(self, t: Tile) -> str:
        """What t does when it ends: a channel or playlist video follows source_end, anything else end_mode."""
        return self.source_end if t.pool else self.end_mode

    def loop_file(self, pool: str | None = None) -> str:
        return "inf" if (self.source_end if pool else self.end_mode) == "loop" else "no"

    def cycle_end_mode(self, to: str | None = None, sources_too: bool = False):
        """E (or the Settings window): what videos do when they end. Applies to the open players at once.
        E while pointing at a channel or playlist video changes what those do instead."""
        if to is None and self.focus and self.focus.pool:
            return self.set_source_end(END_MODES[(END_MODES.index(self.source_end) + 1) % len(END_MODES)], True)
        self.end_mode = to or END_MODES[(END_MODES.index(self.end_mode) + 1) % len(END_MODES)]
        log.info("when finished -> %s", self.end_mode)
        for t in self.tiles:
            if t.ipc and not t.pool:
                t.ipc.set_async("loop-file", self.loop_file())
        if to is None:
            self.announce(f"When a video ends: {END_LABELS[self.end_mode]}")

    def set_source_end(self, mode: str, announce: bool = False):
        self.source_end = mode
        log.info("when a channel video ends -> %s", mode)
        label = {"loop": "play it again", "next": "the next in it", "random": "another from it",
                 "stop": "stop at the end"}[mode]
        for t in self.tiles:
            if t.ipc and t.pool:
                t.ipc.set_async("loop-file", self.loop_file(t.pool))
        if announce:
            self.announce(f"When a channel video ends: {label}")

    def load_into(self, t: Tile, new: str, announce: bool = True, ready: dict | None = None):
        log.info("file swapped: %s -> %s%s", t.path, new, " (prepared)" if ready and ready.get("r") else "")
        if self.sync_offsets:
            self.sync_offsets.pop(t, None)           # a different file has no meaningful offset
        old = self.shape_of.get(os.path.normcase(t.path))
        if old:
            self.shape_of[os.path.normcase(new)] = old
        t.swap_seq = t.ipc.seq
        # The player was launched with a global --start (session position, crash restart or random start),
        # which mpv applies to every file it loads, and A-B loop points also survive a loadfile. So every
        # swap states its own start and clears the loop. Index -1: append position, unused with "replace".
        if C.RANDOM_START and "://" not in new:
            pct = random.randint(5, 80)
            start = f"{pct}%"
            log.info("random start %d%%: %s", pct, new)
        else:
            start = "none"
        r = ready.get("r") if ready else None
        if r:      # looked up ahead: open the streams themselves, as mpv's yt-dlp hook would (see ytdlp.resolve)
            t.ipc.post("loadfile", r["urls"][0], "replace", "-1",
                       f"start={start},ab-loop-a=no,ab-loop-b=no," + ytdlp.loadfile_options(r))
        else:
            t.ipc.post("loadfile", new, "replace", "-1", f"start={start},ab-loop-a=no,ab-loop-b=no")
        t.ready, t.preparing = None, False
        t.prepare_id += 1                                # a lookup still queued for the old video is skipped
        if "://" in new:   # black until the new picture arrives, instead of the old video's last frame
            t.ipc.set_async("brightness", -100)
            t.dim, t.fade = -100, "hold"
        if t.mag:                                    # zoom and pan are player settings: they would stay too
            self.unmagnify(t, announce=False)
        self.schedule_switch(t)
        if "://" in new:
            ytdlp.warm_in_background(new)
        if announce:
            t.ipc.post("show-text", self.title_of.get(new) or os.path.basename(new), 2500)
        t.path, t.swapped, t.aspect_known, t.born = new, True, False, time.monotonic()
        t.opened_at = 0.0                            # a link starts at 0 (start= above), for link_stalled
        self.bar_next = 0.0

    def hint_due(self) -> bool:
        """Show the launch hint on this wall? On the first HINT_WALLS walls (counted in state.json), never
        once H has been pressed, never with the setting off. Counts this wall when it says yes."""
        if not C.SHOW_HELP_HINT:
            return False
        shown = read_state(self.config_path).get("hint_walls", 0)
        if not isinstance(shown, int) or shown >= HINT_WALLS:
            return False
        write_state(self.config_path, hint_walls=shown + 1)
        return True

    def toggle_help(self):
        """Show/hide the controls list. It is its own always-on-top window, so it is drawn above the
        videos whatever presentation mode mpv uses; while the pointer is on it, hover is suspended.
        Opening it retires the launch hint: it has done its job."""
        if not self.help and read_state(self.config_path).get("hint_walls", 0) != HINT_WALLS:
            write_state(self.config_path, hint_walls=HINT_WALLS)
        if self.help:
            win = self.help
            self.help = None
            fade(win, 0.0, 80, win.destroy)
            log.info("help hidden")
            return
        size = max(13, self.H // 64)                     # pixels; scales with the monitor
        bg, pad = BG, size * 2
        body, strong = ("Segoe UI", -size), ("Segoe UI Semibold", -size)
        win = tk.Toplevel(self.root)
        win.withdraw()                                   # build it hidden, then map it once, on top
        win.overrideredirect(True)
        win.configure(bg=bg, padx=pad, pady=int(pad * 0.8))
        tk.Label(win, text="Controls", bg=bg, fg="white", font=("Segoe UI Semibold", -int(size * 1.45))
                 ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, size // 2))
        sections = dict(HELP_SECTIONS)

        def keys_cell(parent, keys):
            cell = tk.Frame(parent, bg=bg)
            if isinstance(keys, str):                    # a mouse action, in words
                tk.Label(cell, text=keys, bg=bg, fg="white", font=strong).pack(side="left")
                return cell
            for i, alt in enumerate(keys):
                if i:
                    tk.Label(cell, text="/", bg=bg, fg=DIM, font=body).pack(side="left", padx=3)
                for j, k in enumerate([alt] if alt == "+" else alt.split("+")):   # "+" is a key itself
                    if j:
                        tk.Label(cell, text="+", bg=bg, fg=DIM, font=body).pack(side="left", padx=3)
                    key_chip(cell, k, ("Segoe UI Semibold", -int(size * 0.8)), bg).pack(side="left")
            return cell

        for col, titles in enumerate(HELP_COLUMNS):
            column = tk.Frame(win, bg=bg)
            column.grid(row=1, column=col * 2, sticky="n")
            if col:
                tk.Frame(win, bg=bg, width=pad * 2).grid(row=1, column=1)      # space between the columns
            r = 0
            for title in titles:
                tk.Label(column, text=title, bg=bg, fg=FG, font=strong
                         ).grid(row=r, column=0, columnspan=2, sticky="w", pady=(size if r else 0, size // 3))
                r += 1
                for keys, action in sections[title]:
                    keys_cell(column, keys).grid(row=r, column=0, sticky="w", padx=(0, size), pady=3)
                    tk.Label(column, text=action, bg=bg, fg=DIM, font=body
                             ).grid(row=r, column=1, sticky="w", pady=3)
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
        if self.clean or not C.TOP_MENU:
            if self.topbar is not None and not self.dialog_open:
                self.hide_topbar()
            self.top_since = None
            return
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

    def show_topbar(self, keyboard: bool = False):
        """keyboard (F10): the bar takes the focus on its first button; arrows or Tab move, Space or Enter
        pick, Esc closes. It stays until then, wherever the pointer is."""
        if self.bar_building:                            # bar.update() below runs pending events: a second
            return                                       # F10 (or the edge) must not build a second bar
        if self.topbar is not None:
            if keyboard and not self.bar_keys:
                self.hide_topbar()                       # shown by the pointer: show it again, focusable
            else:
                return
        self.bar_building = True
        try:
            self._build_topbar(keyboard)
        finally:
            self.bar_building = False

    def _build_topbar(self, keyboard: bool):
        if keyboard:                 # before anything else: building runs the wall's tick, which would
            self.bar_keys = self.dialog_open = True        # otherwise hide a bar the pointer is not near
            log.info("top bar opened from the keyboard")
        m = metrics_for(self.root)
        f = m.f
        bar = tk.Toplevel(self.root)
        bar.withdraw()
        bar.overrideredirect(True)
        bar.configure(bg=BG)
        tk.Frame(bar, bg="#232830", height=1).pack(side="bottom", fill="x")    # a hairline under the bar
        row = tk.Frame(bar, bg=BG, padx=f(8), pady=f(4))
        row.pack(fill="x")
        tk.Label(row, text="Tessera Wall", bg=BG, fg=DIM, font=m.strong).pack(side="left", padx=(f(8), f(12)))
        rects = {}

        def button(key, text, command, side="left", danger=False, shortcut=None):
            b = Button(row, text, command, m, kind="subtle", danger=danger, takefocus=1 if keyboard else 0,
                       shortcut=shortcut)
            b.pack(side=side, padx=(0, f(2)) if side == "left" else (f(2), 0))
            rects[key] = b

        # Each shows its key, so the menu teaches the keyboard (as bind_keys and the controls list do).
        button("add", "Add videos…", lambda: self.add_from_dialog(folder=False), shortcut="Ctrl+O")
        button("folder", "Add a folder…", lambda: self.add_from_dialog(folder=True), shortcut="Ctrl+Shift+O")
        button("search", "Search YouTube…", self.search_from_dialog, shortcut="Ctrl+F")
        if sources.recent(self.config_path, limit=1):
            button("recent", "Recent ▾", lambda: self.recent_menu(rects["recent"]))
        button("controls", "Controls", lambda: (self.hide_topbar(), self.toggle_help()), shortcut="H")
        button("save", "Save wall\u2026", lambda: (self.hide_topbar(), self.save_wall_as()), shortcut="Ctrl+S")
        button("settings", "Settings", lambda: (self.hide_topbar(), self.toggle_settings()), shortcut="S")
        button("close", "Close  ✕", self.quit, side="right", danger=True)
        bar.update_idletasks()
        bar.geometry(f"{self.W}x{bar.winfo_reqheight()}+{self.root.winfo_rootx()}+{self.root.winfo_rooty()}")
        bar.attributes("-alpha", 0.0)
        bar.deiconify()
        bar.attributes("-topmost", True)                 # after mapping, like the controls list
        bar.lift()
        self.topbar = bar
        bar.update()                                     # lay the buttons out now: their positions are logged
        fade(bar, 1.0, 120)
        if keyboard:                                     # the wall leaves the keys to the bar meanwhile
            order = list(rects.values())

            def step(e, by):
                i = order.index(e.widget) if e.widget in order else 0
                order[(i + by) % len(order)].focus_set()
                return "break"
            for b in order:
                b.bind("<Right>", lambda e: step(e, 1))
                b.bind("<Left>", lambda e: step(e, -1))
            bar.bind("<Escape>", lambda e: (self.hide_topbar(), "break")[1])
            bar.focus_force()
            order[0].focus_set()
        log.info("top bar shown: %s", " ".join(
            f"{k}={b.winfo_rootx()},{b.winfo_rooty()},{b.winfo_width()},{b.winfo_height()}" for k, b in rects.items()))

    def hide_topbar(self):
        if self.topbar:
            bar = self.topbar
            self.topbar, self.top_since = None, None      # gone as far as the wall is concerned
            fade(bar, 0.0, 80, bar.destroy)
            if self.bar_keys:                            # opened with F10: give the keys back, and the
                self.bar_keys = self.dialog_open = False   # window focus (the bar had it; without this Windows
                self.root.focus_force()                  # may hand it to another app)
            else:
                self.root.focus_set()                    # keys go back to the wall
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

    def search_from_dialog(self):
        """Top bar: open videos from a YouTube search, like a channel link (sources.py)."""
        self.dialog_open = True
        try:
            words = ask_text(self.topbar, "Search YouTube", "Search for:", "Search", topmost=True)
        finally:
            self.dialog_open = False
        if words and words.strip():
            log.info("search typed")
            self.incoming.put(sources.search_url(words))
        self.hide_topbar()

    def recent_menu(self, anchor: tk.Widget):
        """Top bar: the recent and pinned channels, playlists and searches; picking one adds videos from
        it, like pasting its link. The star in front of each pins or unpins it (the list stays open);
        pinned ones stay listed first, here and in the start window. A themed list (ui.popup_list) below
        the button; the bar stays while it is open."""
        m = metrics_for(self.root)
        items = sources.recent(self.config_path, limit=12)
        x, y = anchor.winfo_rootx(), anchor.winfo_rooty() + anchor.winfo_height() + 4
        log.info("recent menu: %d item(s) at %d,%d", len(items), x, y)
        star_font = icon_font(self.root, m.f(14))
        on, off = ("", "") if star_font else ("★", "☆")   # filled / outline star

        def pick(s):
            log.info("recent picked: %s", s)
            self.incoming.put(s)

        def pin_toggle(d):
            d["pinned"] = not d.get("pinned")
            sources.set_pinned(self.config_path, d["source"], d["pinned"])
            log.info("recent menu %s %s", "pinned" if d["pinned"] else "unpinned", d["source"])
            return (on, ACCENT) if d["pinned"] else (off, DIM)

        def closed():
            self.dialog_open = False
            self.hide_topbar()
        # The list opens on the release of the click on Recent, and the wall ignores mouse events while it
        # is open, so it never sees that release: clear the press now, or it would think the button is
        # still down (no menu bar, no hover) until the next click.
        self.pressing, self.press_tile, self.dragging = False, None, False
        self.dialog_open = True                          # keeps the bar alive while the list is open
        pop = popup_list(self.topbar, x, y, [(sources.menu_name(d["source"], d.get("title")),
                                              (on, ACCENT) if d.get("pinned") else (off, DIM),
                                              lambda s=d["source"]: pick(s), lambda d=d: pin_toggle(d))
                                             for d in items], m, on_close=closed, mark_font=star_font)
        if pop.rows:                                     # where the rows and their stars are, for tests
            star = m.f(12) + pop.rows[0].mark_w // 2
            log.info("recent list rows: star=%d,0,0,0 %s", star, " ".join(
                f"r{i}={b.winfo_rootx()},{b.winfo_rooty()},{b.winfo_width()},{b.winfo_height()}"
                for i, b in enumerate(pop.rows)))

    # ---- files that would not play --------------------------------------------
    def open_failed(self, path: str, tile: Tile | None = None) -> bool:
        """mpv could not open `path`. A file is reported. A link is tried again first, because video sites
        change and yt-dlp may need updating: once after an update check (ytdlp.recover, in a worker thread),
        then with any copy of yt-dlp it has not been tried with; only then is it reported. Returns True when
        a retry is on its way; it opens in `tile` (which keeps its place meanwhile), else in a new one."""
        if "://" not in path:
            self.failed.append(path)
            return False
        tried = list(self.link_tried.get(path, []))
        check = path not in self.link_checked
        if not check and not any(c not in tried for c in ytdlp.candidates()):
            self.failed.append(path)
            return False
        self.link_checked.add(path)
        self.retrying += 1
        if tile is not None:
            self.retry_tile[path] = tile                 # the tile says "Trying again" itself
        else:
            target = self.message_tile(avoid=path)
            if target and check:
                target.ipc.post("show-text", "A link didn't open. Trying again…", 4000, 0)

        def work():
            exe = None
            try:
                if check:
                    ytdlp.warm_tls(path)                 # a missing root certificate is the commonest cause
                    ytdlp.recover()                      # then a newer yt-dlp, if there is one
                    exe = ytdlp.find()                   # first retry: the best yt-dlp, whatever was tried
                exe = exe or next((c for c in ytdlp.candidates() if c not in tried), None)
            except Exception as e:                       # never leave the retry count raised
                log.warning("link retry: %s", e)
            self.link_retries.put((path, exe))
        threading.Thread(target=work, daemon=True, name="link retry").start()
        return True

    # ---- link sources: channels, Shorts tabs and playlists (sources.py) -------------------------------
    def waiting(self) -> bool:
        """Something will arrive without the user doing anything: a link retry, a channel being listed, or
        picked videos still to be opened. An empty wall stays open meanwhile."""
        return bool(self.retrying or self.listing or self.pending_links)

    def open_source(self, source: str, n: int):
        """List a source in a worker thread (cached for a day, an hour in "newest" order), then open n videos
        from it (0: just make it available, for videos reopened from a session)."""
        if source in self.pools:
            self.pool_results.put((source, self.pools[source], None, n))
            return
        self.listing[source] = n
        log.info("listing %s for %d videos", source, n)
        if n:
            self.say_status(f"Getting videos from {sources.label(source)}…")

        def work():
            try:
                fresh = sources.FRESH_AGE if C.SOURCE_ORDER == "newest" else sources.MAX_AGE
                self.pool_results.put((source, sources.load(source, max_age=fresh), None, n))
            except Exception as e:
                log.warning("could not list %s: %s", source, e)
                self.pool_results.put((source, None, str(e), n))
        threading.Thread(target=work, daemon=True, name="list source").start()

    def take_pool_results(self):
        """On the Tk thread: a source's list arrived. Pick n videos not already on the wall and open them
        two at a time, 0.8 s apart, so YouTube does not get a burst of requests."""
        while True:
            try:
                source, entries, error, n = self.pool_results.get_nowait()
            except queue.Empty:
                return
            self.listing.pop(source, None)
            if entries is None:
                if n:
                    self.failed.append(source)
                    self.failed_why[source] = error or ""
                self.clear_status()
                continue
            self.pools[source] = entries
            self.title_of.update({e["url"]: e["title"] for e in entries})
            if n:                                        # opened by the user, not reopened with a session
                sources.remember(self.config_path, source, sources.cached_title(source))
            picked = sources.pick(entries, n, {t.path for t in self.tiles}, C.SOURCE_ORDER, self.seen)
            self.seen.update(e["url"] for e in picked)
            log.info("%s: opening %d of %d videos", source, len(picked), len(entries))
            for i, e in enumerate(picked):
                self.pool_of[e["url"]] = source
                self.pending_links += 1
                self.root.after(800 * (i // 2), lambda url=e["url"]: self.open_picked(url))
            if not picked:
                self.clear_status()

    # ---- the next video of a channel, ready ahead; fading between videos -------------------------------
    FADE_S = 0.35

    def prepare_next(self, now: float):
        """For each playing channel or playlist video that will move on (when it ends, or Move on), pick its
        successor now and look its streams up in a worker thread (two at a time), so the switch skips the
        2-4 s lookup. A prepared pick older than 2 hours is dropped (YouTube's addresses expire)."""
        for t in self.tiles:
            if not t.pool or not t.ipc or not t.aspect_known or t.preparing or self.closing:
                continue
            if t.ready and now - t.ready["at"] > 7200:
                t.ready = None
            mode = self.mode_of(t)
            how = mode if mode in MOVES_ON else ("random" if self.switch_seconds else None)
            if t.ready or not how:
                continue
            url = self.pick_file(t, how)
            if not url:
                continue
            t.preparing = True
            t.prepare_id += 1
            coming = len(self.tiles) + self.pending_links + self.incoming.qsize() + sum(self.listing.values())
            height = sources.height_for(coming, self.H, C.STREAM_MAX_HEIGHT)

            def work(t=t, url=url, how=how, height=height, ticket=t.prepare_id):
                with self.prepare_slots:
                    if t.prepare_id != ticket or self.closing:   # swapped while waiting: nobody wants it
                        log.info("prepare skipped (out of date): %s", url)
                        return
                    try:
                        r = ytdlp.resolve(url, height)
                    except Exception as e:
                        log.info("could not prepare %s: %s", url, e)
                        r = None
                self.prepared.put((t, url, how, r, ticket))
            threading.Thread(target=work, daemon=True, name="prepare next").start()

    def take_prepared(self):
        while True:
            try:
                t, url, how, r, ticket = self.prepared.get_nowait()
            except queue.Empty:
                return
            if t in self.tiles and t.preparing and t.prepare_id == ticket:
                t.preparing = False
                t.ready = {"url": url, "how": how, "r": r, "at": time.monotonic()}
                log.info("next ready%s: %s", "" if r else " (page link only)", url)

    def take_ready(self, t: Tile, how: str) -> dict | None:
        """t's prepared next video, if it was picked the way now wanted and is not on the wall meanwhile."""
        ready, t.ready = t.ready, None
        if ready and ready["how"] == how and ready["url"] not in {x.path for x in self.tiles}:
            return ready
        return None

    def update_fades(self, now: float):
        """Fade a switched video in from black once its picture arrived (or after 10 s regardless)."""
        for t in self.tiles:
            if t.fade is None or not t.ipc:
                continue
            if t.fade == "hold":
                if now - t.born > 10:
                    t.fade = now
                continue
            target = -C.DIM_UNFOCUSED if (self.focus and t is not self.focus and not self.clean) else 0
            p = min(1.0, (now - t.fade) / self.FADE_S)
            value = round(-100 + (target + 100) * p)
            if p >= 1:
                t.fade = None
            if value != t.dim:
                t.ipc.set_async("brightness", value)
                t.dim = value

    def open_picked(self, url: str):
        self.pending_links -= 1
        if not self.closing:
            self.add(url)

    def say_status(self, text: str):
        """A line of text on the empty wall while the first videos are on their way."""
        if self.tiles or self.closing:
            return
        if self.status is None:
            self.status = tk.Label(self.root, bg="black", fg="#9AA4AE", font=("Segoe UI", max(12, self.H // 60)))
            self.status.place(relx=0.5, rely=0.5, anchor="center")
        self.status.config(text=text)

    def clear_status(self):
        if self.status is not None:
            self.status.destroy()
            self.status = None

    def link_stalled(self, t: Tile, now: float) -> bool:
        """A link whose stream could not be read: mpv puts it at its end at once (seen when HTTPS
        verification failed, and now and then when YouTube answers a stream request with nothing), leaving a
        black tile. Only within the first 30 s, and only when the position is nearer the end than playing
        from where it was opened could have got it. Live streams have no length. Checked from 6 s on, or as
        soon as mpv reports the end, so a channel video that fails at once is retried instead of being taken
        for one that finished (which would skip it, and with a lasting failure race through the channel)."""
        age = now - t.born
        ended = t.ipc.value("eof-reached") and t.ipc.value_seq("eof-reached") > t.swap_seq
        if (age < 6 and not ended) or age > 30:
            return False
        dur, pos = t.ipc.value("duration"), t.ipc.value("time-pos")
        if not dur or pos is None or dur < 60:
            return False
        return pos >= dur - 1 and pos - t.opened_at > age + 10

    def take_link_retries(self):
        """On the Tk thread: open again the links open_failed prepared, or report them."""
        while True:
            try:
                path, exe = self.link_retries.get_nowait()
            except queue.Empty:
                return
            self.retrying -= 1
            t = self.retry_tile.pop(path, None)
            t = t if t in self.tiles and t.failed else None
            if exe:
                log.info("link: trying again with %s: %s", exe, path)
                self.link_next[path] = exe
                if t is None or not self.respawn(t):
                    if t is not None:
                        self.remove(t, "retry could not start")
                    self.add(path)
            else:
                self.failed.append(path)
                if t is not None:
                    self.say_failed(t)

    def report_failed(self):
        """Tell the user, in plain words, about files mpv could not open."""
        if not self.failed or (self.waiting() and not self.tiles):
            return
        if self.tiles:
            # Failures their own tile showed (fail_in_place) are not announced again. While only failed
            # tiles are left, wait: once they have gone, the wall says it all and closes (below).
            if all(t.failed for t in self.tiles):
                return
            shown = [p for p in self.failed if p in self.failed_shown]
            if shown:
                self.failed = [p for p in self.failed if p not in self.failed_shown]
                self.failed_shown.difference_update(shown)
                if not self.failed:
                    return
        links = all("://" in p for p in self.failed)
        names = [p if "://" in p else (os.path.basename(p) or p) for p in self.failed]
        names = [n if len(n) <= 60 else n[:57] + "…" for n in names]
        why = self.failed_why.pop(self.failed[0], None) if len(self.failed) == 1 else None
        if why is not None:                          # a channel or playlist that could not be listed
            msg = f"Couldn't get videos from {sources.label(self.failed[0])}" + (f": {why[:90]}" if why else ".")
        elif len(names) == 1:
            msg = (f"Couldn't play \"{names[0]}\". " +
                   ("It may be offline, private or not a video." if links else
                    "The file may be damaged or not a video."))
        else:
            what = "links" if links else "files"
            msg = f"Couldn't play {len(names)} {what}: " + ", ".join(names[:3]) + ("…" if len(names) > 3 else "")
        if not self.tiles:                               # nothing at all could be played: say so and leave
            log.info("nothing playable: %s", msg)
            self.failed.clear()
            self.closing = True
            if os.environ.get("TESSERAWALL_NO_DIALOGS") != "1":   # tests cannot click a message box
                self.root.withdraw()
                messagebox.showerror("Tessera Wall", msg)
            self.root.destroy()
            return
        if self.message_tile():                          # otherwise wait until a player can show it
            log.info("could not play: %s", "; ".join(self.failed))
            self.announce(msg, 7000, problem=True)
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
        if "WHEN_FINISHED" in names and C.WHEN_FINISHED in END_MODES:
            self.cycle_end_mode(C.WHEN_FINISHED)
        if "SOURCE_WHEN_FINISHED" in names and C.SOURCE_WHEN_FINISHED in END_MODES:
            self.set_source_end(C.SOURCE_WHEN_FINISHED)
        if "SWITCH_SECONDS" in names:
            self.cycle_switch(C.SWITCH_SECONDS)
        if "CLEAN_MODE" in names:
            self.set_clean(bool(C.CLEAN_MODE), announce=False)
        if set(names) & {"SHOW_MESSAGES", "FOCUS_OUTLINE", "DIM_UNFOCUSED", "SEEKBAR_HEIGHT", "TOP_MENU"}:
            self.relayout()                              # message level, dimming and outline now
            self.bar_next = 0.0
        if "LAYOUT_MODE" in names or "CROP_MODE" in names:   # these are otherwise only starting values
            self.layout_mode = C.LAYOUT_MODE if C.LAYOUT_MODE in LAYOUT_MODES else self.layout_mode
            self.crop_mode = C.CROP_MODE if C.CROP_MODE in CROP_MODES else self.crop_mode
            self.relayout(rearrange=True)

    def on_escape(self):
        if self.blanked is not None:
            self.toggle_blackout()
        elif self.settings:
            self.toggle_settings()
        elif self.help:
            self.toggle_help()
        elif self.pin:
            self.set_pin(None)
        else:
            self.quit()

    def update_bar(self, now):
        """Draw a progress line + time on the focused tile via mpv's OSD; clear it from the old one."""
        if C.SEEKBAR_HEIGHT <= 0 or self.clean:
            if self.bar_tile and self.bar_tile.ipc:            # just switched off: take the last one down
                self.bar_tile.ipc.post({"name": "osd-overlay", "id": 1, "format": "none", "data": ""})
            self.bar_tile = None
            return
        t = self.focus
        if self.bar_tile and self.bar_tile is not t and self.bar_tile.ipc:
            self.bar_tile.ipc.post({"name": "osd-overlay", "id": 1, "format": "none", "data": ""})
        self.bar_tile = t
        if not t or not t.ipc or not t.target or now < self.bar_next:
            return
        self.bar_next = now + 0.25
        pct, pos, dur = t.ipc.value("percent-pos"), t.ipc.value("time-pos"), t.ipc.value("duration")
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
            # A link shows its title (from the site, via yt-dlp) rather than the raw address.
            name = (t.ipc.value("media-title") or t.path) if "://" in t.path else os.path.basename(t.path)
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

    def hide_after(self) -> float:
        """Seconds without movement before the pointer hides: 1 in clean mode, else the setting."""
        return 1.0 if self.clean else C.CURSOR_HIDE_SECONDS

    def set_clean(self, on: bool, announce: bool = True):
        """I (and the clean_mode setting): just the videos. No outline, dimming, progress line, menu bar
        or messages, and the pointer hides after a second. Keys and the mouse still work. The one message
        it shows is its own, at osd level 0, which mpv shows even when other messages are off."""
        if on == self.clean:
            return
        self.clean = on
        log.info("clean mode %s", "on" if on else "off")
        self.relayout()                                  # dimming, message level, outline
        self.bar_next = 0.0
        for x in self.tiles:                             # links still loading: their "Loading…" goes or comes back
            if not x.failed and not x.aspect_known and "://" in x.path:
                self.show_loading(x)
        t = self.focus if self.focus and self.focus.ipc else next((x for x in self.tiles if x.ipc), None)
        if announce and t:
            t.ipc.post("show-text", "Clean mode. Press I to show the controls again" if on else "Controls shown",
                       2000, 0)

    def update_cursor(self, pos, now):
        if pos != self.last_pos:
            self.last_pos, self.last_move = pos, now
            if self.cursor_hidden:
                self.cursor_hidden = False
                if not self.dragging:
                    self.root.config(cursor="")
        elif (self.hide_after() > 0 and not self.cursor_hidden
              and now - self.last_move > self.hide_after()):
            self.cursor_hidden = True
            self.root.config(cursor="none")  # tk swaps in a blank cursor; mouse input reaches tk, not mpv

    # ---- session -----------------------------------------------------------
    def save_session(self):
        if not self.session_path:
            return
        items = []
        for t in self.tiles:
            if t.failed:                       # it could not play: not worth reopening next time
                continue
            pos = t.ipc.value("time-pos") if t.ipc else None
            items.append({"path": t.path, "pos": round(pos or 0, 1)} | ({"pool": t.pool} if t.pool else {}))
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
            if t.failed:                       # showing why it could not play (fail_in_place)
                if t.gone_at is not None and now >= t.gone_at:
                    self.remove(t, "could not play")
                continue
            if t.note is not None and t.aspect_known and t.ipc and t.ipc.value("time-pos") is not None:
                self.clear_note(t)             # "Loading…" on a link whose size never came: it plays now
            if t.proc.poll() is not None:
                code = t.proc.returncode
                if code != 0 and not t.aspect_known:   # never showed a picture
                    self.fail_in_place(t, f"mpv exited with {code}")
                    continue
                elif code != 0 and self.restart(t, code):
                    continue
                self.remove(t, f"mpv exited with {code}")
            elif t.ipc is None:
                try:
                    # Never block the UI thread on a player: no waiting handshake, and the values the
                    # wall needs are pushed by mpv instead of asked for (see ipc.MpvIpc).
                    t.ipc = MpvIpc(t.pipe, timeout=0, handshake=False)
                    t.ipc.observe("idle-active", "dwidth", "dheight", "video-params", "percent-pos",
                                  "time-pos", "duration", "pause", "eof-reached", "media-title")
                    if t.mag:                # a restarted player starts at normal size
                        self.send_magnify(t)
                    t.osd = 0 if (self.clean or not C.SHOW_MESSAGES) else 1
                    t.ipc.set_async("osd-level", t.osd)   # before the hint below, which it may silence
                    self.need_layout = True  # so panscan gets sent
                    if not self.hint_shown:
                        # The launch moment, once per wall: a startup problem with the settings file wins
                        # over the hint. hint_shown is set even when nothing is shown: the update notice
                        # waits for it.
                        self.hint_shown = True
                        if self.notice:
                            t.ipc.post("show-text", self.notice, 8000)
                        elif self.hint_due():
                            t.ipc.post("show-text", HINT_TEXT, 5000)
                except OSError:
                    if now - t.born > CONNECT_TIMEOUT:
                        self.fail_in_place(t, "no IPC connection")
            elif not t.ipc.alive:
                # The connection ended but mpv runs on. Usually it is quitting and its exit is a moment
                # away; if not, the player can no longer be told anything (sound, dimming, seeking), so it
                # is ended and comes back in place through the crash restart above.
                if t.lost_at is None:
                    t.lost_at = now
                elif now - t.lost_at > LOST_PLAYER_S:
                    log.warning("lost the connection to a running player; restarting it: %s", t.path)
                    t.lost_at = None
                    t.proc.kill()
            elif "://" in t.path and t.aspect_known and self.link_stalled(t, now):
                self.fail_in_place(t, "the link ended without playing")
            elif t.aspect_known and self.mode_of(t) in MOVES_ON and t.ipc.value("eof-reached") \
                    and t.ipc.value_seq("eof-reached") > t.swap_seq:
                self.advance(t)          # it ended (keep-open paused it on its last frame): next / random file
            elif t.aspect_known and t.switch_due and now >= t.switch_due:
                self.timed_switch(t, now)
            elif not t.aspect_known:
                # A file mpv cannot open leaves the player idle with an empty window (it does not exit,
                # because of --force-window). Seen twice, 0.6 s apart, so a brief idle moment while a
                # file is still being opened is not mistaken for a failure.
                if t.ipc.value("idle-active"):
                    if t.idle_since is None:
                        t.idle_since = now
                    elif now - t.idle_since > 0.6:
                        self.fail_in_place(t, "mpv could not open the file")
                    continue
                t.idle_since = None
                if t.swapped:
                    # After a swap mpv reports the new file's name before its size: the size only
                    # counts once the new file has loaded AND a size (or video change) came after that.
                    loaded = t.ipc.event_seq("file-loaded")
                    if loaded <= t.swap_seq:
                        continue
                    if max(t.ipc.value_seq("dwidth"), t.ipc.event_seq("video-reconfig")) <= loaded:
                        continue
                w, h = t.ipc.value("dwidth"), t.ipc.value("dheight")
                if w and h:
                    rot = (t.ipc.value("video-params") or {}).get("rotate") or 0
                    t.aspect = h / w if rot in (90, 270) else w / h
                    t.size = (w, h)
                    t.aspect_known = True
                    self.need_layout = True
                    if t.fade == "hold":
                        t.fade = now
                    self.clear_note(t)
                    log.info("aspect known %.4f (%sx%s rot=%s): %s", t.aspect, w, h, rot, t.path)
                elif now - t.born > ASPECT_TIMEOUT:
                    t.aspect_known = True
                    log.info("aspect unknown, keeping 16:9: %s", t.path)
        while True:
            try:
                self.add(self.incoming.get_nowait())
            except queue.Empty:
                break
        self.take_link_retries()
        self.take_pool_results()
        self.take_prepared()
        self.update_fades(now)
        self.prepare_next(now)
        self.report_failed()
        if self.closing:
            return
        if not self.tiles:
            if self.waiting():                 # a retry or a channel list is on its way: keep the wall
                self.root.after(C.TICK_MS, self.tick)
                return
            return self.quit()
        if self.need_layout:
            self.relayout(rearrange=True)
        pos = self.cursor()
        self.hover(pos)
        self.update_cursor(pos, now)
        self.update_bar(now)
        self.update_topbar(pos, now)
        self.show_update_notice()
        self.keep_in_sync(now)
        self.report_refused()
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
    SYNCED = {"multiply", "set", "frame-step", "frame-back-step"}   # speed and stepping follow in sync mode

    def cmd(self, *args):
        if not (self.focus and self.focus.ipc):
            return
        name = args[1] if args[0] == "osd-msg" else args[0]
        if self.sync and args[:2] == ("cycle", "pause"):
            state = not self.focus.ipc.value("pause", False)   # one decision for all, so they stay together
            for t in self.tiles:
                if t.ipc:
                    t.ipc.set_async("pause", state)
            return
        targets = self.tiles if (self.sync and name in self.SYNCED) else [self.focus]
        for t in targets:
            if t.ipc:
                t.ipc.post(*(args if t is self.focus or args[0] != "osd-msg" else args[1:]))

    def toggle_sync(self, keep_offsets: bool = False):
        """G: link playback. Pause, seek, speed and frame steps then act on every video, and videos that
        drift more than a quarter of a second are pulled back to the focused one's position.
        Ctrl+G (keep_offsets): the same, but every video keeps its current time difference to the others,
        for cameras that started recording at different moments; nothing jumps when it starts."""
        lead = self.focus or (self.tiles[0] if self.tiles else None)
        if not lead or not lead.ipc:
            return
        self.sync = not self.sync
        self.sync_offsets = None
        log.info("sync %s%s", "on" if self.sync else "off", " (keeping offsets)" if self.sync and keep_offsets else "")
        if self.sync:
            pos, paused = lead.ipc.value("time-pos") or 0, lead.ipc.value("pause", False)
            if keep_offsets:
                self.sync_offsets = {t: t.ipc.value("time-pos") or 0 for t in self.tiles if t.ipc}
            for t in self.tiles:
                if t.ipc and t is not lead:
                    if not keep_offsets:
                        t.ipc.post("seek", pos, "absolute")
                    t.ipc.set_async("pause", paused)
                    t.ipc.set_async("speed", lead.ipc.value("speed", 1.0))
        text = ("Sync on: every video keeps its place relative to this one" if keep_offsets
                else "Sync on: every video follows this one") if self.sync else "Sync off"
        lead.ipc.post("show-text", text, 2500)
        self.sync_checked = time.monotonic()

    def keep_in_sync(self, now):
        if not self.sync or now - self.sync_checked < 1.0:
            return
        self.sync_checked = now
        lead = self.focus or (self.tiles[0] if self.tiles else None)
        if not lead or not lead.ipc or lead.ipc.value("time-pos") is None:
            return
        pos = lead.ipc.value("time-pos")
        offsets = self.sync_offsets
        for t in self.tiles:
            if t is lead or not t.ipc or t.ipc.value("time-pos") is None:
                continue
            if offsets is not None:
                if t not in offsets or lead not in offsets:
                    continue                          # added or swapped since: not part of this sync
                want = pos + offsets[t] - offsets[lead]
                dur = t.ipc.value("duration")
                if want < 0 or (dur and want > dur):
                    continue                          # outside this video: leave it alone
            else:
                want = pos
            drift = t.ipc.value("time-pos") - want
            if abs(drift) > 0.25:
                t.ipc.post("seek", round(want, 3), "absolute")
                log.info("sync: corrected %.2fs drift", drift)

    def paste(self):
        """Ctrl+V: open the links (YouTube, Twitch, stream addresses...), paths, or files copied in Explorer
        that are on the clipboard. Read in a worker thread so a slow clipboard owner cannot freeze the wall."""
        def done(items):
            target = self.focus or (self.tiles[0] if self.tiles else None)
            log.info("pasted %s item(s)", "no answer" if items is None else len(items))
            if items is None:
                msg = "The app that copied this is not answering"
            elif items:
                msg = f"Opening {len(items)} pasted video{'s' if len(items) != 1 else ''}"
            else:
                msg = "Nothing to play on the clipboard"
            if target and target.ipc:
                target.ipc.post("show-text", msg, 2000)
            for item in items or []:
                self.incoming.put(item)
        paste_async(self.root, done)

    def focus_number(self, n: int):
        """1-9: focus and pin the n-th video (wall order: rows top to bottom, left to right); the same
        number again releases it."""
        if n > len(self.tiles):
            return
        t = self.tiles[n - 1]
        self.set_pin(None if self.pin is t else t)

    def add_one(self):
        """+: one more random video from the focused (or last) video's folder; of the same shape in a
        template wall. The wall re-arranges for the new count."""
        src = self.focus or (self.tiles[-1] if self.tiles else None)
        if not src or not src.ipc or ("://" in src.path and not src.pool):
            return
        new = self.pick_file(src, "random")
        if not new:
            src.ipc.post("show-text", self.no_other_message(src))
            return
        shape = self.shape_of.get(os.path.normcase(src.path))
        if shape:
            self.shape_of[os.path.normcase(new)] = shape
        log.info("one more video: %s", new)
        self.incoming.put(new)

    def remove_one(self):
        """-: close the last video that is not the focused one (never the last video left)."""
        if len(self.tiles) <= 1:
            return
        others = [t for t in self.tiles if t is not self.focus and t is not self.audio_lock] \
            or [t for t in self.tiles if t is not self.focus]
        self.remove(others[-1], "one fewer (minus key)")

    def toggle_blackout(self):
        """Ctrl+B: hide every video, pause and mute everything, and stop "move on"; Ctrl+B or Esc again
        brings everything back as it was."""
        if self.blanked is None:
            self.blanked = {t: bool(t.ipc.value("pause", False)) for t in self.tiles if t.ipc}
            for t in self.tiles:
                t.frame.place_forget()
                t.placed = None
                if t.ipc:
                    t.ipc.set_async("pause", True)
                    t.ipc.set_async("mute", True)
                    t.gain = 0.0
            self.ring.place_forget()
            log.info("blackout on")
        else:
            paused, self.blanked = self.blanked, None
            for t in self.tiles:
                if t.ipc:
                    t.ipc.set_async("pause", paused.get(t, False))
            self.anim_start = time.monotonic()
            self.relayout()
            log.info("blackout off")

    def report_refused(self):
        if not self.refused:
            return
        target = self.message_tile()
        if target:
            n = len(self.refused)
            target.ipc.post("show-text", f"{n} video{'s' if n != 1 else ''} not opened: {self.refused[0]}", 7000, 0)
            log.info("refused reported: %d (%s)", n, self.refused[0])
            self.refused = []

    def move_focus(self, step):
        """Tab / Shift+Tab: focus the next / previous video in wall order (moving a pin, if set)."""
        if not self.tiles:
            return "break"
        i = self.tiles.index(self.focus) if self.focus in self.tiles else (-1 if step > 0 else 0)
        t = self.tiles[(i + step) % len(self.tiles)]
        if self.pin:
            self.set_pin(t, zoom=self.zoom)
        else:
            self.set_focus(t)
        return "break"                 # stop tk's own Tab handling

    def save_wall_as(self):
        """Ctrl+S / menu: save the open videos and positions under a name, for the start window."""
        items = [{"path": t.path, "pos": round((t.ipc.value("time-pos") if t.ipc else 0) or 0, 1)}
                 | ({"pool": t.pool} if t.pool else {}) for t in self.tiles if not t.failed]
        if not items:
            return
        self.dialog_open = True
        try:
            name = ask_text(self.root, "Save wall", "Name for this wall:", "Save", topmost=True)
        finally:
            self.dialog_open = False
            self.root.focus_set()
        if not name or not name.strip():
            return
        try:
            path = save_wall(self.config_path, name, items)
        except OSError as e:                          # a full disk or a locked folder must not close the wall
            log.warning("wall not saved: %s", e)
            text = f"Couldn't save \u201c{name.strip()}\u201d: {e.strerror or e}"
        else:
            log.info("wall saved: %s (%d videos)", path, len(items))
            text = f"Saved \u201c{name.strip()}\u201d"
        target = self.message_tile()                  # the dialog was modal, but the wall may have changed
        if target:
            target.ipc.post("show-text", text[:120], 4000 if text.startswith("Couldn't") else 2500)

    def change_speed(self, factor):
        """[ and ]: slower / faster by 10 %; Backspace (factor None): normal speed. mpv shows the result."""
        if factor is None:
            self.cmd("osd-msg", "set", "speed", "1")   # "set" takes its value as text
        else:
            self.cmd("osd-msg", "multiply", "speed", factor)

    def cycle_track(self, kind: str):
        """T / A: next subtitle / audio track of the focused video; mpv names the track on screen."""
        self.cmd("osd-msg", "cycle", kind)

    def snapshot(self):
        """P: save the focused video's current frame (without overlays) as a PNG."""
        t = self.focus
        if not t or not t.ipc:
            return
        folder = C.SNAPSHOT_DIR or os.path.join(pictures_folder(), "Tessera Wall")
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            t.ipc.post("show-text", f"Couldn't save a snapshot: {e}"[:120], 4000)
            return
        base = "stream" if "://" in t.path else os.path.splitext(os.path.basename(t.path))[0]
        base = re.sub(r'[<>:"/\\|?*]', "_", base)[:80]
        secs = int(t.ipc.value("time-pos") or 0)
        at = (f"{secs // 3600}h{secs % 3600 // 60:02d}m{secs % 60:02d}s" if secs >= 3600
              else f"{secs // 60}m{secs % 60:02d}s")
        path = os.path.join(folder, f"{base} at {at} {time.strftime('%Y%m%d-%H%M%S')}.png")
        t.ipc.post("screenshot-to-file", path, "video")
        t.ipc.post("show-text", f"Snapshot saved in {folder}", 2500)
        log.info("snapshot: %s", path)

    def pause_all(self):
        ref = self.focus or (self.tiles[0] if self.tiles else None)
        if not ref or not ref.ipc:
            return
        state = not ref.ipc.value("pause", False)
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
        for t in (self.tiles if (everyone or self.sync) else [self.focus]):
            if t and t.ipc:
                t.ipc.post("seek", seconds, "relative")
        self.bar_next = 0.0

    def restart_all(self):
        log.info("restart all")
        for t in self.tiles:
            if t.ipc:
                t.ipc.post("seek", 0, "absolute")
        self.bar_next = 0.0

    def close_focused(self):
        """X, Delete, middle-click: close the focused video (a failed one, whose player is gone, too)."""
        t = self.focus
        if t and t.failed:
            self.remove(t, "closed")
            return
        self.cmd("quit")

    def lock_audio(self, t: Tile | None):
        """The tile menu's sound item: keep the sound on t (None: let it follow the pointer)."""
        if self.audio_lock is t:
            return
        if t is None:
            self.toggle_audio_lock()
            return
        self.audio_lock = t
        log.info("audio lock -> %s", self.tiles.index(t))
        if t.ipc:
            t.ipc.post("show-text", "Sound stays on this video (K to release)", 2000)

    def tile_menu(self, e):
        """Right-click: what can be done to the video under the pointer, each item with its key, so the
        menu also teaches the keyboard. The video gets the focus first (a pin moves to it, as Tab does),
        so every item acts on it as its key would."""
        t = self.tile_under_cursor((e.x_root, e.y_root))
        if t is None or self.closing:
            return
        if self.pin and self.pin is not t:
            self.set_pin(t, zoom=self.zoom)
        elif self.focus is not t:
            self.set_focus(t, (e.x_root, e.y_root))
        entries = []

        def item(text, key, action):
            def run():
                if t in self.tiles:
                    if self.focus is not t:
                        self.set_focus(t)
                    action()
            entries.append((text, "", run, None, key))
        if t.failed:
            item("Close", "X", lambda: self.remove(t, "closed from its menu"))
        else:
            paused = bool(t.ipc and t.ipc.value("pause"))
            zoomed = self.zoom and self.pin is t
            item("Play" if paused else "Pause", "Space", lambda: self.cmd("cycle", "pause"))
            item("Unpin" if self.pin is t else "Pin", "Click", lambda: self.set_pin(None if self.pin is t else t))
            item("Zoom back" if zoomed else "Zoom", "Double-click",
                 lambda: self.set_pin(None) if zoomed else self.set_pin(t, zoom=True))
            item("Let the sound follow the pointer" if self.audio_lock is t else "Keep the sound on this video",
                 "K", lambda: self.lock_audio(None if self.audio_lock is t else t))
            if t.mag:
                item("Normal size", "Z", lambda: self.unmagnify(t))
            if not ("://" in t.path and not t.pool):
                item("Next video", "Page Down", lambda: self.step_file(1))
                item("Random video", "N", self.swap_random)
            item("Next audio track", "A", lambda: self.cycle_track("audio"))
            item("Next subtitles", "T", lambda: self.cycle_track("sub"))
            item("Snapshot", "P", self.snapshot)
            item("Close", "X", self.close_focused)

        def closed():
            self.dialog_open = False
            self.root.focus_set()
        self.pressing, self.press_tile, self.dragging = False, None, False
        self.dialog_open = True                          # the list has the keys while it is open
        m = metrics_for(self.root)
        pop = popup_list(self.root, e.x_root, e.y_root, entries, m, on_close=closed)
        log.info("tile menu for %d: %s", self.tiles.index(t), " ".join(
            f"{re.sub(r'[^a-z]+', '-', x[0].lower()).strip('-')}={b.winfo_rootx()},{b.winfo_rooty()},{b.winfo_width()},{b.winfo_height()}"
            for x, b in zip(entries, pop.rows)))

    def reset(self):
        self.pin, self.zoom, self.audio_lock = None, False, None
        self.set_focus(None)

    def bind_keys(self):
        bind_all = self.root.bind_all

        def b(seq, fn):
            """bind_all, plus the upper-case twin of a letter key: with Caps Lock on (or Shift held)
            tk reports <R>, not <r>, and without this every letter key silently stops working."""
            guarded = lambda e, fn=fn: None if self.dialog_open else fn(e)   # a dialog has the keys
            bind_all(seq, guarded)
            if len(seq) == 3 and seq[1].isalpha() and seq[1].islower():
                bind_all(f"<{seq[1].upper()}>", guarded)

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
        b("<Control-MouseWheel>", lambda e: self.magnify(e, 1 if e.delta > 0 else -1))
        b("<z>", lambda e: self.unmagnify(self.focus))
        b("<i>", lambda e: self.set_clean(not self.clean))
        b("<r>", lambda e: self.restart_all())
        b("<Up>", lambda e: self.change_volume(C.VOLUME_STEP))
        b("<Down>", lambda e: self.change_volume(-C.VOLUME_STEP))
        b("<n>", lambda e: self.swap_random())
        b("<k>", lambda e: self.toggle_audio_lock())
        b("<e>", lambda e: self.cycle_end_mode())
        for seq in ("<Control-e>", "<Control-E>"):   # not Shift+E: that is E with Caps Lock to tk
            bind_all(seq, lambda e: None if self.dialog_open else self.cycle_switch())
        for seq in ("<Control-g>", "<Control-G>"):
            bind_all(seq, lambda e: None if self.dialog_open else self.toggle_sync(keep_offsets=True))
        for seq in ("<Control-b>", "<Control-B>"):
            bind_all(seq, lambda e: None if self.dialog_open else self.toggle_blackout())
        for seq in ("<Control-v>", "<Control-V>"):
            bind_all(seq, lambda e: None if self.dialog_open else self.paste())
        for n in range(1, 10):
            b(f"<Key-{n}>", lambda e, n=n: self.focus_number(n))
        for seq in ("<plus>", "<equal>", "<KP_Add>"):
            b(seq, lambda e: self.add_one())
        for seq in ("<minus>", "<KP_Subtract>"):
            b(seq, lambda e: self.remove_one())
        b("<bracketleft>", lambda e: self.change_speed(1 / 1.1))
        b("<bracketright>", lambda e: self.change_speed(1.1))
        b("<BackSpace>", lambda e: self.change_speed(None))
        b("<comma>", lambda e: self.cmd("frame-back-step"))
        b("<period>", lambda e: self.cmd("frame-step"))
        b("<b>", lambda e: self.cmd("osd-msg", "ab-loop"))
        b("<t>", lambda e: self.cycle_track("sub"))
        b("<a>", lambda e: self.cycle_track("audio"))
        b("<p>", lambda e: self.snapshot())
        b("<g>", lambda e: self.toggle_sync())
        # Bound on the wall window, not with bind_all: Tk defines <<NextWindow>> as <Key-Tab>, and its own
        # <<NextWindow>> binding on "all" wins over a bind_all("<Tab>"), which then never fires. The wall
        # window's tag comes before "all", and returning "break" skips Tk's traversal there, while the
        # Settings window and dialogs keep ordinary Tab between their fields.
        self.root.bind("<Tab>", lambda e: self.move_focus(1))
        self.root.bind("<Shift-Tab>", lambda e: self.move_focus(-1))
        for seq in ("<Control-s>", "<Control-S>"):
            bind_all(seq, lambda e: None if self.dialog_open else self.save_wall_as())
        # The menu bar from the keyboard. Its dialogs need the bar as their parent (it is above the wall).
        bind_all("<F10>", lambda e: None if self.dialog_open else (self.show_topbar(keyboard=True), "break")[1])

        def from_bar(action):
            if self.dialog_open:
                return None
            # Opened as F10 does: that holds the bar while it is built (the wall's tick would otherwise
            # hide a bar the pointer is not near, leaving the dialog without a parent).
            self.show_topbar(keyboard=True)
            if self.topbar is None:
                return "break"
            action()
            return "break"
        for seq in ("<Control-o>", "<Control-O>"):          # with Shift: a folder
            bind_all(seq, lambda e: from_bar(lambda: self.add_from_dialog(folder=bool(e.state & 0x1))))
        for seq in ("<Control-f>", "<Control-F>"):
            bind_all(seq, lambda e: from_bar(self.search_from_dialog))
        b("<F5>", lambda e: self.reload_config())
        b("<s>", lambda e: self.toggle_settings())
        b("<u>", lambda e: self.open_update())
        for seq in ("<h>", "<question>", "<F1>"):
            b(seq, lambda e: self.toggle_help())
        b("<l>", lambda e: self.cycle_mode("layout_mode", LAYOUT_MODES, "Arrangement"))
        b("<c>", lambda e: self.cycle_mode("crop_mode", CROP_MODES, "Black bars"))
        b("<Next>", lambda e: self.step_file(1))     # Page Down
        b("<Prior>", lambda e: self.step_file(-1))   # Page Up
        b("<ButtonRelease-1>", self.on_release)
        for seq in ("<x>", "<Delete>", "<Button-2>"):
            b(seq, lambda e: self.close_focused())
        b("<Button-3>", self.tile_menu)
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
        for t in self.tiles:                 # ask all at once, then wait on one shared deadline: a few frozen
            if t.ipc:                        # players must not hold the full-screen window up for seconds each
                t.ipc.post("quit")
        deadline = time.monotonic() + 1.5
        for t in self.tiles:
            try:
                t.proc.wait(max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                t.proc.kill()
        self.root.destroy()

    def kill_all(self):
        for t in self.tiles:
            if t.proc.poll() is None:
                t.proc.kill()
