"""Magnify (zoom and pan inside a video). TAKES OVER MOUSE, KEYBOARD AND SCREEN for ~25 s.
Run: python tests/magnify_test.py

One video filling the screen, split into four coloured quarters (red, green / blue, yellow), so where the
picture went can be read straight off screen pixels:
  A. before: red at the top-left quarter's centre, green at the top-right's
  B. Ctrl+wheel up 4 notches (2x) with the pointer on the red quarter's centre: that spot stays red (the
     point under the pointer stays put), and the screen centre turns red too (the picture grew away from
     the pointer; with the pan the wrong way round it would show green)
  C. drag left by half the screen width: the picture follows the pointer, so the centre shows the green
     quarter; the drag is not taken as a click (no pin change)
  D. Z: normal size again, red and green back in their quarters
"""
import ctypes
import os
import re
import subprocess
import sys
import tempfile
import time

ctypes.windll.shcore.SetProcessDpiAwareness(2)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(tempfile.gettempdir(), "tesserawall.log")
APP = os.path.join(ROOT, "tesserawall.pyw")
QUAD = ("av://lavfi:color=c=red:s=640x360[a];color=c=0x00FF00:s=640x360[b];color=c=blue:s=640x360[c];"
        "color=c=yellow:s=640x360[d];[a][b]hstack[t];[c][d]hstack[u];[t][u]vstack")
user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
KEYUP, VK_CTRL, VK_Z, WHEEL = 0x0002, 0x11, 0x5A, 0x0800


def log_text():
    try:
        return open(LOG, encoding="utf-8", errors="replace").read()
    except FileNotFoundError:
        return ""


def colour(x, y):
    """'red', 'green', 'blue', 'yellow' or 'other(r, g, b)' for the screen pixel at x, y."""
    dc = user32.GetDC(0)
    c = gdi32.GetPixel(dc, x, y)
    user32.ReleaseDC(0, dc)
    r, g, b = c & 0xFF, c >> 8 & 0xFF, c >> 16 & 0xFF
    hi = lambda v: v > 150
    lo = lambda v: v < 90
    if hi(r) and lo(g) and lo(b):
        return "red"
    if lo(r) and hi(g) and lo(b):
        return "green"
    if lo(r) and lo(g) and hi(b):
        return "blue"
    if hi(r) and hi(g) and lo(b):
        return "yellow"
    return f"other{(r, g, b)}"


def move(x, y, pause=0.3):
    user32.SetCursorPos(x, y)
    time.sleep(pause)


def main():
    print("WARNING: this test controls mouse, keyboard and screen for ~25 seconds.")
    tmp = tempfile.mkdtemp(prefix="tesserawall-magnify-")
    with open(os.path.join(tmp, "config.toml"), "w") as f:
        f.write("show_help_hint = false\n")
    if os.path.exists(LOG):
        os.remove(LOG)
    p = subprocess.Popen([sys.executable, APP, "--exit-after", "22", "--config", os.path.join(tmp, "config.toml"),
                          "--session", os.path.join(tmp, "s.json"), QUAD])
    r = {}
    try:
        end = time.monotonic() + 10
        found = []
        while time.monotonic() < end and not (found and "aspect known" in log_text()):
            found = re.findall(r"rects=(\[.*?\]) panscan", log_text())
            time.sleep(0.2)
        time.sleep(1.5)
        x, y, w, h = eval(found[-1])[0]
        tl, tr, mid = (x + w // 4, y + h // 4), (x + 3 * w // 4, y + h // 4), (x + w // 2, y + h // 2)

        # A
        move(*mid)
        user32.mouse_event(0x0002, 0, 0, 0, 0)          # click: keyboard focus (and a pin, harmless here)
        user32.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(0.6)
        r["A_tl"], r["A_tr"] = colour(*tl), colour(*tr)

        # B
        move(*tl)
        user32.keybd_event(VK_CTRL, 0, 0, 0)
        for _ in range(4):
            user32.mouse_event(WHEEL, 0, 0, 120, 0)
            time.sleep(0.15)
        user32.keybd_event(VK_CTRL, 0, KEYUP, 0)
        time.sleep(1.2)
        r["B_logged"] = bool(re.search(r"magnify 1\.00 \(x2\.00\) pan=\(0\.12\d+, 0\.12\d+\)", log_text()))
        r["B_pointer"], r["B_centre"] = colour(*tl), colour(*mid)

        # C
        pins_before = log_text().count("pin -> ")
        move(*mid)
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        for i in range(1, 21):                          # drag left by half the width, in steps
            move(mid[0] - (w // 2) * i // 20, mid[1], 0.02)
        user32.mouse_event(0x0004, 0, 0, 0, 0)
        move(*tr, 0.8)
        r["C_centre"] = colour(*mid)
        r["C_no_click"] = log_text().count("pin -> ") == pins_before

        # D
        user32.keybd_event(VK_Z, 0, 0, 0)
        user32.keybd_event(VK_Z, 0, KEYUP, 0)
        time.sleep(1.0)
        r["D_logged"] = "magnify off" in log_text()
        r["D_tl"], r["D_tr"] = colour(*tl), colour(*tr)
        r["exit"] = p.wait(30)
    finally:
        if p.poll() is None:
            p.kill()
    for k, v in r.items():
        print(f"{k:12} {v}")
    assert r["A_tl"] == "red" and r["A_tr"] == "green", "quadrants where expected before magnifying"
    assert r["B_logged"], "4 notches = 2x, pan +1/8 towards the pointer's quarter"
    assert r["B_pointer"] == "red", "the spot under the pointer stays put"
    assert r["B_centre"] == "red", "the picture grows away from the pointer (pan direction)"
    assert r["C_centre"] == "green", "dragging left moves the picture left"
    assert r["C_no_click"], "a look-around drag is not a click"
    assert r["D_logged"] and r["D_tl"] == "red" and r["D_tr"] == "green", "Z: back to normal size"
    assert r["exit"] == 0
    print("MAGNIFY TEST PASSED")


if __name__ == "__main__":
    main()
