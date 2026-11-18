"""Memory guard, Tab focus, sync mode, saved walls. TAKES OVER MOUSE, KEYBOARD AND SCREEN for ~50 s.
Run: python tests/phase3_test.py      Uses its own settings/walls folder and session file.

  A. a pretend 700 MB graphics card: of 6 videos only 3 open, and the wall says why
  B. Tab / Shift+Tab move focus; G syncs (drift corrected, pause and seek act on all); Ctrl+S saves
     the wall under a typed name (typing in the dialog must not reach the wall)
  C. a launch with no files shows the saved wall in the start window; clicking it opens it
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
from ipc import MpvIpc

LOG = os.path.join(tempfile.gettempdir(), "tesserawall.log")
APP = os.path.join(ROOT, "tesserawall.pyw")
CLIP = os.path.join(tempfile.gettempdir(), "tesserawall-test-clip.y4m")   # 30 s; made by features_test.py
L = "av://lavfi:testsrc2=s=1280x720:r=30"
user32 = ctypes.windll.user32
user32.GetForegroundWindow.restype = wintypes.HWND
KEYUP, EXT = 0x0002, 0x0001
VK_SHIFT, VK_CTRL, VK_TAB, VK_RIGHT, VK_SPACE, VK_RETURN = 0x10, 0x11, 0x09, 0x27, 0x20, 0x0D


def log_text():
    try:
        return open(LOG, encoding="utf-8", errors="replace").read()
    except FileNotFoundError:
        return ""


def press(vk, mods=(), ext=False, pause=0.4):
    for m in mods:
        user32.keybd_event(m, 0, 0, 0)
    user32.keybd_event(vk, 0, EXT if ext else 0, 0)
    user32.keybd_event(vk, 0, KEYUP | (EXT if ext else 0), 0)
    for m in reversed(mods):
        user32.keybd_event(m, 0, KEYUP, 0)
    time.sleep(pause)


def type_text(s):
    for ch in s:
        press(0x20 if ch == " " else ord(ch.upper()), pause=0.05)


def click(x, y):
    user32.SetCursorPos(x - 1, y)
    time.sleep(0.05)
    user32.SetCursorPos(x, y)
    time.sleep(0.3)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.5)


def foreground_is(pid):
    out = wintypes.DWORD()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(out))
    return out.value == pid


def launch(args, env=None):
    if os.path.exists(LOG):
        os.remove(LOG)
    return subprocess.Popen([sys.executable, APP, *args], env=dict(os.environ, **(env or {})))


def main():
    assert os.path.exists(CLIP), "run tests/features_test.py once first; it generates the clip"
    print("WARNING: this test controls mouse, keyboard and screen for ~50 seconds.")
    tmp = tempfile.mkdtemp(prefix="tesserawall-p3-")
    cfg = os.path.join(tmp, "config.toml")
    open(cfg, "w").close()
    session = os.path.join(tmp, "session.json")
    orig = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(orig))
    r, procs = {}, []
    try:
        # A. memory guard
        p = launch(["--exit-after", "6", "--config", cfg, "--session", session] + [L] * 6, {"TESSERAWALL_VRAM_MB": "700"})
        procs.append(p)
        r["A_exit"] = p.wait(30)
        log = log_text()
        r["A_opened"] = log.count("tile added")
        r["A_refused"] = log.count("not opened (not enough video memory")
        r["A_reported"] = bool(re.search(r"refused reported: 3 \(not enough video memory", log))

        # B. Tab focus, sync, save wall
        p = launch(["--exit-after", "60", "--config", cfg, "--session", session, CLIP, CLIP, CLIP])
        procs.append(p)
        time.sleep(3.5)
        ipc = [MpvIpc(rf"\\.\pipe\tesserawall-{p.pid}-{n}", timeout=3) for n in (1, 2, 3)]
        pos = lambda: [round(c.get("time-pos") or -1, 2) for c in ipc]
        rects = eval(re.findall(r"rects=(\[.*?\]) panscan", log_text())[-1])
        x, y, w, h = rects[0]
        click(x + w // 2, y + h // 2)                         # pin video 0, keyboard focus to the wall
        assert foreground_is(p.pid), "wall is not the foreground window; not sending keys"
        # Shift+Tab only: on the development PC a synthetic bare Tab never reaches any tk window (a minimal
        # tk probe gets Shift+Tab and letters but not Tab), so plain Tab is checked by hand. Same move_focus.
        press(VK_TAB, (VK_SHIFT,))
        press(VK_TAB, (VK_SHIFT,))
        r["B_pins"] = re.findall(r"pin -> (\d+)", log_text())
        press(VK_TAB, (VK_SHIFT,))                            # wraps back to video 0
        ipc[2].command("seek", 8, "relative")                # put video 2 eight seconds ahead
        time.sleep(0.3)
        r["B_before_sync"] = pos()
        press(ord("G"), pause=1.8)
        r["B_after_sync"] = pos()
        press(VK_SPACE, pause=0.6)
        r["B_paused"] = [c.get("pause") for c in ipc]
        p0 = pos()
        press(VK_RIGHT, ext=True, pause=0.8)
        r["B_seek_all"] = [round(b - a, 1) for a, b in zip(p0, pos())]
        press(ord("G"), pause=0.5)                            # sync off
        p0 = pos()
        press(VK_RIGHT, ext=True, pause=0.8)
        r["B_seek_one"] = [round(b - a, 1) for a, b in zip(p0, pos())]
        layout_events_before = log_text().count("layout mode ->")
        press(ord("S"), (VK_CTRL,), pause=1.0)                # save dialog
        type_text("my wall")
        press(VK_RETURN, pause=1.0)
        log = log_text()
        r["B_keys_leaked_into_wall"] = (log.count("layout mode ->") != layout_events_before)
        saved = os.path.join(tmp, "walls", "my wall.json")
        r["B_saved"] = json.load(open(saved, encoding="utf-8")) if os.path.exists(saved) else None
        press(ord("Q"), pause=1.5)
        r["B_exit"] = p.wait(30)

        # C. start window lists the saved wall; clicking it opens it
        p = launch(["--exit-after", "10", "--config", cfg, "--session", os.path.join(tmp, "none.json")])
        procs.append(p)
        end = time.monotonic() + 8
        m = None
        while time.monotonic() < end and not m:
            m = re.search(r"start window wall0 at (-?\d+),(-?\d+) (\d+)x(\d+)", log_text())
            time.sleep(0.1)
        r["C_listed"] = bool(m)
        if m:
            x, y, w, h = map(int, m.groups())
            time.sleep(0.5)
            click(x + w // 2, y + h // 2)
            time.sleep(3)
            r["C_opened"] = log_text().count("tile added")
        r["C_exit"] = p.wait(30)
    finally:
        user32.SetCursorPos(orig.x, orig.y)
        for q in procs:
            if q.poll() is None:
                q.kill()
    for k, v in r.items():
        print(f"{k:24} {v}")
    assert r["A_exit"] == 0 and r["A_opened"] == 3 and r["A_refused"] == 3 and r["A_reported"]
    assert r["B_pins"][-3:] == ["0", "2", "1"], r["B_pins"]
    spread = max(r["B_after_sync"]) - min(r["B_after_sync"])
    assert max(r["B_before_sync"]) - min(r["B_before_sync"]) > 5 and spread < 0.6, "G should line the videos up"
    assert r["B_paused"] == [True, True, True], "Space in sync mode pauses every video"
    assert all(4 <= d <= 6 for d in r["B_seek_all"]), "Right in sync mode seeks every video"
    assert 4 <= r["B_seek_one"][0] <= 6 and abs(r["B_seek_one"][1]) < 0.5, "after G again only one video seeks"
    assert not r["B_keys_leaked_into_wall"], "typing in the save dialog must not reach the wall"
    assert r["B_saved"] and r["B_saved"]["name"] == "my wall" and len(r["B_saved"]["videos"]) == 3
    assert r["B_exit"] == 0
    assert r["C_listed"] and r["C_opened"] == 3 and r["C_exit"] == 0
    assert "callback error" not in log_text()
    print("PHASE3 TEST PASSED")


if __name__ == "__main__":
    main()
