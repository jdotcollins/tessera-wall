"""End-to-end smoke test. TAKES OVER THE MOUSE AND SCREEN for about 12 seconds.

Run from the project folder:  python tests/smoke.py
Starts a real wall with four generated test videos, moves the real cursor over two tiles,
closes one tile, and checks focus, mute state, relayout and clean shutdown.
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
L, P = "av://lavfi:testsrc2=s=1280x720:r=30", "av://lavfi:testsrc2=s=720x1280:r=30"
user32 = ctypes.windll.user32


def move(x, y):
    user32.SetCursorPos(x - 1, y)
    time.sleep(0.05)
    user32.SetCursorPos(x, y)


def mutes(pid, ns):
    out = []
    for n in ns:
        c = MpvIpc(rf"\\.\pipe\tesserawall-{pid}-{n}", timeout=2)
        out.append(c.get("mute"))
        c.close()
    return out


def main():
    print("WARNING: this test controls the mouse and covers the screen for ~12 seconds.")
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    orig = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(orig))
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "tesserawall.pyw"), "--exit-after", "11", L, P, L, L])
    try:
        time.sleep(3.5)
        m0 = mutes(proc.pid, [1, 2, 3, 4])
        move(sw // 4, sh // 8)                 # top-left tile
        time.sleep(0.8)
        m1 = mutes(proc.pid, [1, 2, 3, 4])
        time.sleep(1.0)                        # mouse is still: focus must not change again
        move(sw * 7 // 8, sh * 7 // 8)         # bottom-right tile
        time.sleep(0.8)
        m2 = mutes(proc.pid, [1, 2, 3, 4])
        c = MpvIpc(rf"\\.\pipe\tesserawall-{proc.pid}-2", timeout=2)   # close the portrait tile from outside
        c.command("quit", timeout=0.5)
        code = proc.wait(15)
    finally:
        user32.SetCursorPos(orig.x, orig.y)
        if proc.poll() is None:
            proc.kill()

    log = open(LOG, encoding="utf-8", errors="replace").read()
    print(log)
    focuses = re.findall(r"focus -> (\d+)", log)
    relayouts = re.findall(r"relayout (?:rows|cols)=\[.*?\] focus=(\w+) rects=(\[.*?\]) panscan=(\[.*?\])", log)
    areas = {}
    for f, rects, _ in relayouts:
        rects = eval(rects)
        assert sum(w * h for _, _, w, h in rects) == sw * sh, "tiles must cover the screen"
        areas[(f, len(rects))] = rects

    assert code == 0, f"exit code {code}"
    assert "callback error" not in log and "crashed" not in log
    # If a person nudges the real mouse during the test there can be extra focus changes at the
    # start, so only the last two (ours) are checked strictly.
    assert m0.count(False) <= 1, f"at most one video audible at any time, got {m0}"
    assert 2 <= len(focuses) <= 4 and focuses[-2] != focuses[-1], f"expected 2 distinct focus changes, got {focuses}"
    assert focuses[-2] == "0", focuses
    assert m1 == [False, True, True, True], f"only tile 0 audible after first hover, got {m1}"
    f2 = int(focuses[-1])
    assert m2 == [i != f2 for i in range(4)], f"only tile {f2} audible after second hover, got {m2}"
    base = [r for f, r, _ in relayouts if f == "None"][-1]
    base = eval(base)
    assert len(base) == 4
    for f in (0, f2):
        grown = areas[(str(f), 4)][f]
        assert grown[2] * grown[3] > base[f][2] * base[f][3], f"tile {f} did not grow"
    assert any(n == 3 for _, n in areas), "no relayout with 3 tiles after closing one"
    assert all(0 <= p <= 1 for _, _, pans in relayouts for p in eval(pans))
    left = subprocess.run(["tasklist", "/FI", "IMAGENAME eq mpv.exe", "/NH"], capture_output=True, text=True).stdout
    assert "mpv.exe" not in left, "mpv processes left running"
    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    main()
