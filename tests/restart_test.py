"""Restart-all (r) reliability. TAKES OVER MOUSE, KEYBOARD AND SCREEN for about a minute.
Run: python tests/restart_test.py [cycles]

Repeats: Shift+Right twice (seek all +10 s), then r (restart all), and checks every video is back near 0.
For each failure it prints the positions and whether the wall logged the key, which separates
"the key press never arrived" from "the wall got it but a video did not restart".
"""
import ctypes
import os
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
CLIP = os.path.join(tempfile.gettempdir(), "tesserawall-test-clip.y4m")   # 30 s, made by features_test.py
user32 = ctypes.windll.user32
user32.GetForegroundWindow.restype = wintypes.HWND
KEYUP, EXT = 0x0002, 0x0001
VK_SHIFT, VK_RIGHT, VK_R, VK_Q = 0x10, 0x27, 0x52, 0x51


def key(vk, shift=False, ext=False, pause=0.25):
    if shift:
        user32.keybd_event(VK_SHIFT, 0, 0, 0)
    user32.keybd_event(vk, 0, EXT if ext else 0, 0)
    user32.keybd_event(vk, 0, KEYUP | (EXT if ext else 0), 0)
    if shift:
        user32.keybd_event(VK_SHIFT, 0, KEYUP, 0)
    time.sleep(pause)


def main():
    cycles = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    assert os.path.exists(CLIP), "run tests/features_test.py once first; it generates the clip"
    print(f"WARNING: this test controls mouse, keyboard and screen for ~{cycles * 2 + 8} seconds.")
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    orig = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(orig))
    failures = []
    wall = subprocess.Popen([sys.executable, APP, "--exit-after", str(cycles * 3 + 20)] + [CLIP] * 4)
    try:
        time.sleep(3.5)
        ipc = [MpvIpc(rf"\\.\pipe\tesserawall-{wall.pid}-{n}", timeout=3) for n in (1, 2, 3, 4)]

        def positions():
            out = []
            for c in ipc:
                v = c.get("time-pos")
                for _ in range(20):
                    if v is not None:
                        break
                    time.sleep(0.05)
                    v = c.get("time-pos")
                out.append(round(v, 1) if v is not None else None)
            return out

        user32.SetCursorPos(sw // 4, sh // 4)
        time.sleep(0.3)
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        user32.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(0.6)
        out = wintypes.DWORD()
        user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(out))
        assert out.value == wall.pid, "wall is not the foreground window; not sending keys"
        for i in range(cycles):
            # Put one video near the end so "seek all" pushes it past the end of the file (it loops).
            near_end = (18.0, 21.0, 24.0, 26.5)[i % 4]
            ipc[i % 4].command("seek", near_end, "absolute")
            time.sleep(0.3)
            key(VK_RIGHT, shift=True, ext=True)
            key(VK_RIGHT, shift=True, ext=True)
            time.sleep(0.3 if i % 2 else 0.0)           # alternate: r right after the seeks / after a pause
            seeked = positions()
            logged_before = open(LOG, encoding="utf-8", errors="replace").read().count("restart all")
            key(VK_R, pause=0.6)
            after = positions()
            logged = open(LOG, encoding="utf-8", errors="replace").read().count("restart all") - logged_before
            if not all(v is not None and v < 2 for v in after):
                failures.append((i, seeked, after, logged))
                print(f"cycle {i}: FAIL before r {seeked} after r {after} wall logged r {logged}x")
        # Upper-case R is what the wall receives when Caps Lock is on. It must work too.
        key(VK_RIGHT, shift=True, ext=True)
        key(VK_RIGHT, shift=True, ext=True)
        seeked = positions()
        key(VK_R, shift=True, pause=0.6)
        upper = positions()
        upper_ok = all(v is not None and v < 2 for v in upper)
        print(f"upper-case R: before {seeked} after {upper} -> {'ok' if upper_ok else 'IGNORED'}")
        key(VK_Q, shift=True, pause=1.5)                 # upper-case Q must quit as well
        quit_by_upper_q = wall.poll() is not None
        print("upper-case Q quits:", quit_by_upper_q)
        if not quit_by_upper_q:
            key(VK_Q)
        code = wall.wait(40)
    finally:
        user32.SetCursorPos(orig.x, orig.y)
        if wall.poll() is None:
            wall.kill()
    print(f"{cycles - len(failures)}/{cycles} cycles restarted every video")
    assert code == 0
    assert not failures, f"{len(failures)} failures"
    assert upper_ok and quit_by_upper_q, "letter keys must work with Caps Lock / Shift"
    print("RESTART TEST PASSED")


if __name__ == "__main__":
    main()
