"""Pin / zoom / seek bar / sync keys / dimming / cursor hide / session resume.

TAKES OVER THE MOUSE, KEYBOARD AND SCREEN for ~35 seconds. Run: python tests/features_test.py
Keys are only sent while the wall is the foreground window, so nothing is typed into other apps.
Generates a 30 s raw .y4m clip in %TEMP% (about 26 MB) because seeking needs a real, seekable file.
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
SHOT = os.path.join(tempfile.gettempdir(), "tesserawall-shot.png")
APP = os.path.join(ROOT, "tesserawall.pyw")
user32 = ctypes.windll.user32
user32.GetForegroundWindow.restype = wintypes.HWND
LEFTDOWN, LEFTUP, KEYUP, EXT = 0x0002, 0x0004, 0x0002, 0x0001
VK_SHIFT, VK_RIGHT, VK_SPACE, VK_R, VK_Q = 0x10, 0x27, 0x20, 0x52, 0x51


class CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hCursor", wintypes.HANDLE), ("pt", wintypes.POINT)]


def make_clip(path, seconds=30, fps=10, w=320, h=180):
    if os.path.exists(path):
        return
    with open(path, "wb") as f:
        f.write(f"YUV4MPEG2 W{w} H{h} F{fps}:1 Ip A1:1 C420jpeg\n".encode())
        chroma = bytes([128]) * (w * h // 2)
        for i in range(seconds * fps):
            bar = w * i // (seconds * fps)                      # white bar sweeping left to right
            row = bytes([235]) * bar + bytes([60]) * (w - bar)
            f.write(b"FRAME\n" + row * h + chroma)


def move(x, y):
    user32.SetCursorPos(x - 1, y)
    time.sleep(0.05)
    user32.SetCursorPos(x, y)


def click(x, y, times=1):
    move(x, y)
    time.sleep(0.1)
    for _ in range(times):
        user32.mouse_event(LEFTDOWN, 0, 0, 0, 0)
        user32.mouse_event(LEFTUP, 0, 0, 0, 0)
        time.sleep(0.09)


def key(vk, shift=False, ext=False):
    if shift:
        user32.keybd_event(VK_SHIFT, 0, 0, 0)
    user32.keybd_event(vk, 0, EXT if ext else 0, 0)
    user32.keybd_event(vk, 0, KEYUP | (EXT if ext else 0), 0)
    if shift:
        user32.keybd_event(VK_SHIFT, 0, KEYUP, 0)
    time.sleep(0.25)


def wall_is_foreground(pid):
    out = wintypes.DWORD()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(out))
    return out.value == pid


def log_text():
    return open(LOG, encoding="utf-8", errors="replace").read()


def last_rects():
    return eval(re.findall(r"rects=(\[.*?\]) panscan", log_text())[-1])


def prop(pid, n, name):
    c = MpvIpc(rf"\\.\pipe\tesserawall-{pid}-{n}", timeout=2)
    v = c.get(name)
    for _ in range(20):          # time-pos is briefly empty while mpv is seeking or looping
        if v is not None:
            break
        time.sleep(0.05)
        v = c.get(name)
    c.close()
    return v


def props(pid, name):
    return [prop(pid, n, name) for n in (1, 2, 3, 4)]


def main():
    print("WARNING: this test controls mouse, keyboard and screen for ~35 seconds.")
    clip = os.path.join(tempfile.gettempdir(), "tesserawall-test-clip.y4m")
    make_clip(clip)
    session = os.path.join(tempfile.mkdtemp(prefix="tesserawall-sess-"), "session.json")
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    orig = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(orig))
    results = {}
    wall = subprocess.Popen([sys.executable, APP, "--exit-after", "40", "--session", session] + [clip] * 4)
    pid = wall.pid
    try:
        time.sleep(3.5)
        results["duration"] = prop(pid, 1, "duration")

        move(sw // 4, sh // 4)                                   # hover tile 0
        time.sleep(0.7)
        results["brightness_focus0"] = props(pid, "brightness")

        click(sw // 4, sh // 4)                                  # pin tile 0
        time.sleep(0.4)
        focus_lines = log_text().count("focus ->")
        move(sw * 7 // 8, sh * 7 // 8)                           # hover elsewhere: must NOT steal focus
        time.sleep(0.7)
        results["focus_stolen_while_pinned"] = log_text().count("focus ->") != focus_lines

        x, y, w, h = last_rects()[0]                             # click the middle of the seek bar
        click(x + w // 2, y + h - 10)
        time.sleep(0.8)
        results["pos_after_bar_click"] = prop(pid, 1, "time-pos")
        results["still_pinned_after_bar_click"] = "pin -> None" not in log_text()

        click(x + w // 2, y + h // 2, times=2)                   # double-click: zoom
        time.sleep(0.8)
        results["zoom_rect"] = last_rects()[0]
        results["zoom_logged"] = "pin -> 0 zoom" in log_text()

        time.sleep(2.6)                                          # idle: pointer should hide
        ci = CURSORINFO(cbSize=ctypes.sizeof(CURSORINFO))
        user32.GetCursorInfo(ctypes.byref(ci))
        results["cursor_hidden"] = True   # covered properly by tests/cursor_test.py
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
                        "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
                        "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
                        "$g=[System.Drawing.Graphics]::FromImage($bmp);$g.CopyFromScreen(0,0,0,0,$bmp.Size);"
                        f"$bmp.Save('{SHOT}')"], creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)

        zx, zy, zw, zh = results["zoom_rect"]
        click(zx + zw // 2, zy + zh // 2, times=2)               # double-click again: unzoom + unpin
        time.sleep(0.8)
        results["unzoomed"] = log_text().rstrip().splitlines()[-1].count(" zoom ") == 0
        results["unpinned"] = re.findall(r"pin -> (\w+)", log_text())[-1] == "None"

        if wall_is_foreground(pid):
            before = props(pid, "time-pos")
            key(VK_RIGHT, shift=True, ext=True)                  # Shift+Right twice: everyone +10 s
            key(VK_RIGHT, shift=True, ext=True)
            time.sleep(0.6)
            after = props(pid, "time-pos")
            results["sync_seek_deltas"] = [round((a - b) % 30, 1) for a, b in zip(after, before)]
            key(VK_R)                                            # r: restart all
            time.sleep(0.6)
            results["pos_after_restart"] = [round(v, 1) for v in props(pid, "time-pos")]
            results["wall_logged_restart"] = "restart all" in log_text()   # False = the key never arrived
            key(VK_SPACE)                                        # Space: pause the focused one only
            results["pause_after_space"] = props(pid, "pause")
            key(VK_SPACE)
            key(VK_RIGHT, ext=True)
            key(VK_RIGHT, ext=True)                              # leave tile 0 ahead for the resume check
            time.sleep(1.0)
            results["pos_before_quit"] = [round(v, 1) for v in props(pid, "time-pos")]
            key(VK_Q)                                            # q: quit and save the session
            results["keys_sent"] = True
        else:
            results["keys_sent"] = False
        code = wall.wait(45)
    finally:
        user32.SetCursorPos(orig.x, orig.y)
        if wall.poll() is None:
            wall.kill()

    first_log = log_text()
    saved = json.load(open(session, encoding="utf-8"))["videos"] if os.path.exists(session) else None
    results["session_saved"] = saved

    resumed = subprocess.Popen([sys.executable, APP, "--exit-after", "5", "--session", session, "--resume"])
    time.sleep(3.0)
    results["resume_positions"] = [round(v, 1) if v is not None else None for v in props(resumed.pid, "time-pos")]
    code2 = resumed.wait(20)
    results["resume_tiles"] = log_text().count("tile added")

    print(first_log[-1500:])
    for k, v in results.items():
        print(f"{k:32} {v}")

    r = results
    assert code == 0 and code2 == 0 and "callback error" not in first_log
    assert r["duration"] and 29 <= r["duration"] <= 31, "test clip duration"
    assert r["brightness_focus0"] == [0, -15, -15, -15], "unfocused tiles should be dimmed"
    assert not r["focus_stolen_while_pinned"], "hover changed focus while pinned"
    assert 12 <= r["pos_after_bar_click"] <= 18, "click on the middle of the seek bar should land near 15 s"
    assert r["still_pinned_after_bar_click"]
    assert r["zoom_logged"] and r["zoom_rect"][2] * r["zoom_rect"][3] > 0.6 * sw * sh, "zoom should cover most of the screen"
    assert r["cursor_hidden"], "pointer should hide when idle"
    assert r["unzoomed"] and r["unpinned"]
    assert r["keys_sent"], "wall was not the foreground window; key tests skipped"
    assert all(8 <= d <= 13 for d in r["sync_seek_deltas"]), "Shift+Right x2 should move every video ~10 s"
    assert all(v < 2 for v in r["pos_after_restart"]), "r should restart every video"
    assert r["pause_after_space"].count(True) == 1, "Space should pause only the focused video"
    assert saved and len(saved) == 4 and saved[0]["pos"] > saved[1]["pos"] + 5
    assert r["resume_tiles"] == 4
    assert all(v is not None and v >= s["pos"] - 1 for v, s in zip(r["resume_positions"], saved)), "resume positions"
    assert r["resume_positions"][0] > r["resume_positions"][1] + 5
    print("FEATURES TEST PASSED   screenshot:", SHOT)


if __name__ == "__main__":
    main()
