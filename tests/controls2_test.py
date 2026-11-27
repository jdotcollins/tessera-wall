"""The controls the other tests never pressed: mouse wheel, Up / Down, dragging along the seek bar, 0, and
closing with X, Delete and middle-click.

TAKES OVER THE MOUSE, KEYBOARD AND SCREEN for ~40 seconds. Run: python tests/controls2_test.py
Checks the players' own state over their IPC pipes and the wall's log, not screen pixels, so it also works
with the monitor off. Uses generated clips; no network.

  1. all paused (Shift+Space); the wheel over a video seeks it by SEEK_SECONDS, forward and back
  2. Up / Down change the heard video's volume by VOLUME_STEP
  3. pressing near the left of the seek-bar zone and dragging to 80 % leaves the video at about 80 %
  4. click pins a video, K keeps the sound on it; 0 unpins, unfocuses and mutes every video
  5. X, Delete and a middle-click each close the video under the pointer
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
from ipc import MpvIpc  # noqa: E402

LOG = os.path.join(tempfile.gettempdir(), "tesserawall.log")
APP = os.path.join(ROOT, "tesserawall.pyw")
user32 = ctypes.windll.user32
KEYUP, EXT = 0x0002, 0x0001
VK = {"Shift": 0x10, "Space": 0x20, "Up": 0x26, "Down": 0x28, "0": 0x30, "K": 0x4B, "X": 0x58,
      "Delete": 0x2E, "Q": 0x51}
EXTENDED = {"Up", "Down", "Delete"}
SEEK, STEP, ZONE, LENGTH = 5, 5, 28, 30


def make_clip(path, seconds=LENGTH, fps=10, w=320, h=180):
    with open(path, "wb") as f:
        f.write(f"YUV4MPEG2 W{w} H{h} F{fps}:1 Ip A1:1 C420jpeg\n".encode())
        chroma = bytes([128]) * (w * h // 2)
        for i in range(seconds * fps):
            bar = w * i // (seconds * fps)
            f.write(b"FRAME\n" + (bytes([235]) * bar + bytes([60]) * (w - bar)) * h + chroma)


def log_text():
    try:
        return open(LOG, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""


def wait_count(text, n, seconds=8.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if log_text().count(text) >= n:
            return True
        time.sleep(0.1)
    return False


def rects():
    return eval(re.findall(r"rects=(\[.*?\]) panscan", log_text())[-1])


def press(*names, pause=0.5):
    for n in names:
        user32.keybd_event(VK[n], 0, EXT if n in EXTENDED else 0, 0)
    for n in reversed(names):
        user32.keybd_event(VK[n], 0, KEYUP | (EXT if n in EXTENDED else 0), 0)
    time.sleep(pause)


def move(x, y, settle=0.4):
    user32.SetCursorPos(x - 1, y)
    time.sleep(0.05)
    user32.SetCursorPos(x, y)
    time.sleep(settle)


def wheel(clicks):
    user32.mouse_event(0x0800, 0, 0, 120 * clicks, 0)    # MOUSEEVENTF_WHEEL
    time.sleep(0.6)


def button(down, up, pause=0.5):
    user32.mouse_event(down, 0, 0, 0, 0)
    user32.mouse_event(up, 0, 0, 0, 0)
    time.sleep(pause)


def centre(r):
    x, y, w, h = r
    return x + w // 2, y + h // 2


def main():
    print("WARNING: this test controls mouse, keyboard and screen for ~40 seconds.")
    folder = tempfile.mkdtemp(prefix="tesserawall-controls2-")
    clips = []
    for i in range(5):
        clips.append(os.path.join(folder, f"clip {i}.y4m"))
        make_clip(clips[-1])
    conf = os.path.join(folder, "config.toml")
    with open(conf, "w") as f:
        f.write(f'layout_mode = "flat"\nshow_help_hint = false\nseek_seconds = {SEEK}\n'
                f'volume_step = {STEP}\nseekbar_zone = {ZONE}\nwhen_finished = "loop"\n')
    if os.path.exists(LOG):
        os.remove(LOG)
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    orig = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(orig))
    move(sw // 2, sh // 2)
    r = {}
    ipc = []
    wall = subprocess.Popen([sys.executable, APP, "--exit-after", "60", "--config", conf, *clips])
    try:
        assert wait_count("relayout", 1, 10) and wait_count("aspect known", 5, 15), "the wall did not start"
        time.sleep(1.0)
        ipc = [MpvIpc(rf"\\.\pipe\tesserawall-{wall.pid}-{n}", timeout=3) for n in range(1, 6)]
        pos = lambda i: ipc[i].get("time-pos") or 0.0

        # 1. wheel over video 0 seeks it (all paused, so positions stay put between reads)
        press("Shift", "Space")
        move(*centre(rects()[0]), settle=0.8)
        r["focus_0"] = "focus -> 0" in log_text()
        ipc[0].command("seek", 10, "absolute")
        time.sleep(0.5)
        t0 = pos(0)
        wheel(1)
        t1 = pos(0)
        wheel(-1)
        t2 = pos(0)
        r["wheel"] = (round(t0, 1), round(t1, 1), round(t2, 1))

        # 2. Up / Down: volume of the video heard (the one under the pointer)
        time.sleep(1.0)                                   # let the hover's audio fade finish
        v0 = ipc[0].get("volume")
        press("Up")
        v1 = ipc[0].get("volume")
        press("Down")
        press("Down")
        v2 = ipc[0].get("volume")
        r["volume"] = (v0, v1, v2)

        # 3. seek-bar drag on video 0: press near its left end, drag to 80 %, release
        x, y, w, h = rects()[0]
        by = y + h - ZONE // 2
        move(x + w // 10, by)
        swaps = log_text().count("swap")
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        time.sleep(0.2)
        for k in range(1, 11):
            user32.SetCursorPos(x + w // 10 + (w * 7 // 10) * k // 10, by)
            time.sleep(0.05)
        user32.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(1.0)
        r["bar_pos"] = round(pos(0), 1)
        r["swapped"] = log_text().count("swap") - swaps

        # 4. click pins video 1, K keeps the sound on it; 0 releases all of that and mutes everything
        move(*centre(rects()[1]))
        button(0x0002, 0x0004, 0.8)
        r["pinned"] = "pin -> 1" in log_text()
        press("K")
        r["locked"] = "audio lock -> 1" in log_text()
        before = log_text().count("focus -> None")
        press("0", pause=1.0)
        r["unfocused"] = log_text().count("focus -> None") > before
        r["mutes_after_0"] = [ipc[i].get("mute") for i in range(5)]

        # 5. X, Delete and middle-click each close the video under the pointer
        removed = log_text().count("tile removed")
        for how in ("X", "Delete", "middle"):
            move(*centre(rects()[-1]), settle=0.8)
            if how == "middle":
                button(0x0020, 0x0040)                    # MOUSEEVENTF_MIDDLEDOWN / UP
            else:
                press(how)
            removed += 1
            r[f"closed_by_{how}"] = wait_count("tile removed", removed, 6)
        text = log_text()
        r["tiles_left"] = text.count("tile added") - text.count("tile removed")
        press("Q")
        r["exit"] = wall.wait(20)
    finally:
        for c in ipc:
            try:
                c.close()
            except Exception:
                pass
        if wall.poll() is None:
            wall.kill()
        user32.mouse_event(0x0004, 0, 0, 0, 0)
        user32.SetCursorPos(orig.x, orig.y)
    r["callback_error"] = "callback error" in log_text()
    for k, v in r.items():
        print(f"{k:18} {v}")

    t0, t1, t2 = r["wheel"]
    assert r["focus_0"], "hovering video 0 should focus it"
    assert abs(t1 - t0 - SEEK) < 1.0 and abs(t2 - t0) < 1.0, "the wheel should seek by SEEK_SECONDS and back"
    v0, v1, v2 = r["volume"]
    assert v1 == min(130, v0 + STEP) and v2 == v1 - 2 * STEP, "Up / Down should step the heard video's volume"
    assert abs(r["bar_pos"] - 0.8 * LENGTH) < 1.5, "dragging along the seek bar should leave it near 80 %"
    assert r["swapped"] == 0, "a seek-bar drag must not swap videos"
    assert r["pinned"] and r["locked"] and r["unfocused"], "click pins, K locks, 0 unfocuses"
    assert all(r["mutes_after_0"]), "after 0 every video should be muted"
    assert r["closed_by_X"] and r["closed_by_Delete"] and r["closed_by_middle"], "X, Delete, middle-click close"
    assert r["tiles_left"] == 2
    assert r["exit"] == 0 and not r["callback_error"]
    print("CONTROLS2 TEST PASSED")


if __name__ == "__main__":
    main()
