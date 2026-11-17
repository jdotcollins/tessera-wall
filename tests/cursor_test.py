"""Idle pointer hiding. Takes over the mouse and screen for ~10 seconds. Run: python tests/cursor_test.py"""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

ctypes.windll.shcore.SetProcessDpiAwareness(2)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "tesserawall.pyw")
L = "av://lavfi:testsrc2=s=1280x720:r=30"
user32 = ctypes.windll.user32
user32.LoadCursorW.restype = wintypes.HANDLE
user32.LoadCursorW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR]


class CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hCursor", wintypes.HANDLE), ("pt", wintypes.POINT)]


ARROW = user32.LoadCursorW(None, ctypes.cast(32512, wintypes.LPCWSTR))  # IDC_ARROW; tk hides the pointer by swapping in a blank cursor


def visible():
    ci = CURSORINFO(cbSize=ctypes.sizeof(CURSORINFO))
    user32.GetCursorInfo(ctypes.byref(ci))
    return bool(ci.flags & 1) and ci.hCursor == ARROW


def nudge(x, y):
    for i in range(6):
        user32.SetCursorPos(x + i * 4, y)
        user32.mouse_event(0x0001, 1, 0, 0, 0)
        time.sleep(0.03)


def main():
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    orig = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(orig))
    wall = subprocess.Popen([sys.executable, APP, "--exit-after", "14", L, L])
    try:
        time.sleep(3)
        nudge(sw // 4, sh // 2)
        time.sleep(0.3)
        after_move = visible()
        time.sleep(3.2)
        after_idle = visible()
        nudge(sw * 3 // 4, sh // 2)
        time.sleep(0.3)
        after_move_again = visible()
        time.sleep(3.2)
        after_idle_again = visible()
        code = wall.wait(20)
    finally:
        user32.SetCursorPos(orig.x, orig.y)
        if wall.poll() is None:
            wall.kill()
    print("visible after move / idle / move / idle:", after_move, after_idle, after_move_again, after_idle_again)
    assert code == 0
    assert (after_move, after_idle, after_move_again, after_idle_again) == (True, False, True, False)
    print("CURSOR TEST PASSED")


if __name__ == "__main__":
    main()
