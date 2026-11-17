"""Page Down / Page Up: step the focused tile through its folder. TAKES OVER MOUSE, KEYBOARD AND SCREEN
for ~20 seconds. Run: python tests/stepfile_test.py     Keys are only sent while the wall is foreground.

Folder: clip a..e (landscape) plus "clip f" (portrait). The wall opens a and c, so stepping from a must
skip c (already open), wrap around at both ends, and re-tile when the shape changes at f.
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
user32 = ctypes.windll.user32
user32.GetForegroundWindow.restype = wintypes.HWND
LEFTDOWN, LEFTUP, KEYUP, EXT = 0x0002, 0x0004, 0x0002, 0x0001
VK_PRIOR, VK_NEXT, VK_Q = 0x21, 0x22, 0x51


def make_clip(path, w, h, seconds=4, fps=10):
    with open(path, "wb") as f:
        f.write(f"YUV4MPEG2 W{w} H{h} F{fps}:1 Ip A1:1 C420jpeg\n".encode())
        chroma = bytes([128]) * (w * h // 2)
        for i in range(seconds * fps):
            bar = w * i // (seconds * fps)
            f.write(b"FRAME\n" + (bytes([235]) * bar + bytes([60]) * (w - bar)) * h + chroma)


def key(vk):
    user32.keybd_event(vk, 0, EXT, 0)
    user32.keybd_event(vk, 0, KEYUP | EXT, 0)
    time.sleep(1.0)


def log_text():
    return open(LOG, encoding="utf-8", errors="replace").read()


def main():
    print("WARNING: this test controls mouse, keyboard and screen for ~20 seconds.")
    folder = tempfile.mkdtemp(prefix="tesserawall-step-")
    for name in "abcde":
        make_clip(os.path.join(folder, f"clip {name}.y4m"), 320, 180)
    make_clip(os.path.join(folder, "clip f.y4m"), 180, 320)
    open(os.path.join(folder, "notes.txt"), "w").close()
    p = lambda n: os.path.join(folder, f"clip {n}.y4m")
    orig = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(orig))
    seen = []
    wall = subprocess.Popen([sys.executable, APP, "--exit-after", "30", p("a"), p("c")])
    try:
        time.sleep(3.5)
        ipc = MpvIpc(rf"\\.\pipe\tesserawall-{wall.pid}-1", timeout=3)
        x, y, w, h = eval(re.findall(r"rects=(\[.*?\]) panscan", log_text())[-1])[0]
        user32.SetCursorPos(x + w // 2 - 1, y + h // 2)
        time.sleep(0.05)
        user32.SetCursorPos(x + w // 2, y + h // 2)
        time.sleep(0.6)
        user32.mouse_event(LEFTDOWN, 0, 0, 0, 0)          # click: pin tile 0 and take keyboard focus
        user32.mouse_event(LEFTUP, 0, 0, 0, 0)
        time.sleep(0.5)
        out = wintypes.DWORD()
        user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(out))
        assert out.value == wall.pid, "wall is not the foreground window; not sending keys"
        name = lambda: os.path.basename(ipc.get("path") or "")[5:6]
        seen.append(name())                                # a
        for vk in (VK_NEXT, VK_NEXT, VK_NEXT, VK_NEXT, VK_NEXT, VK_PRIOR, VK_PRIOR):
            key(vk)
            seen.append(name())
        aspect_log = log_text()
        user32.keybd_event(VK_Q, 0, 0, 0)
        user32.keybd_event(VK_Q, 0, KEYUP, 0)
        code = wall.wait(40)
    finally:
        user32.SetCursorPos(orig.x, orig.y)
        if wall.poll() is None:
            wall.kill()
    print("sequence:", " ".join(seen))
    log = log_text()
    assert code == 0 and "callback error" not in log
    # down x5 from a: b, (skip c) d, e, f, wrap to a ; up x2: wrap to f, e
    assert seen == ["a", "b", "d", "e", "f", "a", "f", "e"], seen
    assert "aspect known 0.5625" in aspect_log, "portrait clip f should have re-measured the tile"
    assert "notes.txt" not in log
    print("STEPFILE TEST PASSED")


if __name__ == "__main__":
    main()
