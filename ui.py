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
from files import expand
from winapi import GA_ROOT, monitor_under_cursor, user32

log = logging.getLogger("tesserawall")


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
