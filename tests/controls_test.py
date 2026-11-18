"""Small controls: 1-9, + / -, Ctrl+B blackout, Ctrl+G sync keeping offsets.
TAKES OVER MOUSE, KEYBOARD AND SCREEN for ~45 s.   Run: python tests/controls_test.py
Needs the clips made by tests/phase4_test.py and tests/features_test.py.

  A. three 16:9 clips: 1 releases the pin on video 0, 2 and 3 pin videos 1 and 2; + adds another video
     from the same folder, - closes an unfocused one; Ctrl+B hides everything (screen black, all
     paused and muted), Esc brings it back (picture back, playing again)
  B. three copies of the 30 s clip, 8 s and 4 s apart: Ctrl+G keeps those differences, Right seeks all
     and keeps them, and a video pushed out of step is pulled back to its own offset
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
CLIPS = os.path.join(tempfile.gettempdir(), "tesserawall-p4-clips")
LONG = os.path.join(tempfile.gettempdir(), "tesserawall-test-clip.y4m")
user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
KEYUP, EXT = 0x0002, 0x0001
VK_CTRL, VK_ESC, VK_RIGHT = 0x11, 0x1B, 0x27


def log_text():
    try:
        return open(LOG, encoding="utf-8", errors="replace").read()
    except FileNotFoundError:
        return ""


def press(vk, mods=(), ext=False, pause=0.5):
    for m in mods:
        user32.keybd_event(m, 0, 0, 0)
    user32.keybd_event(vk, 0, EXT if ext else 0, 0)
    user32.keybd_event(vk, 0, KEYUP | (EXT if ext else 0), 0)
    for m in reversed(mods):
        user32.keybd_event(m, 0, KEYUP, 0)
    time.sleep(pause)


def click(x, y):
    user32.SetCursorPos(x - 1, y)
    time.sleep(0.05)
    user32.SetCursorPos(x, y)
    time.sleep(0.3)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.5)


def pixel_brightness(points):
    """Sum of R+G+B over a few screen points (0 = all black)."""
    dc = user32.GetDC(0)
    total = 0
    for x, y in points:
        c = gdi32.GetPixel(dc, x, y)
        total += (c & 0xFF) + (c >> 8 & 0xFF) + (c >> 16 & 0xFF)
    user32.ReleaseDC(0, dc)
    return total


def launch(args, exit_after):
    if os.path.exists(LOG):
        os.remove(LOG)
    return subprocess.Popen([sys.executable, APP, "--exit-after", str(exit_after), *args])


def rects():
    found = re.findall(r"rects=(\[.*?\]) panscan", log_text())
    return eval(found[-1]) if found else []


def main():
    assert os.path.isdir(CLIPS) and os.path.exists(LONG), "run phase4_test.py and features_test.py first"
    print("WARNING: this test controls mouse, keyboard and screen for ~45 seconds.")
    tmp = tempfile.mkdtemp(prefix="tesserawall-ctl-")
    cfg = os.path.join(tmp, "config.toml")
    open(cfg, "w").close()
    base = ["--config", cfg, "--session", os.path.join(tmp, "s.json")]
    orig = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(orig))
    r, procs = {}, []
    try:
        # A
        p = launch(base + [os.path.join(CLIPS, f"land{i}.mkv") for i in range(3)], 25)
        procs.append(p)
        time.sleep(3.5)
        x, y, w, h = rects()[0]
        click(x + w // 2, y + h // 2)                       # pins video 0, keyboard to the wall
        press(ord("1"))                                     # same number: release
        press(ord("2"))
        press(ord("3"))
        r["A_pins"] = re.findall(r"pin -> (\w+)", log_text())[-3:]
        press(0xBB)                                         # VK_OEM_PLUS (+/= key)
        time.sleep(2.0)
        r["A_more"] = re.findall(r"one more video: .*\\(\w+\.mkv)", log_text())
        r["A_tiles_after_plus"] = log_text().count("tile added")
        press(0xBD)                                         # VK_OEM_MINUS
        time.sleep(1.0)
        r["A_removed"] = "tile removed (one fewer" in log_text()
        centres = [(rx + rw // 2, ry + rh // 2) for rx, ry, rw, rh in rects()]
        before = pixel_brightness(centres)
        ipc = [MpvIpc(rf"\\.\pipe\tesserawall-{p.pid}-{n}", timeout=3) for n in (1, 2, 3)]
        press(ord("B"), (VK_CTRL,), pause=1.0)
        r["A_black"] = pixel_brightness(centres)
        r["A_black_state"] = [(c.get("pause"), c.get("mute")) for c in ipc]
        press(VK_ESC, pause=1.5)
        r["A_back"] = pixel_brightness(centres)
        r["A_back_paused"] = [c.get("pause") for c in ipc]
        r["A_before"] = before
        r["A_logged"] = "blackout on" in log_text() and "blackout off" in log_text()
        for c in ipc:
            c.close()
        r["A_exit"] = p.wait(40)

        # B
        p = launch(base + [LONG, LONG, LONG], 20)
        procs.append(p)
        time.sleep(3.5)
        ipc = [MpvIpc(rf"\\.\pipe\tesserawall-{p.pid}-{n}", timeout=3) for n in (1, 2, 3)]
        pos = lambda: [round(c.get("time-pos") or -1, 2) for c in ipc]
        ipc[1].command("seek", 8, "relative")
        ipc[2].command("seek", 4, "relative")
        time.sleep(0.4)
        x, y, w, h = rects()[0]
        click(x + w // 2, y + h // 2)                       # focus video 0 (the lead)
        press(ord("G"), (VK_CTRL,), pause=1.5)
        a = pos()
        r["B_offsets_kept"] = [round(a[1] - a[0], 1), round(a[2] - a[0], 1)]
        press(VK_RIGHT, ext=True, pause=0.8)
        b = pos()
        r["B_seek_all"] = [round(b[i] - a[i], 1) for i in range(3)]   # 5 s seek + ~0.8 s of playing
        ipc[1].command("seek", 3, "relative")                # push video 1 out of step
        time.sleep(2.5)
        c = pos()
        r["B_pulled_back"] = [round(c[1] - c[0], 1), round(c[2] - c[0], 1)]
        r["B_logged"] = "sync on (keeping offsets)" in log_text() and "sync: corrected" in log_text()
        for q in ipc:
            q.close()
        r["B_exit"] = p.wait(40)
    finally:
        user32.SetCursorPos(orig.x, orig.y)
        for q in procs:
            if q.poll() is None:
                q.kill()
    for k, v in r.items():
        print(f"{k:20} {v}")
    assert r["A_pins"] == ["None", "1", "2"], r["A_pins"]
    # not a template wall, so + takes any other video from that folder (template walls: same shape,
    # covered by pick_file and N in phase4_test)
    assert r["A_more"] and r["A_more"][0] not in ("land0.mkv", "land1.mkv", "land2.mkv")
    assert r["A_tiles_after_plus"] == 4
    assert r["A_removed"]
    assert r["A_black"] < r["A_before"] * 0.1 and r["A_black"] < 60, "blackout leaves the screen black"
    assert all(s == (True, True) for s in r["A_black_state"]), "blackout pauses and mutes everything"
    assert r["A_back"] > r["A_before"] * 0.5 and r["A_back_paused"] == [False, False, False]
    assert r["A_logged"] and r["A_exit"] == 0
    assert abs(r["B_offsets_kept"][0] - 8) < 0.6 and abs(r["B_offsets_kept"][1] - 4) < 0.6, r["B_offsets_kept"]
    assert all(4.5 <= d <= 7 for d in r["B_seek_all"]), r["B_seek_all"]
    assert abs(r["B_pulled_back"][0] - 8) < 0.6 and abs(r["B_pulled_back"][1] - 4) < 0.6, r["B_pulled_back"]
    assert r["B_logged"] and r["B_exit"] == 0
    assert "callback error" not in log_text()
    print("CONTROLS TEST PASSED")


if __name__ == "__main__":
    main()
