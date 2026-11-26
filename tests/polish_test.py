"""Top-edge menu bar and the "couldn't play" message. TAKES OVER MOUSE AND SCREEN for ~25 seconds.
Run: python tests/polish_test.py     Saves %TEMP%\\tesserawall-topbar.png for a visual check.

  1. three videos + one file that is not a video -> plain message on a video, wall keeps running
  2. pointer on the top edge -> bar appears; "Controls" opens the list; moving away hides the bar;
     "Close" quits
  3. only an unplayable file -> exits by itself and logs why (the message box is skipped in tests)
The two "Add..." buttons open native file dialogs and are not exercised.
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
LOG = os.path.join(tempfile.gettempdir(), "tesserawall.log")
APP = os.path.join(ROOT, "tesserawall.pyw")
L = "av://lavfi:testsrc2=s=1280x720:r=30"
user32 = ctypes.windll.user32


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


def move(x, y):
    user32.SetCursorPos(x - 1 if x > 0 else x + 1, y)
    time.sleep(0.05)
    user32.SetCursorPos(x, y)


def click(rect):
    x, y, w, h = rect
    move(x + w // 2, y + h // 2)
    time.sleep(0.25)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    user32.mouse_event(0x0004, 0, 0, 0, 0)


def bar_rects():
    line = re.findall(r"top bar shown: (.*)", log_text())[-1]
    return {k: tuple(map(int, v.split(","))) for k, v in (item.split("=") for item in line.split())}


def launch(args, seconds, env=None):
    if os.path.exists(LOG):
        os.remove(LOG)
    return subprocess.Popen([sys.executable, APP, "--exit-after", str(seconds), *args],
                            env=dict(os.environ, **(env or {})))


def main():
    print("WARNING: this test controls the mouse and covers the screen for ~25 seconds.")
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    tmp = tempfile.mkdtemp(prefix="tesserawall-polish-")
    bad = os.path.join(tmp, "holiday video.mp4")
    with open(bad, "w") as f:
        f.write("this is not a video\n" * 50)
    orig = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(orig))
    r, procs = {}, []
    try:
        p = launch([L, bad, L, L], 25); procs.append(p)
        r["bad_reported"] = wait_count("could not play: " + bad, 1, 8)
        wait_count("tile removed (could not play)", 1, 8)     # it keeps its place ~5 s, saying why
        time.sleep(0.5)
        r["wall_still_running"] = p.poll() is None
        r["tiles_left"] = log_text().count("tile added") - log_text().count("tile removed")

        move(sw // 2, 0)                                   # rest on the top edge
        r["bar_shown"] = wait_count("top bar shown", 1)
        time.sleep(0.4)
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"Add-Type -AssemblyName System.Drawing;$bmp=New-Object System.Drawing.Bitmap {sw},120;"
                        "$g=[System.Drawing.Graphics]::FromImage($bmp);$g.CopyFromScreen(0,0,0,0,$bmp.Size);"
                        f"$bmp.Save('{os.path.join(tempfile.gettempdir(), 'tesserawall-topbar.png')}')"],
                       creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)
        r["buttons"] = sorted(bar_rects())
        click(bar_rects()["controls"])
        r["controls_opens_list"] = wait_count("help shown", 1) and wait_count("top bar hidden", 1)
        click((sw // 2 - 5, sh // 2 - 5, 10, 10))          # a click on the list closes it
        r["list_closed"] = wait_count("help hidden", 1)

        move(sw // 3, 0)
        r["bar_shown_again"] = wait_count("top bar shown", 2)
        move(sw // 3, sh // 2)                             # wander off: bar goes away
        r["bar_hides_when_leaving"] = wait_count("top bar hidden", 2)

        move(sw * 2 // 3, 0)
        r["bar_shown_third"] = wait_count("top bar shown", 3)
        time.sleep(0.3)
        click(bar_rects()["close"])
        try:
            r["close_exit"] = p.wait(8)
        except subprocess.TimeoutExpired:
            r["close_exit"] = "still running"
        r["quit_before_timeout"] = "quit" in log_text().splitlines()[-1] or "session" in log_text().splitlines()[-1]
        first_log = log_text()

        p = launch([bad], 15, {"TESSERAWALL_NO_DIALOGS": "1"}); procs.append(p)
        try:
            r["only_bad_exit"] = p.wait(12)
        except subprocess.TimeoutExpired:
            r["only_bad_exit"] = "still running"
        r["only_bad_logged"] = "nothing playable: Couldn't play \"holiday video.mp4\"" in log_text()
    finally:
        user32.SetCursorPos(orig.x, orig.y)
        for q in procs:
            if q.poll() is None:
                q.kill()
    for line in first_log.splitlines():
        if re.search(r"top bar|help |could not|tile removed|error|Traceback|Error|quit", line):
            print("  log:", line[:150])
    for k, v in r.items():
        print(f"{k:26} {v}")
    assert "callback error" not in first_log
    assert r["bad_reported"] and r["wall_still_running"] and r["tiles_left"] == 3
    assert r["bar_shown"] and r["buttons"] == ["add", "close", "controls", "folder", "save", "search", "settings"]
    assert r["controls_opens_list"] and r["list_closed"]
    assert r["bar_shown_again"] and r["bar_hides_when_leaving"]
    assert r["bar_shown_third"] and r["close_exit"] == 0 and r["quit_before_timeout"]
    assert r["only_bad_exit"] == 0 and r["only_bad_logged"]
    assert "callback error" not in log_text()
    print("POLISH TEST PASSED")


if __name__ == "__main__":
    main()
