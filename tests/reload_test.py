"""Settings hot-reload (F5). TAKES OVER MOUSE, KEYBOARD AND SCREEN for ~15 seconds.
Run: python tests/reload_test.py

Uses its own settings file (--config), so the real one in %APPDATA% is never touched. Edits that file
while the wall is open, presses F5 and checks the new values took effect; then checks that one bad key
is skipped while the rest apply, and that a file which is not valid TOML changes nothing.
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
L, P = "av://lavfi:testsrc2=s=1280x720:r=30", "av://lavfi:testsrc2=s=720x1280:r=30"
user32 = ctypes.windll.user32
user32.GetForegroundWindow.restype = wintypes.HWND
VK_F5, VK_Q, KEYUP = 0x74, 0x51, 0x0002


def key(vk):
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, KEYUP, 0)
    time.sleep(0.8)


def log_text():
    return open(LOG, encoding="utf-8", errors="replace").read()


def rects():
    return eval(re.findall(r"rects=(\[.*?\]) panscan", log_text())[-1])


def main():
    print("WARNING: this test controls mouse, keyboard and screen for ~15 seconds.")
    cfg = os.path.join(tempfile.mkdtemp(prefix="tesserawall-reload-"), "config.toml")

    def write(text):
        with open(cfg, "w", encoding="utf-8") as f:
            f.write(text)

    write("dim_unfocused = 20\n")                          # read at startup
    orig = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(orig))
    r = {}
    wall = subprocess.Popen([sys.executable, APP, "--exit-after", "30", "--config", cfg, L, P, L, L])
    try:
        time.sleep(3.5)
        ipc = [MpvIpc(rf"\\.\pipe\tesserawall-{wall.pid}-{n}", timeout=3) for n in (1, 2, 3, 4)]
        state = lambda: ([c.get("brightness") for c in ipc], [round(c.get("panscan"), 3) for c in ipc])
        x, y, w, h = rects()[0]
        user32.SetCursorPos(x + w // 2 - 1, y + h // 2)
        time.sleep(0.05)
        user32.SetCursorPos(x + w // 2, y + h // 2)
        time.sleep(0.7)
        user32.mouse_event(0x0002, 0, 0, 0, 0)            # click: pin tile 0, take keyboard focus
        user32.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(0.5)
        out = wintypes.DWORD()
        user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(out))
        assert out.value == wall.pid, "wall is not the foreground window; not sending keys"
        r["startup"] = (state(), rects()[0])

        write("dim_unfocused = 40\nfocus_scale = 1.15\nmax_crop = 0.0\n")
        key(VK_F5)
        r["after_reload"] = (state(), rects()[0])

        write('dim_unfocused = 55\nfocus_scale = "huge"\nnot_a_setting = 1\n')   # one good key, two bad
        key(VK_F5)
        r["after_partial"] = (state(), rects()[0])

        write("dim_unfocused = 5\nfocus_scale = = 3\n")    # not valid TOML at all
        key(VK_F5)
        r["after_broken"] = (state(), rects()[0])
        alive = wall.poll() is None
        log = log_text()
        key(VK_Q)
        code = wall.wait(40)
    finally:
        user32.SetCursorPos(orig.x, orig.y)
        if wall.poll() is None:
            wall.kill()
    for k, v in r.items():
        print(f"{k:16} {v}")
    print([ln.split(" ", 1)[1][:110] for ln in log.splitlines() if "config" in ln])
    area = lambda q: q[2] * q[3]
    assert code == 0 and alive and "callback error" not in log
    assert f"settings file: {cfg}" in log
    assert r["startup"][0][0] == [0, -20, -20, -20], "the settings file should be read at startup"
    assert r["after_reload"][0][0] == [0, -40, -40, -40], "new dim_unfocused should apply"
    assert r["after_reload"][0][1] == [0.0] * 4, "max_crop = 0 should remove all cropping"
    assert area(r["after_reload"][1]) < area(r["startup"][1]), "smaller focus_scale should shrink the focused tile"
    assert "config reloaded, changed: DIM_UNFOCUSED, FOCUS_SCALE, MAX_CROP" in log
    # Partial file: the good key applies, the bad ones are skipped, and keys no longer in the file
    # (max_crop) go back to their defaults.
    assert r["after_partial"][0][0] == [0, -55, -55, -55]
    assert area(r["after_partial"][1]) > area(r["after_reload"][1]), "focus_scale back to default 1.5"
    assert "unknown setting: not_a_setting" in log and "focus_scale should be float" in log
    assert r["after_broken"] == r["after_partial"], "a file that is not valid TOML must change nothing"
    assert log.count("config reload failed") == 1 and "TOMLDecodeError" in log
    print("RELOAD TEST PASSED")


if __name__ == "__main__":
    main()
