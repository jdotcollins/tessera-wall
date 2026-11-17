"""Layout cycling (l) and crop cycling (c). TAKES OVER MOUSE, KEYBOARD AND SCREEN for ~20 seconds.
Run: python tests/modes_test.py     Keys are only sent while the wall is the foreground window.
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
sys.path.insert(0, ROOT)
from ipc import MpvIpc

LOG = os.path.join(tempfile.gettempdir(), "tesserawall.log")
APP = os.path.join(ROOT, "tesserawall.pyw")
L, P = "av://lavfi:testsrc2=s=1280x720:r=30", "av://lavfi:testsrc2=s=720x1280:r=30"
user32 = ctypes.windll.user32
user32.GetForegroundWindow.restype = wintypes.HWND
LEFTDOWN, LEFTUP, KEYUP = 0x0002, 0x0004, 0x0002
VK_L, VK_C, VK_Q = 0x4C, 0x43, 0x51


def key(vk):
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, KEYUP, 0)
    time.sleep(0.7)


def log_text():
    return open(LOG, encoding="utf-8", errors="replace").read()


def rects():
    return eval(re.findall(r"rects=(\[.*?\]) panscan", log_text())[-1])


def main():
    print("WARNING: this test controls mouse, keyboard and screen for ~20 seconds.")
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    orig = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(orig))
    r = {}
    wall = subprocess.Popen([sys.executable, APP, "--exit-after", "30", L, P, L, L])
    try:
        time.sleep(3.5)
        ipc = [MpvIpc(rf"\\.\pipe\tesserawall-{wall.pid}-{n}", timeout=3) for n in (1, 2, 3, 4)]
        pans = lambda: [round(c.get("panscan"), 3) for c in ipc]
        r["balanced"] = rects()
        x, y, w, h = r["balanced"][0]
        user32.SetCursorPos(x + w // 2 - 1, y + h // 2)
        time.sleep(0.05)
        user32.SetCursorPos(x + w // 2, y + h // 2)
        time.sleep(0.7)
        user32.mouse_event(LEFTDOWN, 0, 0, 0, 0)          # click: pin tile 0, take keyboard focus
        user32.mouse_event(LEFTUP, 0, 0, 0, 0)
        time.sleep(0.5)
        out = wintypes.DWORD()
        user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(out))
        assert out.value == wall.pid, "wall is not the foreground window; not sending keys"
        r["grow"] = rects()
        r["pan_limited"] = pans()
        key(VK_L); r["flat"] = rects()
        r["mute_in_flat"] = [c.get("mute") for c in ipc]
        key(VK_L); r["grid"] = rects()
        key(VK_L); r["grow_again"] = rects()
        key(VK_C); r["pan_fill"] = pans()
        key(VK_C); r["pan_fit"] = pans()
        key(VK_C); r["pan_limited_again"] = pans()
        key(VK_Q)
        code = wall.wait(40)
    finally:
        user32.SetCursorPos(orig.x, orig.y)
        if wall.poll() is None:
            wall.kill()
    for k, v in r.items():
        print(f"{k:18} {v}")
    log = log_text()
    area = lambda q: q[2] * q[3]
    assert code == 0 and "callback error" not in log
    assert area(r["grow"][0]) > area(r["balanced"][0]) * 1.2, "grow: focused tile enlarges"
    assert r["flat"] == r["balanced"], "flat: focus must not move anything"
    assert r["mute_in_flat"] == [False, True, True, True], "flat: audio still follows focus"
    assert r["grid"] == [(0, 0, 1280, 720), (1280, 0, 1280, 720), (0, 720, 1280, 720), (1280, 720, 1280, 720)]
    assert r["grow_again"] == r["grow"], "third press returns to grow"
    assert r["pan_fill"] == [1.0] * 4, "fill: every video cropped until no bars"
    assert r["pan_fit"] == [0.0] * 4, "fit: nothing cropped"
    assert r["pan_limited_again"] == r["pan_limited"] and 0 < max(r["pan_limited"]) and min(r["pan_limited"]) < 1
    for mode in ("layout mode -> flat", "layout mode -> grid", "layout mode -> grow",
                 "crop mode -> fill", "crop mode -> fit", "crop mode -> limited"):
        assert mode in log, mode
    for q in (r["flat"], r["grid"], r["grow"]):
        assert sum(map(area, q)) == sw * sh
    print("MODES TEST PASSED")


if __name__ == "__main__":
    main()
