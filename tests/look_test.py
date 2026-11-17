"""Gutters between videos, the focus outline, and the restyled on-screen text.
TAKES OVER THE MOUSE AND SCREEN for ~12 seconds. Run: python tests/look_test.py
Saves %TEMP%\\tesserawall-look.png (whole wall, with the launch hint) for a visual check.

Reads real screen pixels: the gutter between two unfocused videos must be black, the gutter around
the focused one must be the outline blue, and both must follow when focus moves.
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
L = "av://lavfi:testsrc2=s=1280x720:r=30"
BLUE = (0x6C, 0xB6, 0xFF)
user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
gdi32.GetPixel.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.GetPixel.restype = wintypes.DWORD


def pixel(x, y):
    dc = user32.GetDC(None)
    c = gdi32.GetPixel(dc, x, y)
    user32.ReleaseDC(None, dc)
    return (c & 255, (c >> 8) & 255, (c >> 16) & 255)


def near(a, b, tol=12):
    return all(abs(p - q) <= tol for p, q in zip(a, b))


def rects():
    log = open(LOG, encoding="utf-8", errors="replace").read()
    return eval(re.findall(r"rects=(\[.*?\]) panscan", log)[-1])


def hover(r):
    x, y, w, h = r
    user32.SetCursorPos(x + w // 2 - 1, y + h // 2)
    time.sleep(0.05)
    user32.SetCursorPos(x + w // 2, y + h // 2)
    time.sleep(0.9)


def edges(r):
    """A point inside the 2 px outline on each side of a tile's slot, away from the corners."""
    x, y, w, h = r
    return {"left": (x + 1, y + h // 2), "top": (x + w // 2, y + 1),
            "right": (x + w - 2, y + h // 2), "bottom": (x + w // 2, y + h - 2)}


def main():
    print("WARNING: this test controls the mouse and covers the screen for ~12 seconds.")
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    orig = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(orig))
    r = {}
    wall = subprocess.Popen([sys.executable, APP, "--exit-after", "11", L, L, L, L])
    try:
        time.sleep(2.0)
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"Add-Type -AssemblyName System.Drawing;$bmp=New-Object System.Drawing.Bitmap {sw},{sh};"
                        "$g=[System.Drawing.Graphics]::FromImage($bmp);$g.CopyFromScreen(0,0,0,0,$bmp.Size);"
                        f"$bmp.Save('{os.path.join(tempfile.gettempdir(), 'tesserawall-look.png')}')"],
                       creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)
        time.sleep(1.0)
        hover(rects()[0])
        rr = rects()
        r["focused_edges"] = {k: pixel(*p) for k, p in edges(rr[0]).items() if k in ("right", "bottom")}
        r["unfocused_edges"] = {k: pixel(*p) for k, p in edges(rr[3]).items() if k in ("left", "top")}
        hover(rects()[3])
        rr = rects()
        r["after_move_new"] = pixel(*edges(rr[3])["left"])
        r["after_move_old"] = pixel(*edges(rr[0])["right"])
        code = wall.wait(20)
    finally:
        user32.SetCursorPos(orig.x, orig.y)
        if wall.poll() is None:
            wall.kill()
    for k, v in r.items():
        print(f"{k:18} {v}")
    log = open(LOG, encoding="utf-8", errors="replace").read()
    assert code == 0 and "callback error" not in log
    assert all(near(c, BLUE) for c in r["focused_edges"].values()), "focused video should have a blue outline"
    assert all(near(c, (0, 0, 0)) for c in r["unfocused_edges"].values()), "gutters elsewhere should be black"
    assert near(r["after_move_new"], BLUE) and near(r["after_move_old"], (0, 0, 0)), "outline should follow focus"
    print("LOOK TEST PASSED")


if __name__ == "__main__":
    main()
