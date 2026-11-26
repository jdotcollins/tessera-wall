"""Stop at end, subfolders, audio lock (K), menu-bar shortcut labels, the launch-hint counter.

TAKES OVER THE MOUSE, KEYBOARD AND SCREEN for ~20 seconds. Run: python tests/batch1_test.py
Keys are only sent while the wall is the foreground window.
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
user32 = ctypes.windll.user32
user32.GetForegroundWindow.restype = wintypes.HWND
LEFTDOWN, LEFTUP, KEYUP, EXT = 0x0002, 0x0004, 0x0002, 0x0001
VK_DOWN, VK_K, VK_Q, VK_F10, VK_ESC = 0x28, 0x4B, 0x51, 0x79, 0x1B


def make_clip(path, seconds=2, fps=10, w=320, h=180):
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


def key(vk, ext=False):
    user32.keybd_event(vk, 0, EXT if ext else 0, 0)
    user32.keybd_event(vk, 0, KEYUP | (EXT if ext else 0), 0)
    time.sleep(0.3)


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
    folder = tempfile.mkdtemp(prefix="tesserawall-batch1-")
    os.makedirs(os.path.join(folder, "sub"))
    for rel in ("clip a.y4m", "clip b.y4m", os.path.join("sub", "clip c.y4m")):
        make_clip(os.path.join(folder, rel))
    conf_dir = tempfile.mkdtemp(prefix="tesserawall-batch1-conf-")
    config = os.path.join(conf_dir, "config.toml")
    with open(config, "w") as f:
        f.write('when_finished = "stop"\ninclude_subfolders = true\nlayout_mode = "flat"\nanimations = "on"\n')
    orig = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(orig))
    r = {}
    wall = subprocess.Popen([sys.executable, APP, "--exit-after", "40", "--config", config, folder])
    pid = wall.pid
    try:
        time.sleep(4.5)                                    # 2 s clips: every one has reached its end
        opened = re.findall(r"tile added #\d+ pipe=\S+ path=(.*)", log_text())
        r["opened"] = sorted(os.path.relpath(p, folder) for p in opened)
        ipc = [MpvIpc(rf"\\.\pipe\tesserawall-{pid}-{n}", timeout=3) for n in (1, 2, 3)]
        state = lambda: [(c.get("mute"), c.get("volume")) for c in ipc]

        # --- stop: each player sits paused at its end on the same file, and nothing advanced
        r["stopped"] = [(c.get("eof-reached"), c.get("pause")) for c in ipc]
        r["paths"] = sorted(os.path.basename(c.get("path") or "") for c in ipc)
        r["advanced"] = "finished (" in log_text()

        # --- K: sound stays on tile 0 while pointing at tile 1
        click(*centres()[0])                               # pin (and the wall gets the keys) ...
        time.sleep(0.6)                                    # (not a double-click, which would zoom)
        click(*centres()[0])                               # ... and release
        r["foreground"] = wall_is_foreground(pid)
        if r["foreground"]:
            move(*centres()[0])
            time.sleep(0.6)
            key(VK_K)
            move(*centres()[1])
            time.sleep(0.8)
            r["locked_hovering_1"] = state()
            r["focus_moved"] = "focus -> 1" in log_text()
            key(VK_DOWN, ext=True)                         # volume goes to the video you hear
            r["volume_locked"] = (ipc[0].get("volume"), ipc[1].get("volume"))
            key(VK_K)                                      # release: sound follows the pointer again
            time.sleep(0.8)
            r["released"] = state()

            # --- F10: the menu bar, every button inside the screen, Close still on it
            key(VK_F10)
            time.sleep(0.6)
            bar = re.findall(r"top bar shown: (.*)", log_text())
            r["bar"] = bar[-1] if bar else ""
            key(VK_ESC)
            key(VK_Q)
        code = wall.wait(45)
    finally:
        user32.mouse_event(LEFTUP, 0, 0, 0, 0)
        user32.SetCursorPos(orig.x, orig.y)
        if wall.poll() is None:
            wall.kill()
    try:
        with open(os.path.join(conf_dir, "state.json")) as f:
            r["state"] = json.load(f)
    except OSError:
        r["state"] = None

    for k, v in r.items():
        print(f"{k:20} {v}")
    log = log_text()
    assert code == 0 and "callback error" not in log, log[-1500:]
    assert r["opened"] == ["clip a.y4m", "clip b.y4m", os.path.join("sub", "clip c.y4m")], "subfolder clip missing"
    assert r["stopped"] == [(True, True)] * 3, "stop: each video should sit paused at its end"
    assert not r["advanced"], "stop: nothing should move on"
    assert r["foreground"], "wall was not foreground; key tests skipped"
    assert r["focus_moved"], "pointing at tile 1 should still move the focus"
    assert [m for m, _ in r["locked_hovering_1"]] == [False, True, True], "K: tile 0 should keep the sound"
    assert r["volume_locked"] == (95.0, 100.0), "Down should change the locked video's volume"
    assert [m for m, _ in r["released"]] == [True, False, True], "K again: sound follows the pointer to tile 1"
    rects = {k: tuple(map(int, v.split(","))) for k, v in (p.split("=") for p in r["bar"].split())}
    screen_w = user32.GetSystemMetrics(0)
    assert rects and all(x + w <= screen_w for x, _, w, _ in rects.values()), f"menu bar overflows: {rects}"
    left = [x + w for k, (x, _, w, _) in rects.items() if k != "close"]
    assert max(left) <= rects["close"][0], "menu bar buttons run into Close"
    assert r["state"] == {"hint_walls": 1}, "the launch hint should have been counted once"
    print("BATCH1 TEST PASSED")


if __name__ == "__main__":
    main()
