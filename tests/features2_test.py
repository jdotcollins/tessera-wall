"""Random sampling / swap file (n) / drag to reorder / audio crossfade / per-video volume.

TAKES OVER THE MOUSE, KEYBOARD AND SCREEN for ~20 seconds. Run: python tests/features2_test.py
Keys are only sent while the wall is the foreground window.
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
LEFTDOWN, LEFTUP, MOVE, KEYUP, EXT = 0x0002, 0x0004, 0x0001, 0x0002, 0x0001
VK_DOWN, VK_N, VK_Q = 0x28, 0x4E, 0x51


def make_clip(path, seconds=4, fps=10, w=320, h=180):
    with open(path, "wb") as f:
        f.write(f"YUV4MPEG2 W{w} H{h} F{fps}:1 Ip A1:1 C420jpeg\n".encode())
        chroma = bytes([128]) * (w * h // 2)
        for i in range(seconds * fps):
            bar = w * i // (seconds * fps)
            f.write(b"FRAME\n" + (bytes([235]) * bar + bytes([60]) * (w - bar)) * h + chroma)


def move(x, y):
    user32.SetCursorPos(x - 1, y)
    time.sleep(0.05)
    user32.SetCursorPos(x, y)


def click(x, y):
    move(x, y)
    time.sleep(0.1)
    user32.mouse_event(LEFTDOWN, 0, 0, 0, 0)
    user32.mouse_event(LEFTUP, 0, 0, 0, 0)
    time.sleep(0.4)


def drag(a, b):
    move(*a)
    time.sleep(0.1)
    user32.mouse_event(LEFTDOWN, 0, 0, 0, 0)
    for i in range(1, 21):
        user32.SetCursorPos(a[0] + (b[0] - a[0]) * i // 20, a[1] + (b[1] - a[1]) * i // 20)
        user32.mouse_event(MOVE, 1, 0, 0, 0)
        user32.mouse_event(MOVE, -1, 0, 0, 0)
        time.sleep(0.02)
    time.sleep(0.1)
    user32.mouse_event(LEFTUP, 0, 0, 0, 0)
    time.sleep(0.5)


def key(vk, ext=False):
    user32.keybd_event(vk, 0, EXT if ext else 0, 0)
    user32.keybd_event(vk, 0, KEYUP | (EXT if ext else 0), 0)
    time.sleep(0.25)


def wall_is_foreground(pid):
    out = wintypes.DWORD()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(out))
    return out.value == pid


def log_text():
    return open(LOG, encoding="utf-8", errors="replace").read()


def centres():
    rects = eval(re.findall(r"rects=(\[.*?\]) panscan", log_text())[-1])
    return [(x + w // 2, y + h // 2) for x, y, w, h in rects]


def main():
    print("WARNING: this test controls mouse, keyboard and screen for ~20 seconds.")
    folder = tempfile.mkdtemp(prefix="tesserawall-rand-")
    for name in "abcde":
        make_clip(os.path.join(folder, f"clip {name}.y4m"))
    orig = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(orig))
    r = {}
    wall = subprocess.Popen([sys.executable, APP, "--exit-after", "30", "--random", "3", folder])
    pid = wall.pid
    try:
        time.sleep(3.5)
        ipc = [MpvIpc(rf"\\.\pipe\tesserawall-{pid}-{n}", timeout=3) for n in (1, 2, 3)]
        state = lambda: [(c.get("mute"), c.get("volume")) for c in ipc]
        opened = re.findall(r"tile added #\d+ pipe=\S+ path=(.*)", log_text())
        r["opened"] = [os.path.basename(p) for p in opened]

        # --- crossfade: focus tile 0, then tile 1, sampling both players during the switch
        move(*centres()[0])
        time.sleep(0.8)
        r["after_first_hover"] = state()
        move(*centres()[1])
        samples, t0 = [], time.monotonic()
        while time.monotonic() - t0 < 0.9:
            samples.append((ipc[0].get("mute"), ipc[0].get("volume"), ipc[1].get("mute"), ipc[1].get("volume")))
        r["samples"] = len(samples)
        r["mid_fade"] = [s for s in samples if s[0] is False and s[2] is False and (0 < s[1] < 100 or 0 < s[3] < 100)][:3]
        r["after_switch"] = state()

        # --- per-video volume is remembered across focus changes
        click(*centres()[1])                       # pins tile 1 and gives the wall keyboard focus
        r["foreground"] = wall_is_foreground(pid)
        if r["foreground"]:
            key(VK_DOWN, ext=True)
            key(VK_DOWN, ext=True)
            r["volume_after_down_x2"] = ipc[1].get("volume")
            click(*centres()[1])                   # unpin
            move(*centres()[0])
            time.sleep(0.8)
            move(*centres()[1])
            time.sleep(0.8)
            r["volume_after_refocus"] = state()

            # --- n: swap the focused video (tile 1) for another file from its folder
            before = ipc[1].get("path")
            key(VK_N)
            time.sleep(1.5)
            r["swap"] = (os.path.basename(before), os.path.basename(ipc[1].get("path") or ""))
            r["paths_now"] = [os.path.basename(c.get("path") or "") for c in ipc]

        # --- drag tile 0 onto tile 2: they swap places, so player 3 is now at position 0
        move(*centres()[0])                        # hovering tile 0 focuses it and reflows the grid,
        time.sleep(0.8)                            # so wait, then aim at where tile 2 is NOW
        c = centres()
        drag(c[0], c[2])
        r["reorder_logged"] = "reorder 0 <-> 2" in log_text()
        focus_before = log_text().count("focus ->")
        time.sleep(0.5)
        r["focus_changed_by_drag_alone"] = log_text().count("focus ->") != focus_before
        move(*centres()[0])
        time.sleep(0.8)
        r["audible_at_position_0"] = [m is False for m, _ in state()]

        if r["foreground"]:
            key(VK_Q)
        code = wall.wait(40)
    finally:
        user32.mouse_event(LEFTUP, 0, 0, 0, 0)
        user32.SetCursorPos(orig.x, orig.y)
        if wall.poll() is None:
            wall.kill()

    for k, v in r.items():
        print(f"{k:28} {v}")
    log = log_text()
    assert code == 0 and "callback error" not in log, log[-1500:]
    assert len(r["opened"]) == 3 and len(set(r["opened"])) == 3, "--random 3 should open 3 different files"
    assert r["after_first_hover"] == [(False, 100.0), (True, 100.0), (True, 100.0)]
    assert r["mid_fade"], "no sample caught both videos audible mid-fade"
    assert r["after_switch"] == [(True, 100.0), (False, 100.0), (True, 100.0)], "after the fade"
    assert r["foreground"], "wall was not foreground; key tests skipped"
    assert r["volume_after_down_x2"] == 90.0
    assert r["volume_after_refocus"][1] == (False, 90.0), "tile 1 should come back at its own volume"
    assert r["swap"][0] != r["swap"][1] and r["swap"][1].startswith("clip "), "n should load another file"
    assert len(set(r["paths_now"])) == 3, "swapped-in file must not duplicate an open one"
    assert r["reorder_logged"] and not r["focus_changed_by_drag_alone"]
    assert r["audible_at_position_0"] == [False, False, True], "after the drag, player 3 sits at position 0"
    print("FEATURES2 TEST PASSED")


if __name__ == "__main__":
    main()
