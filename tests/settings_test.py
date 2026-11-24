"""Settings window. TAKES OVER MOUSE, KEYBOARD AND SCREEN for ~25 seconds.
Run: python tests/settings_test.py     Saves %TEMP%\\tesserawall-settings.png for a visual check.

Uses its own settings file (--config). Opens the window from the top-edge menu bar and, with real
clicks, moves a slider, picks a layout, clears a checkbox, resets, and closes it; after each step it
checks both the settings file and the running wall. Then opens it with S and closes it with Esc.
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
KEYUP = 0x0002


def log_text():
    try:
        return open(LOG, encoding="utf-8", errors="replace").read()
    except FileNotFoundError:
        return ""


def wait_count(text, n, seconds=6.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if log_text().count(text) >= n:
            return True
        time.sleep(0.1)
    return False


def click_at(x, y, times=1):
    user32.SetCursorPos(x - 1, y)
    time.sleep(0.05)
    user32.SetCursorPos(x, y)
    time.sleep(0.2)
    for _ in range(times):
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        user32.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(0.12)


def click(rect):
    x, y, w, h = rect
    click_at(x + w // 2, y + h // 2)


def rects(prefix):
    line = re.findall(prefix + r": (.*)", log_text())[-1]
    return {k: tuple(map(int, v.split(","))) for k, v in (item.rsplit("=", 1) for item in line.split())}


def key(vk):
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, KEYUP, 0)
    time.sleep(0.6)


def main():
    print("WARNING: this test controls mouse, keyboard and screen for ~25 seconds.")
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    cfg = os.path.join(tempfile.mkdtemp(prefix="tesserawall-settings-"), "config.toml")
    with open(cfg, "w", encoding="utf-8") as f:
        f.write("# my own note\nmax_videos = 9\n")
    read_cfg = lambda: open(cfg, encoding="utf-8").read()
    orig = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(orig))
    r = {}
    wall = subprocess.Popen([sys.executable, APP, "--exit-after", "40", "--config", cfg, L, P, L, L],
                            env=dict(os.environ, TESSERAWALL_NO_DIALOGS="1"))
    try:
        time.sleep(3.5)
        ipc = [MpvIpc(rf"\\.\pipe\tesserawall-{wall.pid}-{n}", timeout=3) for n in (1, 2, 3, 4)]
        dims = lambda: sorted(c.get("brightness") for c in ipc)
        click_at(sw // 4, sh // 4)                          # pin a tile: one focused, three dimmed
        time.sleep(0.6)
        r["dims_before"] = dims()

        user32.SetCursorPos(sw // 2, 1); time.sleep(0.05); user32.SetCursorPos(sw // 2, 0)
        assert wait_count("top bar shown", 1), "menu bar did not appear"
        time.sleep(0.3)
        click(rects("top bar shown")["settings"])
        r["opened_from_bar"] = wait_count("settings shown", 1)
        time.sleep(0.5)
        w = rects("settings shown")
        x0 = min(v[0] for v in w.values()) - 60; y0 = min(v[1] for v in w.values()) - 150
        x1 = max(v[0] + v[2] for v in w.values()) + 60; y1 = max(v[1] + v[3] for v in w.values()) + 60
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"Add-Type -AssemblyName System.Drawing;$bmp=New-Object System.Drawing.Bitmap {x1 - x0},{y1 - y0};"
                        f"$g=[System.Drawing.Graphics]::FromImage($bmp);$g.CopyFromScreen({x0},{y0},0,0,$bmp.Size);"
                        f"$bmp.Save('{os.path.join(tempfile.gettempdir(), 'tesserawall-settings.png')}')"],
                       creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)

        sx, sy, swid, shei = w["DIM_UNFOCUSED"]             # click a third of the way along 0..60: 20
        unit = int(re.findall(r"settings text unit (\d+)", log_text())[-1])
        pad = unit // 2                                     # the slider's inner margin (u // 2 in the app)
        click_at(sx + pad + round((swid - 2 * pad) / 3), sy + shei // 2)
        time.sleep(1.0)
        r["dims_after_slider"] = dims()
        r["file_after_slider"] = re.findall(r"(?m)^dim_unfocused = (\d+)$", read_cfg())

        click(w["LAYOUT_MODE:grid"])
        time.sleep(0.8)
        last = re.findall(r"relayout .*", log_text())[-1]
        r["grid_live"] = " grid " in last and len(set(re.findall(r"\(\d+, \d+, (\d+, \d+)\)", last))) == 1
        r["file_has_grid"] = 'layout_mode = "grid"' in read_cfg()

        cx, cy, cw, ch = w["SEEKBAR_SHOW_NAME"]
        click_at(cx + 14, cy + ch // 2)
        time.sleep(0.6)
        r["file_name_off"] = "\nseekbar_show_name = false" in read_cfg()
        r["own_lines_kept"] = read_cfg().startswith("# my own note\nmax_videos = 9\n")

        click(w["reset"])
        time.sleep(1.2)
        r["dims_after_reset"] = dims()
        r["file_after_reset"] = ("# dim_unfocused = 15" in read_cfg() and '# layout_mode = "grow"' in read_cfg()
                                 and "max_videos = 9" in read_cfg())
        r["grow_after_reset"] = " grid " not in re.findall(r"relayout .*", log_text())[-1]

        click(w["done"])
        r["done_closes"] = wait_count("settings hidden", 1)
        time.sleep(0.4)
        out = wintypes.DWORD()
        user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(out))
        r["wall_foreground"] = out.value == wall.pid
        if r["wall_foreground"]:
            key(0x53)                                       # S opens it
            r["s_opens"] = wait_count("settings shown", 2)
            key(0x20)                                       # Space inside the window must not pause a video
            r["pauses_while_open"] = [c.get("pause") for c in ipc]
            key(0x1B)                                       # Esc closes only the settings window
            r["esc_closes"] = wait_count("settings hidden", 2) and wall.poll() is None
            key(0x51)                                       # Q quits the wall
        code = wall.wait(45)
    finally:
        user32.SetCursorPos(orig.x, orig.y)
        if wall.poll() is None:
            wall.kill()
    for k, v in r.items():
        print(f"{k:22} {v}")
    log = log_text()
    assert code == 0 and "callback error" not in log and "settings window:" not in log
    assert r["dims_before"] == [-15, -15, -15, 0]
    assert r["opened_from_bar"]
    assert "settings dark title bar: True" in log, "the settings window should get a dark title bar"
    assert r["dims_after_slider"] == [-20, -20, -20, 0] and r["file_after_slider"] == ["20"], "slider should apply live"
    assert r["grid_live"] and r["file_has_grid"]
    assert r["file_name_off"] and r["own_lines_kept"]
    assert r["dims_after_reset"] == [-15, -15, -15, 0] and r["file_after_reset"] and r["grow_after_reset"]
    assert r["done_closes"] and r["wall_foreground"]
    assert r["s_opens"] and r["esc_closes"]
    assert r["pauses_while_open"] == [False] * 4, "keys typed in the settings window must not reach the wall"
    print("SETTINGS TEST PASSED")


if __name__ == "__main__":
    main()
