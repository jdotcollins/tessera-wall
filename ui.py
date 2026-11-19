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
from winapi import GA_ROOT, monitor_under_cursor, user32

log = logging.getLogger("tesserawall")


def dropped_items(root, data: str) -> list:
    """What a drop carries: files and folders from Explorer, or a link (text) dragged from a browser."""
    if "://" in data:
        return [i for item in links_from_text(data) for i in ([item] if "://" in item else expand([item]))]
    return expand(list(root.tk.splitlist(data)))


def pasted_items(root) -> list:
    """Links and paths on the clipboard (Ctrl+V): one per line or separated by spaces."""
    try:
        text = root.clipboard_get()
    except tk.TclError:
        return []
    return [i for item in links_from_text(text) for i in ([item] if "://" in item else expand([item]))]


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
    ("choice", "WHEN_FINISHED", "When a video ends", [("loop", "Play it again"), ("next", "Next in folder"),
                                                      ("random", "Random from folder")]),
    ("choice", "SWITCH_SECONDS", "Move on", [(0, "Never"), (30, "After 30 s"), (60, "After 1 min"),
                                             (120, "After 2 min")]),
    ("check", "RANDOM_START", "Start videos at a random point"),
    ("section", "Other"),
    ("check", "FOCUS_OUTLINE", "Outline the video you point at"),
    ("check", "SEEKBAR_SHOW_NAME", "Show the file name on the video you point at"),
    ("check", "CURSOR_HIDE_SECONDS", "Hide the pointer when it is not moving", 2.0, 0.0),
    ("check", "UNFOCUSED_SKIP", "Save power: lower frame rate on the other videos (they look choppy)", "nonref", ""),
    ("check", "UPDATE_CHECK", "Check for a new version once a week"),
    ("check", "SAFE_RENDERING", "Compatibility mode (recommended; applies to videos opened next)"),
]


def typed(name: str, value: str):
    """A choice's value comes back from tk as text; give whole-number settings their number back."""
    default = C.DEFAULTS[name]
    if isinstance(default, int) and not isinstance(default, bool):
        return int(value)
    return value


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
                                   command=lambda n=name, v=var: change(n, typed(n, v.get())))
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
    button("template", "Use a template…", pick_template)
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
        items = pasted_items(root)
        log.info("start window paste: %d item(s)", len(items))
        if items:
            finish(items)
        else:
            status.config(text="Nothing to play on the clipboard: copy a video link or file first.")
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
