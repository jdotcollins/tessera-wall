"""Seek-bar label with file name. Takes over the mouse and screen for ~12 seconds.
Run: python tests/label_test.py     Saves %TEMP%\\tesserawall-label-wide.png and tesserawall-label-narrow.png.

Opens a short-named clip, a very long-named clip with ASS-special characters, and two more, then
screenshots the label on a wide focused tile and on a narrow one (where the name must be shortened).
The check is visual: open the two PNGs. The script itself only asserts nothing crashed.
"""
import ctypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes

ctypes.windll.shcore.SetProcessDpiAwareness(2)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(tempfile.gettempdir(), "tesserawall.log")
APP = os.path.join(ROOT, "tesserawall.pyw")
CLIP = os.path.join(tempfile.gettempdir(), "tesserawall-test-clip.y4m")   # made by features_test.py
user32 = ctypes.windll.user32


def shot(name, rect):
    x, y, w, h = rect
    path = os.path.join(tempfile.gettempdir(), name)
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Add-Type -AssemblyName System.Drawing;"
                    f"$bmp=New-Object System.Drawing.Bitmap {w},{h};"
                    f"$g=[System.Drawing.Graphics]::FromImage($bmp);$g.CopyFromScreen({x},{y},0,0,$bmp.Size);"
                    f"$bmp.Save('{path}')"], creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)
    return path


def rects():
    log = open(LOG, encoding="utf-8", errors="replace").read()
    return eval(re.findall(r"rects=(\[.*?\]) panscan", log)[-1])


def hover(r):
    x, y, w, h = r
    user32.SetCursorPos(x + w // 2 - 1, y + h // 2)
    time.sleep(0.05)
    user32.SetCursorPos(x + w // 2, y + h // 2)
    time.sleep(1.2)


def main():
    assert os.path.exists(CLIP), "run tests/features_test.py once first; it generates the clip"
    print("WARNING: this test controls the mouse and covers the screen for ~12 seconds.")
    folder = tempfile.mkdtemp(prefix="tesserawall-label-")
    names = ["short.y4m",
             "A very long file name {with braces} and ünïcöde that goes on and on and on, "
             "season 01 episode 02, director's cut, final FINAL version (1080p) [x264].y4m",
             "third.y4m", "fourth.y4m"]
    paths = []
    for n in names:
        paths.append(os.path.join(folder, n))
        shutil.copyfile(CLIP, paths[-1])
    orig = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(orig))
    wall = subprocess.Popen([sys.executable, APP, "--exit-after", "11"] + paths)
    try:
        time.sleep(3.5)
        hover(rects()[1])                                   # long name on a large focused tile
        r = rects()[1]
        shot("tesserawall-label-wide.png", (r[0], r[1] + r[3] - 70, min(r[2], 2400), 70))
        hover(rects()[0])                                   # short name
        r = rects()[0]
        shot("tesserawall-label-short.png", (r[0], r[1] + r[3] - 70, min(r[2], 1200), 70))
        code = wall.wait(30)
    finally:
        user32.SetCursorPos(orig.x, orig.y)
        if wall.poll() is None:
            wall.kill()
    log = open(LOG, encoding="utf-8", errors="replace").read()
    assert code == 0 and "callback error" not in log and "tile removed" not in log
    print("LABEL TEST RAN (check the screenshots)")


if __name__ == "__main__":
    main()
