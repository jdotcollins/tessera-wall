"""A swapped-in video starts at its own beginning and without the previous video's A-B loop, even when the
player was launched at a restored position. TAKES OVER MOUSE, KEYBOARD AND SCREEN for ~15 seconds.
Run: python tests/carryover_test.py     Keys are only sent while the wall is foreground.

mpv applies a launch-time --start to every file it loads and keeps ab-loop-a/b across loadfile, so before
Wall.load_into passed start= and ab-loop-a/b=no, Page Down from a session restored at 12 s opened the next
video at 12 s with the old loop still set.
"""
import ctypes
import json
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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ipc import MpvIpc
from stepfile_test import make_clip, key, log_text, LEFTDOWN, LEFTUP, KEYUP, VK_NEXT, VK_Q

APP = os.path.join(ROOT, "tesserawall.pyw")
user32 = ctypes.windll.user32
user32.GetForegroundWindow.restype = wintypes.HWND


def main():
    print("WARNING: this test controls mouse, keyboard and screen for ~15 seconds.")
    folder = tempfile.mkdtemp(prefix="tesserawall-carry-")
    a, b = (os.path.join(folder, f"clip {n}.y4m") for n in "ab")
    for path in (a, b):
        make_clip(path, 320, 180, seconds=20)
    session = os.path.join(folder, "session.json")
    with open(session, "w", encoding="utf-8") as f:
        json.dump({"videos": [{"path": a, "pos": 12.0}]}, f)
    orig = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(orig))
    wall = subprocess.Popen([sys.executable, APP, "--exit-after", "30", "--session", session, "--resume"])
    try:
        time.sleep(3.5)
        ipc = MpvIpc(rf"\\.\pipe\tesserawall-{wall.pid}-1", timeout=3)
        start_a = ipc.get("time-pos")
        assert start_a is not None and start_a >= 11.5, f"restored session should start a at 12 s, got {start_a}"
        ipc.set("pause", True)
        ipc.set("ab-loop-a", "2")
        ipc.set("ab-loop-b", "4")
        x, y, w, h = eval(re.findall(r"rects=(\[.*?\]) panscan", log_text())[-1])[0]
        user32.SetCursorPos(x + w // 2 - 1, y + h // 2)
        time.sleep(0.05)
        user32.SetCursorPos(x + w // 2, y + h // 2)
        time.sleep(0.6)
        user32.mouse_event(LEFTDOWN, 0, 0, 0, 0)          # click: pin the tile and take keyboard focus
        user32.mouse_event(LEFTUP, 0, 0, 0, 0)
        time.sleep(0.5)
        out = wintypes.DWORD()
        user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(out))
        assert out.value == wall.pid, "wall is not the foreground window; not sending keys"
        key(VK_NEXT)                                       # Page Down: a -> b
        time.sleep(0.5)
        path_b = os.path.basename(ipc.get("path") or "")
        start_b = ipc.get("time-pos")
        loop = (ipc.get("ab-loop-a"), ipc.get("ab-loop-b"))
        user32.keybd_event(VK_Q, 0, 0, 0)
        user32.keybd_event(VK_Q, 0, KEYUP, 0)
        code = wall.wait(40)
    finally:
        user32.SetCursorPos(orig.x, orig.y)
        if wall.poll() is None:
            wall.kill()
    print(f"a started at {start_a:.1f} s; after Page Down: {path_b} at {start_b} s, ab-loop {loop}")
    assert code == 0 and "callback error" not in log_text()
    assert path_b == "clip b.y4m", path_b
    assert start_b is not None and start_b < 5, "b must start at its beginning, not at a's restored 12 s"
    assert all(v in (None, "no", False) for v in loop), f"a's A-B loop carried over to b: {loop}"
    print("CARRYOVER TEST PASSED")


if __name__ == "__main__":
    main()
