"""The start window, the Settings window, and the small window helpers they and the wall share
(dark title bar, fades, rounded corners, hover colours, icon, drag-and-drop)."""
import ctypes
import logging
import os
import queue
import tkinter as tk
from ctypes import wintypes
from tkinter import filedialog

import config as C
import updates
from files import expand, list_walls
from ytdlp import links_from_text
from winapi import GA_ROOT, monitor_under_cursor, read_clipboard, user32

log = logging.getLogger("tesserawall")


def dropped_items(root, data: str) -> list:
    """What a drop carries: files and folders from Explorer, or a link (text) dragged from a browser."""
    if "://" in data:
        return [i for item in links_from_text(data) for i in ([item] if "://" in item else expand([item]))]
    return expand(list(root.tk.splitlist(data)))


def items_from_clipboard(text: str, files: list) -> list:
    """What Ctrl+V plays: files copied in Explorer (folders expanded), else the links and paths in the text."""
    if files:
        return expand(files)
    return [i for item in links_from_text(text) for i in ([item] if "://" in item else expand([item]))]


PASTE_TIMEOUT_MS = 5000


def paste_async(root, done):
    """Ctrl+V without freezing: read the clipboard (and list any copied folders) in a worker thread, then call
    done(items) on the Tk thread, or done(None) if the app that owns the clipboard has not answered within
    PASTE_TIMEOUT_MS (the worker is then abandoned; it ends when that app answers or Windows gives up)."""
    import threading
    import time
    result = queue.Queue()

    def work():
        try:
            result.put(items_from_clipboard(*read_clipboard()))
        except Exception as e:
            log.warning("clipboard read failed: %s", e)
            result.put([])
    threading.Thread(target=work, daemon=True, name="paste").start()
    deadline = time.monotonic() + PASTE_TIMEOUT_MS / 1000

    def poll():
        try:
            items = result.get_nowait()
        except queue.Empty:
            if time.monotonic() < deadline:
                root.after(50, poll)
            else:
                log.info("clipboard: no answer from the app that owns it")
                done(None)
            return
        done(items)
    root.after(20, poll)


def enable_drop(root, incoming: queue.Queue):
    """Optional: accept files, and links dragged from a browser, dropped on the running wall.
    Needs `pip install tkinterdnd2`."""
    try:
        from tkinterdnd2 import DND_FILES, DND_TEXT
        root.drop_target_register(DND_FILES, DND_TEXT)
        root.dnd_bind("<<Drop>>", lambda e: [incoming.put(p) for p in dropped_items(root, e.data)])
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
# settings file. Two columns of cards; each entry is one of
#   ("column",)                                      start the right-hand column
#   ("section", title)                               a card with this heading
#   ("scale", NAME, label, low, high, step, unit)    unit formats the value shown, e.g. "{} ms"
#   ("choice", NAME, label, [(value, text), ...])
#   ("check", NAME, label)  or  ("check", NAME, label, value_when_on, value_when_off)
# followed, for the last three, by an optional dict: {"hint": grey line under the label,
# "clean": True for what clean mode overrides (greyed while it is on)}.
SETTINGS_UI = [
    ("section", "When you point at a video"),
    ("scale", "FOCUS_SCALE", "How much it grows", 1.0, 2.5, 0.05, "{:.2f}×"),
    ("scale", "DIM_UNFOCUSED", "Dim the others", 0, 60, 1, "{} %", {"clean": True}),
    ("scale", "CROSSFADE_MS", "Sound fade", 0, 1000, 10, "{} ms", {"hint": "From one video's sound to the next"}),
    ("section", "Layout"),
    ("choice", "LAYOUT_MODE", "Arrangement", [("grow", "Grow on hover"), ("flat", "Best fit"), ("grid", "Even grid")]),
    ("choice", "CROP_MODE", "Black bars", [("limited", "Crop a little"), ("fill", "Crop to fill"), ("fit", "Never crop")]),
    ("section", "Playing"),
    ("choice", "WHEN_FINISHED", "When a video ends", [("loop", "Play it again"), ("next", "Next"), ("random", "Random")],
     {"hint": "Videos from files and folders"}),
    ("choice", "SWITCH_SECONDS", "Move on", [(0, "Never"), (30, "30 s"), (60, "1 min"), (120, "2 min")],
     {"hint": "Change a video after a while"}),
    ("check", "RANDOM_START", "Start at a random point"),
    ("section", "Other"),
    ("check", "UNFOCUSED_SKIP", "Save power", "nonref", "",
     {"hint": "The other videos play at a lower, choppy frame rate"}),
    ("check", "UPDATE_CHECK", "Check for a new version", {"hint": "Once a week"}),
    ("check", "SAFE_RENDERING", "Compatibility mode", {"hint": "Recommended. Applies to videos opened after this"}),
    ("column",),
    ("section", "Channels, playlists and searches"),
    ("scale", "SOURCE_COUNT", "Videos to open", 1, 24, 1, "{}", {"hint": "How many each one adds to the wall"}),
    ("choice", "SOURCE_ORDER", "Which ones", [("random", "At random"), ("newest", "Newest first")]),
    ("choice", "SOURCE_WHEN_FINISHED", "When one ends",
     [("random", "Another"), ("next", "The next"), ("loop", "Play it again")]),
    ("section", "On screen"),
    ("check", "CLEAN_MODE", "Clean mode", {"hint": "Only the videos: overrides the greyed-out ones. I in the wall"}),
    ("check", "FOCUS_OUTLINE", "Outline", {"hint": "Around the video you point at", "clean": True}),
    ("check", "SEEKBAR_HEIGHT", "Progress line and time", 6, 0, {"hint": "On the video you point at", "clean": True}),
    ("check", "SEEKBAR_SHOW_NAME", "File name next to the time", {"clean": True}),
    ("check", "TOP_MENU", "Menu bar", {"hint": "When the pointer rests on the top edge", "clean": True}),
    ("check", "SHOW_MESSAGES", "Messages", {"hint": "Volume, modes and errors, on the videos", "clean": True}),
    ("check", "SHOW_HELP_HINT", "Controls hint when a wall opens", {"clean": True}),
    ("check", "CURSOR_HIDE_SECONDS", "Hide the pointer", 2.0, 0.0, {"hint": "When it isn't moving", "clean": True}),
]


def typed(name: str, value: str):
    """A choice's value comes back from tk as text; give whole-number settings their number back."""
    default = C.DEFAULTS[name]
    if isinstance(default, int) and not isinstance(default, bool):
        return int(value)
    return value


def _split(item):
    """An entry without its optional trailing options dict, and that dict."""
    return (item[:-1], item[-1]) if isinstance(item[-1], dict) else (item, {})


_AA_CACHE: dict = {}


def _rgb(colour: str) -> tuple:
    return tuple(int(colour[i:i + 2], 16) for i in (1, 3, 5))


def _aa_corners(master, r: int, fill: str, bg: str, stroke: str | None, sw: int) -> list:
    """The four r x r corner images of a rounded rectangle, smoothed (4 x 4 samples a pixel): tk's own ovals
    have hard, stair-stepped edges. Cached per colour set; held by the Tk root so they outlive one window."""
    key = (str(master), r, fill, bg, stroke, sw)
    if key in _AA_CACHE:
        return _AA_CACHE[key]
    import math
    F, B, S = _rgb(fill), _rgb(bg), _rgb(stroke or fill)
    n, rows = 4, []
    for y in range(r):
        row = []
        for x in range(r):
            cf = cs = 0
            for sy in range(n):
                for sx in range(n):
                    d = math.hypot(r - (x + (sx + 0.5) / n), r - (y + (sy + 0.5) / n))
                    if d <= r - sw:
                        cf += 1
                    elif d <= r:
                        cs += 1
            cf, cs = cf / n / n, cs / n / n
            row.append("#%02x%02x%02x" % tuple(round(F[i] * cf + S[i] * cs + B[i] * (1 - cf - cs)) for i in range(3)))
        rows.append(row)
    images = []
    for flip_x, flip_y in ((0, 0), (1, 0), (0, 1), (1, 1)):          # top-left, top-right, bottom-left, bottom-right
        img = tk.PhotoImage(master=master, width=r, height=r)
        img.put(" ".join("{" + " ".join(rows[r - 1 - y if flip_y else y][::-1 if flip_x else 1]) + "}"
                         for y in range(r)))
        images.append(img)
    _AA_CACHE[key] = images
    return images


def rounded(cv, x0, y0, x1, y1, r, fill: str, bg: str, stroke: str | None = None, sw: int = 0, tag="shape"):
    """Draw a smooth rounded rectangle (or, with r = half the height, a pill or circle) on a canvas whose
    background behind it is `bg`. With `stroke`, a `sw` px border of that colour."""
    x0, y0, x1, y1 = int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))
    r = max(1, min(int(r), (x1 - x0) // 2, (y1 - y0) // 2))
    tl, tr, bl, br = _aa_corners(cv._root(), r, fill, bg, stroke if sw else None, sw if stroke else 0)
    for img, x, y in ((tl, x0, y0), (tr, x1 - r, y0), (bl, x0, y1 - r), (br, x1 - r, y1 - r)):
        cv.create_image(x, y, anchor="nw", image=img, tags=tag)
    edge = stroke if (stroke and sw) else fill
    cv.create_rectangle(x0 + r, y0, x1 - r, y1, fill=edge, width=0, tags=tag)
    cv.create_rectangle(x0, y0 + r, x1, y1 - r, fill=edge, width=0, tags=tag)
    if stroke and sw:
        cv.create_rectangle(x0 + r, y0 + sw, x1 - r, y1 - sw, fill=fill, width=0, tags=tag)
        cv.create_rectangle(x0 + sw, y0 + r, x1 - sw, y1 - r, fill=fill, width=0, tags=tag)


def settings_window(parent, config_path: str, apply, on_close, topmost: bool = False) -> tk.Toplevel:
    """Build and show the settings window. Each change is written to the settings file at once and
    `apply(names)` is called, so the wall behind it updates while the user watches.

    Laid out after Windows 11's Settings (Fluent 2): one rounded card per setting, title and grey description
    on the left, the control on the right. Sizes are Fluent's (14 px body text, 4 px grid, 32 px controls)
    scaled to the screen by f(). Every choice control has the same width, its options split evenly, so the
    controls line up down each column; sliders take that width too.

    Its widgets are taken out of the app-wide key and mouse bindings (bindtag "all"): otherwise Space
    on a switch would also pause a video, and clicks would reach the wall's handlers.
    """
    import tkinter.font as tkfont
    u = max(12, parent.winfo_screenheight() // 90)

    def f(px):               # a Fluent size (designed around 14 px text) at this screen's text size u
        return max(1, round(px * u / 14))

    bg, card, edge, well, ctl = "#101214", "#1A1E23", "#232830", "#121519", "#2C323A"
    fg, dim, faint, accent, accent_hot, hot = "#E6E6E6", "#A3ACB6", "#5A626B", "#6CB6FF", "#8FC8FF", "#262C33"
    body, caption = ("Segoe UI", -f(14)), ("Segoe UI", -f(12))
    strong, title_font = ("Segoe UI Semibold", -f(14)), ("Segoe UI Semibold", -f(28))
    win = tk.Toplevel(parent)
    win.withdraw()
    win.title("Tessera Wall settings")
    win.configure(bg=bg, padx=f(32), pady=f(24))
    win.resizable(False, False)
    measure = tkfont.Font(root=win, family="Segoe UI", size=-f(14)).measure
    measure_strong = tkfont.Font(root=win, family="Segoe UI Semibold", size=-f(14)).measure

    items = [_split(i) for i in SETTINGS_UI]
    seg_pad = f(12)
    control_w = max([f(240)] + [len(i[0][3]) * (max(measure(t) for _, t in i[0][3]) + 2 * seg_pad)
                                for i in items if i[0][0] == "choice"])
    control_h, card_pad_x, card_pad_y, card_min_h = f(32), f(16), f(8), f(56)
    hint_wrap = f(280)

    pending: dict = {}
    timer = [None]
    ready = [False]          # controls fire when first given a value; ignore until shown
    refreshers, located = [], {}
    greyed = []              # (title, description) of what clean mode overrides
    clean = tk.BooleanVar(win)

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

    def grey_out(*a):
        for lab, hint in greyed:
            lab.config(fg=faint if clean.get() else fg)
            if hint:
                hint.config(fg=faint if clean.get() else dim)
    clean.trace_add("write", grey_out)

    tk.Label(win, text="Settings", bg=bg, fg="white", font=title_font).pack(anchor="w", pady=(0, f(16)))
    body_frame = tk.Frame(win, bg=bg)
    body_frame.pack(fill="both")
    columns = [tk.Frame(body_frame, bg=bg)]
    columns[0].grid(row=0, column=0, sticky="nsew")
    body_frame.grid_columnconfigure(0, weight=1, uniform="col")

    def section(text):
        top = f(16) if columns[-1].winfo_children() else 0
        tk.Label(columns[-1], text=text, bg=bg, fg=fg, font=strong).pack(anchor="w", pady=(top, f(8)))

    def card_row(label, opts, click=None):
        """One setting: a rounded card with its title (and description) on the left; returns the frame on the
        right for the control, and the card itself."""
        cv = tk.Canvas(columns[-1], bg=bg, highlightthickness=0, height=card_min_h)
        cv.pack(fill="x", pady=(0, f(4)))
        inner = tk.Frame(cv, bg=card)
        item = cv.create_window(card_pad_x, card_pad_y, anchor="nw", window=inner)

        def redraw(e=None):
            w_, h_ = cv.winfo_width(), cv.winfo_height()
            cv.delete("shape")
            rounded(cv, 0, 0, w_, h_, f(5), card, bg, edge, 1)
            cv.itemconfig(item, width=w_ - 2 * card_pad_x)
            cv.coords(item, card_pad_x, (h_ - inner.winfo_reqheight()) // 2)

        def fit(e=None):         # a canvas does not size itself to the frame inside it: ask for that size
            w_ = inner.winfo_reqwidth() + 2 * card_pad_x
            h_ = max(card_min_h, inner.winfo_reqheight() + 2 * card_pad_y)
            if (int(cv.cget("width")), int(cv.cget("height"))) != (w_, h_):
                cv.config(width=w_, height=h_)
            else:
                redraw()
        cv.bind("<Configure>", redraw)
        inner.bind("<Configure>", fit)
        inner.grid_columnconfigure(0, weight=1)
        left = tk.Frame(inner, bg=card)
        left.grid(row=0, column=0, sticky="w")
        lab = tk.Label(left, text=label, bg=card, fg=fg, font=body, anchor="w")
        lab.pack(anchor="w")
        hint = None
        if opts.get("hint"):
            hint = tk.Label(left, text=opts["hint"], bg=card, fg=dim, font=caption, anchor="w", justify="left",
                            wraplength=hint_wrap)
            hint.pack(anchor="w")
        right = tk.Frame(inner, bg=card)
        right.grid(row=0, column=1, sticky="e", padx=(f(16), 0))
        if opts.get("clean"):
            greyed.append((lab, hint))
        if click:                     # the whole card toggles, not only the switch
            for w in (cv, inner, left, lab, right) + ((hint,) if hint else ()):
                w.bind("<Button-1>", click)
                w.config(cursor="hand2")
        return cv, right

    def slider(parent_, name, low, high, step, unit):
        whole = isinstance(C.DEFAULTS[name], int)
        value_w = measure("1000 ms") + f(8)
        # The canvas is the track only, with a margin of u // 2 at each end (tests click at known fractions).
        sw_, sh_, pad = control_w - value_w, control_h, u // 2
        cv = tk.Canvas(parent_, width=sw_, height=sh_, bg=card, highlightthickness=0, cursor="hand2")
        cv.pack(side="left")
        box = tk.Frame(parent_, bg=card, width=value_w, height=sh_)
        box.pack(side="left")
        box.pack_propagate(False)
        shown = tk.Label(box, bg=card, fg=dim, font=body, anchor="e")
        shown.pack(fill="both", expand=True)

        def draw(value):
            x = pad + (sw_ - 2 * pad) * (value - low) / (high - low)
            mid, t = sh_ // 2, max(2, f(4) // 2)
            cv.delete("all")
            cv.create_rectangle(pad, mid - t, sw_ - pad, mid + t, fill=ctl, width=0)
            cv.create_rectangle(pad, mid - t, x, mid + t, fill=accent, width=0)
            r_ = pad                                       # Fluent's thumb: a dark disc with an accent dot
            rounded(cv, x - r_, mid - r_, x + r_, mid + r_, r_, "#3A4048", card)
            r2 = max(2, r_ * 5 // 9)
            rounded(cv, x - r2, mid - r2, x + r2, mid + r2, r2, accent, "#3A4048")
            shown.config(text=unit.format(int(value) if whole else value))

        def pick(e):
            frac = min(1.0, max(0.0, (e.x - pad) / (sw_ - 2 * pad)))
            value = low + round(frac * (high - low) / step) * step
            value = int(round(value)) if whole else round(value, 2)
            draw(value)
            change(name, value, 150)

        cv.bind("<Button-1>", pick)
        cv.bind("<B1-Motion>", pick)
        refreshers.append(lambda: draw(getattr(C, name)))
        located[name] = cv

    def segments(parent_, name, options):
        """A segmented choice, control_w wide, options of equal width; the chosen one is blue.
        Left and Right change it when it has the keyboard focus."""
        cv = tk.Canvas(parent_, width=control_w, height=control_h, bg=card, highlightthickness=0, cursor="hand2",
                       takefocus=1)
        cv.pack(side="right")
        var = tk.StringVar(win)
        values = [str(v) for v, _ in options]
        hover_at = [None]
        seg_w = control_w / len(options)

        def draw(*a):
            cv.delete("all")
            focused = cv.focus_get() is cv
            rounded(cv, 0, 0, control_w, control_h, f(4), well, card, fg if focused else ctl, 1)
            for i, (value, text) in enumerate(options):
                x0, x1 = round(i * seg_w), round((i + 1) * seg_w)
                chosen = str(value) == var.get()
                if chosen or hover_at[0] == i:
                    rounded(cv, x0 + 3, 3, x1 - 3, control_h - 3, f(3), accent if chosen else hot, well)
                cv.create_text((x0 + x1) / 2, control_h / 2, text=text, font=body,
                               fill="#0B0D0F" if chosen else (fg if hover_at[0] == i else dim))
                located[f"{name}:{value}"] = (cv, x0, 0, x1 - x0, control_h)

        def choose(i):
            i = max(0, min(len(values) - 1, i))
            if values[i] != var.get():
                var.set(values[i])
                change(name, typed(name, values[i]))

        def hover_to(i):
            if hover_at[0] != i:
                hover_at[0] = i
                draw()

        cv.bind("<Button-1>", lambda e: choose(int(e.x // seg_w)))
        cv.bind("<Motion>", lambda e: hover_to(min(len(values) - 1, int(e.x // seg_w))))
        cv.bind("<Leave>", lambda e: hover_to(None))
        cv.bind("<Left>", lambda e: choose(values.index(var.get()) - 1) if var.get() in values else None)
        cv.bind("<Right>", lambda e: choose(values.index(var.get()) + 1) if var.get() in values else None)
        cv.bind("<FocusIn>", draw)
        cv.bind("<FocusOut>", draw)
        var.trace_add("write", draw)
        refreshers.append(lambda: var.set(str(getattr(C, name))))

    def switch(parent_, name, on, off, card_cv):
        """Fluent's toggle switch with "On"/"Off" beside it; the whole card is clickable, Space toggles it when
        it has the keyboard focus."""
        w_, h_ = f(40), f(20)
        var = clean if name == "CLEAN_MODE" else tk.BooleanVar(win)
        cv = tk.Canvas(parent_, width=w_, height=h_, bg=card, highlightthickness=0, cursor="hand2", takefocus=1)
        cv.pack(side="right")
        word = tk.Label(parent_, bg=card, fg=fg, font=body)
        word.pack(side="right", padx=(0, f(12)))

        def draw(*a):
            cv.delete("all")
            focused = cv.focus_get() is cv
            r_ = h_ // 2
            if var.get():
                rounded(cv, 0, 0, w_, h_, r_, accent, card)
                k = f(7)
                rounded(cv, w_ - r_ - k, r_ - k, w_ - r_ + k, r_ + k, k, "#0B0D0F", accent)
            else:
                rounded(cv, 0, 0, w_, h_, r_, card, card, fg if focused else "#8A939D", 1)
                k = f(6)
                rounded(cv, r_ - k, r_ - k, r_ + k, r_ + k, k, "#B5BDC6", card)
            word.config(text="On" if var.get() else "Off")

        def toggle(e=None):
            var.set(not var.get())
            change(name, on if var.get() else off)
            return "break"

        var.trace_add("write", draw)
        for w in (cv, word):
            w.bind("<Button-1>", toggle)
            w.config(cursor="hand2")
        cv.bind("<space>", toggle)
        cv.bind("<FocusIn>", draw)
        cv.bind("<FocusOut>", draw)
        refreshers.append(lambda: var.set(getattr(C, name) != off))
        located[name] = card_cv
        return toggle

    for item, opts in items:
        kind = item[0]
        if kind == "column":
            columns.append(tk.Frame(body_frame, bg=bg))
            columns[-1].grid(row=0, column=len(columns) - 1, sticky="nsew", padx=(f(24), 0))
            body_frame.grid_columnconfigure(len(columns) - 1, weight=1, uniform="col")
        elif kind == "section":
            section(item[1])
        elif kind == "scale":
            _, name, label, low, high, step, unit = item
            _cv, right = card_row(label, opts)
            slider(right, name, low, high, step, unit)
        elif kind == "choice":
            _, name, label, options = item
            _cv, right = card_row(label, opts)
            segments(right, name, options)
        elif kind == "check":
            name, label = item[1], item[2]
            on, off = (item[3], item[4]) if len(item) > 3 else (True, False)
            flip = []
            card_cv, right = card_row(label, opts, click=lambda e, fl=flip: fl[0]())
            flip.append(switch(right, name, on, off, card_cv))

    def refresh():
        ready[0] = False
        for fn in refreshers:
            fn()
        win.after(250, lambda: ready.__setitem__(0, True))

    def reset():
        names = [item[1] for item, _ in items if item[0] not in ("section", "column")]
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

    foot = tk.Frame(win, bg=bg)
    foot.pack(fill="x", pady=(f(24), 0))
    status = tk.Label(foot, text="Changes are saved as you make them.", bg=bg, fg=dim, font=caption)

    def button(key, text, command, primary=False):
        """Fluent's button: 32 px high, 4 px corners, at least 120 px wide; the accent one for the main action."""
        bw, bh = max(f(120), (measure_strong if primary else measure)(text) + 2 * f(12)), control_h
        cv = tk.Canvas(foot, width=bw, height=bh, bg=bg, highlightthickness=0, cursor="hand2")
        cv.pack(side="right", padx=(f(8), 0))

        def draw(hot_=False):
            cv.delete("all")
            if primary:
                rounded(cv, 0, 0, bw, bh, f(4), accent_hot if hot_ else accent, bg)
            else:
                rounded(cv, 0, 0, bw, bh, f(4), hot if hot_ else card, bg, ctl, 1)
            cv.create_text(bw / 2, bh / 2, text=text, font=strong if primary else body,
                           fill="#0B0D0F" if primary else fg)

        def release(e):
            if 0 <= e.x < bw and 0 <= e.y < bh:
                command()
        draw()
        cv.bind("<Enter>", lambda e: draw(True))
        cv.bind("<Leave>", lambda e: draw(False))
        cv.bind("<ButtonRelease-1>", release)
        located[key] = cv

    button("done", "Done", close, primary=True)
    button("reset", "Reset to defaults", reset)
    button("file", "Open the settings file", lambda: os.startfile(config_path)
           if os.environ.get("TESSERAWALL_NO_DIALOGS") != "1" else log.info("would open %s", config_path))
    status.pack(side="left")

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

    def rect(v):             # a widget, or (canvas, x, y, w, h) for part of one (a segment)
        if isinstance(v, tuple):
            cv, x, y, w_, h_ = v
            return cv.winfo_rootx() + x, cv.winfo_rooty() + y, w_, h_
        return v.winfo_rootx(), v.winfo_rooty(), v.winfo_width(), v.winfo_height()
    log.info("settings shown: %s", " ".join("%s=%d,%d,%d,%d" % ((k,) + rect(v)) for k, v in located.items()))
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
    chosen_plan: list = []                                # a templates.Plan, when a template was chosen
    u = max(12, root.winfo_screenheight() // 90)          # layout unit in pixels; 16 on a 1440p screen
    bg, panel, fg, dim, accent = "#101214", "#1B1F24", "#E6E6E6", "#9AA4AF", "#6CB6FF"
    w, h = u * 42, u * 31
    mx, my, mw, mh = monitor_under_cursor()
    root.geometry(f"{w}x{h}+{mx + (mw - w) // 2}+{my + (mh - h) // 2}")
    root.configure(bg=bg, padx=u * 2, pady=u * 2)
    root.resizable(False, False)

    def font(scale=1.0, bold=False):
        return ("Segoe UI", -int(u * scale), "bold") if bold else ("Segoe UI", -int(u * scale))

    poll: list = []                                       # pending check_handed_over, cancelled on close
    jobs: list = []                                       # other pending after() calls, cancelled on close

    def close():
        for job in poll + jobs:
            try:
                root.after_cancel(job)
            except tk.TclError:
                pass
        root.destroy()

    def finish(items):
        if items:
            chosen.extend(items)
            close()
        else:
            status.config(text="No videos found in that selection.")

    def search():
        from tkinter import simpledialog
        words = simpledialog.askstring("Search YouTube", "Search YouTube for:", parent=root)
        if words and words.strip():
            import sources
            finish([sources.search_url(words)])

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

    def pick_template():
        # Tests answer the (native, undrivable) folder dialog through this variable.
        folder = (os.environ.get("TESSERAWALL_TEST_PICK_FOLDER")
                  or filedialog.askdirectory(parent=root, title="Choose a folder of videos for the template"))
        if folder:
            plan = template_window([folder], config_path, parent=root)
            if plan and plan.items:
                chosen_plan.append(plan)
                close()

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
                         ("Drop videos, folders or links here" if can_drop else "Choose what to play"))
        drop.create_text(w_ // 2, h_ // 2 + u, fill=dim, font=font(0.9),
                         text="" if lit else "or click to choose videos  ·  Ctrl+V pastes a link")

    drop.bind("<Configure>", lambda e: draw_zone())
    drop.bind("<Enter>", lambda e: draw_zone("hover") if zone["state"] != "drag" else None)
    drop.bind("<Leave>", lambda e: draw_zone("idle") if zone["state"] != "drag" else None)
    drop.bind("<Button-1>", lambda e: pick_files())
    buttons = {}

    def button(key, text, command, primary=False, parent=None, column=None):
        b = tk.Button(parent or root, text=text, command=command, font=font(1.05 if parent is None else 0.95, primary),
                      relief="flat", bd=0, bg=accent if primary else panel, fg="#0B0D0F" if primary else fg,
                      cursor="hand2", activebackground="#8FC8FF" if primary else "#2A3038",
                      activeforeground="#0B0D0F" if primary else fg, pady=u // 2)
        if column is None:
            b.pack(fill="x", pady=(0, u // 2))
        else:
            b.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else u // 4, 0))
        hover(b, accent if primary else panel, "#8FC8FF" if primary else "#2A3038")
        buttons[key] = b
        return b

    button("videos", "Choose videos…", pick_files, primary=True).focus_set()
    trio = tk.Frame(root, bg=bg)             # the other ways in, side by side
    trio.pack(fill="x", pady=(0, u // 2))
    for col, (key, text, command) in enumerate((("folder", "Choose a folder…", pick_folder),
                                                ("search", "Search YouTube…", search),
                                                ("template", "Use a template…", pick_template))):
        trio.grid_columnconfigure(col, weight=1, uniform="trio")
        button(key, text, command, parent=trio, column=col)
    if last_session:
        n = len(last_session)
        button("resume", f"Reopen last session ({n} video{'s' if n != 1 else ''})", lambda: finish(last_session))
    for i, (name, items) in enumerate(list_walls(config_path) if config_path else []):
        n = len(items)
        button(f"wall{i}", f"Open \u201c{name}\u201d ({n} video{'s' if n != 1 else ''})",
               lambda items=items: finish(items))

    # Templates whose every slot names a folder need no folder chosen: one click opens them.
    import templates as T
    ready = [t for t in (T.load_templates(T.user_file_path(config_path))[0] if config_path else [])
             if not t.needs_chosen_folder][:4]

    def play_ready(t):
        import threading
        import probe
        state: dict = {}
        status.config(text=f"Reading video shapes for \u201c{t.name}\u201d\u2026")
        for b in buttons.values():
            if isinstance(b, tk.Button):
                b.config(state="disabled")

        def work():
            read = lambda files: probe.probe(files, probe.cache_path_for(config_path))
            _, per = T.gather([t], [], expand, read)
            state["plan"] = T.plan(t, {}, folder_shapes=per[t.name])

        def wait():
            plan = state.get("plan")
            if plan is None:
                jobs.append(root.after(100, wait))
                return
            log.info("start window template %s: %d videos, rows=%s%s", t.name, len(plan.items), plan.rows,
                     f" ({plan.message})" if plan.message else "")
            if plan.items:
                chosen_plan.append(plan)
                close()
            else:
                status.config(text=plan.message[:90])
                for b in buttons.values():
                    if isinstance(b, tk.Button):
                        b.config(state="normal")
        threading.Thread(target=work, daemon=True).start()
        jobs.append(root.after(100, wait))

    for i, t in enumerate(ready):
        button(f"tpl{i}", f"Play template \u201c{t.name}\u201d ({t.count} videos)", lambda t=t: play_ready(t))

    # Recent and pinned channels, playlists and searches: one click opens a wall of them. The star pins one
    # (pinned ones stay listed, first); the cross forgets one.
    import sources
    remembered = sources.recent(config_path, limit=sources.RECENT_SHOWN) if config_path else []
    heading = tk.Label(root, text="CHANNELS AND SEARCHES", bg=bg, fg=dim, font=font(0.8, True))
    heading_h = 0
    if remembered:
        heading.pack(anchor="w", pady=(u // 2, u // 3), padx=(u // 8, 0))
        heading_h = heading.winfo_reqheight() + u // 2 + u // 3

    def recent_row(i, d):
        row = tk.Frame(root, bg=panel)
        row.pack(fill="x", pady=(0, u // 4))
        src = d["source"]
        b = tk.Button(row, text=sources.menu_name(src, d.get("title")), anchor="w",
                      command=lambda: (log.info("start window recent picked: %s", src), finish([src])),
                      font=font(0.95), relief="flat", bd=0, bg=panel, fg=fg, cursor="hand2", padx=u // 2,
                      activebackground="#2A3038", activeforeground=fg, pady=u // 4)
        b.pack(side="left", fill="x", expand=True)
        hover(b, panel, "#2A3038")
        pin = tk.Label(row, bg=panel, font=font(1.05), cursor="hand2", padx=u // 2)
        cross = tk.Label(row, text="\u2715", bg=panel, fg=dim, font=font(0.8), cursor="hand2", padx=u // 2)
        cross.pack(side="right", fill="y")
        pin.pack(side="right", fill="y")
        state = {"pinned": bool(d.get("pinned"))}

        def draw_pin():
            pin.config(text="\u2605" if state["pinned"] else "\u2606", fg=accent if state["pinned"] else dim)

        def toggle(e=None):
            state["pinned"] = not state["pinned"]
            sources.set_pinned(config_path, src, state["pinned"])
            log.info("start window %s %s", "pinned" if state["pinned"] else "unpinned", src)
            draw_pin()

        def drop_row(e=None):
            sources.forget(config_path, src)
            log.info("start window forgot %s", src)
            # Shrink the window by the row, so the drop zone (which takes spare room) doesn't grow and
            # push the rest of the list down under the pointer. The heading goes with the last row.
            gone = row.winfo_height() + u // 4
            row.destroy()
            for key in (f"recent{i}", f"pin{i}", f"forget{i}"):
                buttons.pop(key, None)                 # gone: nothing may configure them any more
            if not any(k.startswith("recent") for k in buttons):
                heading.destroy()
                gone += heading_h
            root.geometry(f"{w}x{max(1, root.winfo_height() - gone)}")

        draw_pin()
        pin.bind("<ButtonRelease-1>", toggle)              # on release, like a button
        cross.bind("<ButtonRelease-1>", drop_row)
        for lbl in (pin, cross):
            hover(lbl, panel, "#2A3038")
        buttons[f"recent{i}"], buttons[f"pin{i}"], buttons[f"forget{i}"] = b, pin, cross

    for i, d in enumerate(remembered):
        recent_row(i, d)
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
            from tkinterdnd2 import DND_FILES, DND_TEXT
            root.drop_target_register(DND_FILES, DND_TEXT)
            def drag_enter(e):
                draw_zone("drag")
                log.info("drop zone highlighted")
                return e.action

            def drag_leave(e):
                draw_zone("idle")
                return e.action

            def dropped(e):
                draw_zone("idle")
                finish(dropped_items(root, e.data))
                return e.action

            root.dnd_bind("<<DropEnter>>", drag_enter)
            root.dnd_bind("<<DropLeave>>", drag_leave)
            root.dnd_bind("<<Drop>>", dropped)
        except Exception as e:
            log.info("start window: drag-and-drop unavailable: %s", e)
    root.bind("<Escape>", lambda e: close())
    root.protocol("WM_DELETE_WINDOW", close)

    def paste(e=None):
        def done(items):
            try:
                if not root.winfo_exists():
                    return
            except tk.TclError:
                return
            log.info("start window paste: %s item(s)", "no answer" if items is None else len(items))
            if items:
                finish(items)
            elif items is None:
                status.config(text="The app that copied this is not answering. Try copying it again.")
            else:
                status.config(text="Nothing to play on the clipboard: copy a video link or file first.")
        paste_async(root, done)
    for seq in ("<Control-v>", "<Control-V>"):
        root.bind(seq, paste)

    update_link: list = []

    def fit():
        """Grow (never shrink) the window to its contents: saved walls, the template button and the
        update link can add up to more than the design height. The drop zone takes any spare room."""
        root.update_idletasks()
        root.geometry(f"{w}x{max(h, root.winfo_reqheight())}")

    def show_update(found):
        link = tk.Label(root, text=f"Tessera Wall {found['version']} is available – click here to download it",
                        bg=bg, fg=accent, font=font(0.95, True), cursor="hand2")
        link.pack(anchor="w", pady=(u // 3, 0))
        link.bind("<Button-1>", lambda e: updates.open_release_page(found["url"]))
        root.update_idletasks()
        root.geometry(f"{w}x{root.winfo_height() + u * 2}")  # make room without moving the window
        update_link.append(link)

        def report():   # once Windows has resized the window; before that the link reads 0,0 1x1
            try:
                if link.winfo_width() <= 1:
                    root.after(30, report)
                    return
            except tk.TclError:          # the window was closed meanwhile
                return
            log.info("start window update at %d,%d %dx%d: %s", link.winfo_rootx(), link.winfo_rooty(),
                     link.winfo_width(), link.winfo_height(), found["version"])
        report()

    def check_handed_over():  # files sent by a later launch (right-click, Send To) open the wall
        items = []
        while not incoming.empty():
            items.append(incoming.get_nowait())
        if items:
            finish(items)
            return
        if checker and checker.result and not update_link:
            show_update(checker.result)
        poll[:] = [root.after(150, check_handed_over)]
    poll[:] = [root.after(150, check_handed_over)]
    fit()
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
    return chosen_plan[0] if chosen_plan else (chosen or None)


def template_window(folders: list, config_path: str | None, parent=None) -> "templates.Plan | None":
    """Choose a template for the videos in `folders`. Reads the videos' shapes first (cached after the
    first time, with progress shown), then lists Best fit and every template with how many matching
    videos it would get. Returns the Plan chosen, or None if the window was closed.

    With `parent` (the start window) it is a modal window over it; without (Explorer's "Play as
    template" and --choose-template) it is its own window."""
    import random
    import threading
    import probe
    import templates as T

    own = parent is None
    win = make_root() if own else tk.Toplevel(parent)
    win.withdraw()
    if not own:
        # Before it is shown: on Windows, making an already visible window transient leaves it unmapped,
        # so a modal window opened that way never appeared and its grab froze the start window.
        win.transient(parent)
    win.title("Tessera Wall – templates")
    u = max(12, win.winfo_screenheight() // 90)
    bg, panel, fg, dim, accent = "#101214", "#1B1F24", "#E6E6E6", "#9AA4AF", "#6CB6FF"
    win.configure(bg=bg, padx=u * 2, pady=u * 2)
    win.resizable(False, False)
    font = lambda s=1.0, b=False: ("Segoe UI", -int(u * s), "bold") if b else ("Segoe UI", -int(u * s))
    result: list = []
    mx, my, mw, mh = monitor_under_cursor()
    tpl_path = T.user_file_path(config_path) if config_path else None
    all_templates, problems, options = T.load_templates(tpl_path)
    for p in problems:
        log.warning("templates: %s", p)
    paths = expand(list(folders))
    name = ", ".join(os.path.basename(os.path.normpath(f)) or f for f in folders)
    state = {"done": 0, "total": len(paths), "shapes": None, "per_template": {}}

    tk.Label(win, text="Choose a template", bg=bg, fg="white", font=font(1.6, True)).pack(anchor="w")
    tk.Label(win, text=f"{name} – {len(paths)} video{'s' if len(paths) != 1 else ''}", bg=bg, fg=dim,
             font=font(1.0), wraplength=u * 36, justify="left").pack(anchor="w", pady=(0, u))
    status = tk.Label(win, text="Reading video shapes…", bg=bg, fg=dim, font=font(0.95))
    status.pack(anchor="w", pady=(0, u // 2))
    body = tk.Frame(win, bg=bg)
    body.pack(fill="x")
    located: dict = {}

    jobs: list = []                                     # pending after() calls, cancelled on close

    def later(ms, fn):
        jobs.append(win.after(ms, fn))

    def close(plan=None):
        if plan:
            result.append(plan)
        for job in jobs:
            try:
                win.after_cancel(job)
            except tk.TclError:
                pass
        if own:
            win.destroy()
        else:
            win.grab_release()
            win.destroy()

    def choose(make):
        plan = make()
        log.info("template chosen: %s, %d videos, rows=%s%s", plan.name, len(plan.items), plan.rows,
                 f" ({plan.message})" if plan.message else "")
        close(plan)

    def entry(key, title, detail, command, enabled=True, primary=False):
        normal = accent if primary else panel
        b = tk.Button(body, text=f"{title}   ·   {detail}" if detail else title, font=font(1.0, primary),
                      relief="flat", bd=0, anchor="w", padx=u, pady=u // 2, cursor="hand2" if enabled else "",
                      bg=normal, fg="#0B0D0F" if primary else (fg if enabled else "#5C6670"),
                      activebackground="#8FC8FF" if primary else "#2A3038",
                      activeforeground="#0B0D0F" if primary else fg,
                      state="normal" if enabled else "disabled", disabledforeground="#5C6670", command=command)
        b.pack(fill="x", pady=(0, u // 3))
        if enabled:
            hover(b, normal, "#8FC8FF" if primary else "#2A3038")
        located[key] = b

    def build():
        shapes = state["shapes"]
        known = sum(1 for a in shapes.values() if a)
        status.config(text=f"{known} of {len(shapes)} files are playable videos." if known else
                      "No playable videos in that folder.")
        if not known:
            return
        most = options.get("best_fit_max", T.BEST_FIT_MAX)
        preview = T.best_fit(shapes, mw, mh, C.MAX_CROP, C.BALANCE_WEIGHT, most, random.Random(0))
        entry("best", preview.name, "", lambda: choose(lambda: T.best_fit(
            shapes, mw, mh, C.MAX_CROP, C.BALANCE_WEIGHT, most)), primary=True)
        for i, t in enumerate(all_templates):
            own = state["per_template"].get(t.name, {})
            got, need = T.availability(t, shapes, own)
            detail = (f"{need} videos" if got == need else f"only {got} of {need} here" if got else
                      "no matching videos here")
            entry(f"t{i}", t.name, detail, lambda t=t, own=own: choose(lambda: T.plan(t, shapes, folder_shapes=own)),
                  enabled=got > 0)
        win.update_idletasks()
        place()
        win.update()
        for key, b in located.items():           # positions, so tests can click the real buttons
            log.info("template window %s at %d,%d %dx%d", key, b.winfo_rootx(), b.winfo_rooty(),
                     b.winfo_width(), b.winfo_height())

    def work():
        # One read for the chosen folder and every folder a template's slots name.
        read = lambda files: probe.probe(files, probe.cache_path_for(config_path or C.user_config_path()),
                                         progress=lambda d, n: state.update(done=d, total=n))
        shapes, per_template = T.gather(all_templates, list(folders), expand, read)
        state["per_template"] = per_template
        state["shapes"] = shapes

    def poll():
        if state["shapes"] is not None:
            build()
            return
        status.config(text=f"Reading video shapes… {state['done']} of {state['total']}")
        later(100, poll)

    def place():
        win.update_idletasks()
        w, h = max(win.winfo_reqwidth(), u * 40), win.winfo_reqheight()
        win.geometry(f"{w}x{h}+{mx + (mw - w) // 2}+{my + (mh - h) // 2}")

    win.bind("<Escape>", lambda e: close())
    win.protocol("WM_DELETE_WINDOW", close)
    threading.Thread(target=work, daemon=True).start()
    place()
    win.attributes("-alpha", 0.0)
    win.deiconify()
    log.info("template window dark title bar: %s", dark_title_bar(win))
    win.attributes("-topmost", True)
    win.lift()
    win.focus_force()
    fade(win, 1.0, 120)
    later(400, lambda: win.attributes("-topmost", False))
    later(100, poll)
    if own:
        win.mainloop()
    else:
        win.update()                    # visible first: a grab on a window that is not shown freezes input
        log.info("template window shown: %s", bool(win.winfo_ismapped()))
        win.grab_set()
        parent.wait_window(win)
    return result[0] if result else None
