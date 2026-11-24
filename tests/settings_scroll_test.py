"""Settings on a screen too short for it, opened from the start window. TAKES OVER MOUSE, KEYBOARD AND
SCREEN for ~15 seconds. Run: python tests/settings_scroll_test.py
Saves %TEMP%\\tesserawall-settings-scroll.png for a visual check.

TESSERAWALL_TEST_SCREEN_H=700 makes the Settings window size itself as if the screen were 700 px tall.
Starts with nothing to play (the start window), clicks its Settings button, and checks: the cards scroll
(log), the whole window including its Done button fits in those 700 px, the mouse wheel over the cards
scrolls them, Done closes the window, and Esc then closes the start window. This is also the only test
that opens Settings from the start window.
"""
import ctypes
import os
import re
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes

ctypes.windll.shcore.SetProcessDpiAwareness(2)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(tempfile.gettempdir(), "tesserawall.log")
APP = os.path.join(ROOT, "tesserawall.pyw")
SHORT = 700
user32 = ctypes.windll.user32
KEYUP = 0x0002


def log_text():
    try:
        return open(LOG, encoding="utf-8", errors="replace").read()
    except FileNotFoundError:
        return ""


def wait_for(pattern, seconds=8.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        m = re.search(pattern, log_text())
        if m:
            return m
        time.sleep(0.1)
    return None


def click_at(x, y):
    user32.SetCursorPos(x - 1, y)
    time.sleep(0.05)
    user32.SetCursorPos(x, y)
    time.sleep(0.25)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.15)


def rects(prefix):
    line = re.findall(prefix + r": (.*)", log_text())[-1]
    return {k: tuple(map(int, v.split(","))) for k, v in (item.rsplit("=", 1) for item in line.split())}


def main():
    print("WARNING: this test controls mouse, keyboard and screen for ~15 seconds.")
    folder = tempfile.mkdtemp(prefix="tesserawall-scroll-")
    cfg, session = os.path.join(folder, "config.toml"), os.path.join(folder, "none.json")
    orig = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(orig))
    r = {}
    code = None
    app = subprocess.Popen([sys.executable, APP, "--config", cfg, "--session", session],
                           env=dict(os.environ, TESSERAWALL_NO_DIALOGS="1", TESSERAWALL_TEST_SCREEN_H=str(SHORT)))
    try:
        m = wait_for(r"start window settings at (-?\d+),(-?\d+) (\d+)x(\d+)")
        assert m, "the start window did not show its Settings button"
        x, y, w, h = map(int, m.groups())
        time.sleep(1.0)                                    # the window fades in after logging its buttons
        click_at(x + w // 2, y + h // 2)
        r["opened"] = bool(wait_for(r"settings shown"))
        time.sleep(0.6)
        s = wait_for(r"settings scroll: on, view (\d+) of (\d+) px")
        r["scrolls"] = bool(s) and int(s.group(1)) < int(s.group(2))
        w_ = rects("settings shown")
        top = min(v[1] for v in w_.values() if v[3] > 1)
        done = w_["done"]
        area = re.search(r"settings screen area (-?\d+),(-?\d+) (\d+)x(\d+)", log_text())
        r["area_h"] = int(area.group(4)) if area else None
        r["fits"] = bool(area) and r["area_h"] == SHORT and done[1] + done[3] <= int(area.group(2)) + SHORT
        shot = os.path.join(tempfile.gettempdir(), "tesserawall-settings-scroll.png")
        x0, y0 = min(v[0] for v in w_.values()) - 60, top - 160
        x1, y1 = max(v[0] + v[2] for v in w_.values()) + 60, done[1] + done[3] + 40
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"Add-Type -AssemblyName System.Drawing;$bmp=New-Object System.Drawing.Bitmap {x1 - x0},{y1 - y0};"
                        f"$g=[System.Drawing.Graphics]::FromImage($bmp);$g.CopyFromScreen({x0},{y0},0,0,$bmp.Size);"
                        f"$bmp.Save('{shot}')"], creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)

        cx, cy, cw, ch = w_["DIM_UNFOCUSED"]               # the wheel over a card scrolls the cards
        user32.SetCursorPos(cx - 40, cy + ch // 2); time.sleep(0.3)
        for _ in range(3):
            user32.mouse_event(0x0800, 0, 0, ctypes.c_ulong(-120 & 0xFFFFFFFF).value, 0)
            time.sleep(0.15)
        moved = wait_for(r"settings scrolled to (0\.\d+|1\.0+)")
        r["wheel_scrolls"] = bool(moved) and float(moved.group(1)) > 0

        click_at(done[0] + done[2] // 2, done[1] + done[3] // 2)
        r["done_closes"] = bool(wait_for(r"settings closed"))
        time.sleep(0.5)
        user32.keybd_event(0x1B, 0, 0, 0); user32.keybd_event(0x1B, 0, KEYUP, 0)   # Esc closes the start window
        code = app.wait(15)
    finally:
        user32.SetCursorPos(orig.x, orig.y)
        if app.poll() is None:
            app.kill()
    for k, v in r.items():
        print(f"{k:16} {v}")
    log = log_text()
    assert code == 0 and "callback error" not in log and "settings window:" not in log and "Traceback" not in log
    assert r["opened"], "the start window's Settings button should open Settings"
    assert r["scrolls"], "on a short screen the cards should scroll"
    assert r["fits"], "the window, Done button included, should fit the short screen"
    assert r["wheel_scrolls"], "the mouse wheel over the cards should scroll them"
    assert r["done_closes"]
    print("SETTINGS SCROLL TEST PASSED")


if __name__ == "__main__":
    main()
