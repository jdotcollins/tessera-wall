"""The menu bar's Recent list. TAKES OVER MOUSE, KEYBOARD AND SCREEN for ~25 seconds.
Run: python tests/recent_test.py

Uses its own settings file (--config) with two remembered sources, one pinned. Opens the list from the
top-edge menu bar and, with real clicks: pins the unpinned one with its star (the list stays open), unpins
it again, then clicks the row itself, which picks it (the list and the bar close). Opens the list again
and picks with Down and Enter, then once more and closes it with Esc. Checks sources.json and the log
after each step. No network is needed: a pick only has to reach the wall's queue (it is logged first).
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
import sources  # noqa: E402

LOG = os.path.join(tempfile.gettempdir(), "tesserawall.log")
APP = os.path.join(ROOT, "tesserawall.pyw")
L = "av://lavfi:testsrc2=s=1280x720:r=30"
NASA = "https://www.youtube.com/@NASA/videos"
LOFI = sources.search_url("lofi hip hop")
user32 = ctypes.windll.user32
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


def click_at(x, y):
    user32.SetCursorPos(x - 1, y)
    time.sleep(0.05)
    user32.SetCursorPos(x, y)
    time.sleep(0.25)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.15)


def rects(prefix):
    line = re.findall(prefix + r": (.*)", log_text())[-1]
    return {k: tuple(map(int, v.split(","))) for k, v in (item.rsplit("=", 1) for item in line.split())}


def key(vk):
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, KEYUP, 0)
    time.sleep(0.4)


def pinned(cfg):
    return {d["source"]: bool(d.get("pinned")) for d in sources.recent(cfg)}


def open_list(sw, n):
    """Rest the pointer on the top edge, click Recent; returns the rows' rectangles."""
    user32.SetCursorPos(sw // 2, 300); time.sleep(0.3)
    user32.SetCursorPos(sw // 2, 1); time.sleep(0.05); user32.SetCursorPos(sw // 2, 0)
    assert wait_count("top bar shown", n), "menu bar did not appear"
    time.sleep(0.3)
    x, y, w, h = rects("top bar shown")["recent"]
    click_at(x + w // 2, y + h // 2)
    assert wait_count("recent list rows", n), "the Recent list did not open"
    time.sleep(0.3)
    return rects("recent list rows")


def main():
    print("WARNING: this test controls mouse, keyboard and screen for ~25 seconds.")
    sw = user32.GetSystemMetrics(0)
    cfg = os.path.join(tempfile.mkdtemp(prefix="tesserawall-recent-"), "config.toml")
    sources.remember(cfg, NASA, "")
    time.sleep(0.02)
    sources.remember(cfg, LOFI, "lofi hip hop")
    sources.set_pinned(cfg, NASA, True)                    # listed: NASA (pinned) first, then lofi
    orig = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(orig))
    r = {}
    code = None
    wall = subprocess.Popen([sys.executable, APP, "--exit-after", "40", "--config", cfg, L, L, L, L],
                            env=dict(os.environ, TESSERAWALL_NO_DIALOGS="1"))
    try:
        time.sleep(3.5)
        rows = open_list(sw, 1)
        r["rows"] = sorted(k for k in rows if k.startswith("r"))
        star = rows["star"][0]                             # the star's centre, from the row's left edge
        hidden = log_text().count("top bar hidden")

        x, y, w, h = rows["r1"]                            # lofi, not pinned yet
        click_at(x + star, y + h // 2)
        r["star_pins"] = wait_count(f"recent menu pinned {LOFI}", 1) and pinned(cfg).get(LOFI) is True
        r["list_stays_open"] = log_text().count("top bar hidden") == hidden
        click_at(x + star, y + h // 2)
        r["star_unpins"] = wait_count(f"recent menu unpinned {LOFI}", 1) and pinned(cfg).get(LOFI) is False
        r["nasa_still_pinned"] = pinned(cfg).get(NASA) is True

        click_at(x + w * 2 // 3, y + h // 2)               # the row itself: picks it
        r["row_picks"] = wait_count(f"recent picked: {LOFI}", 1)
        r["closes_after_pick"] = wait_count("top bar hidden", hidden + 1)

        time.sleep(0.6)
        open_list(sw, 2)
        key(0x28)                                          # Down: the first row
        key(0x0D)                                          # Enter picks it
        r["enter_picks"] = wait_count(f"recent picked: {NASA}", 1)

        time.sleep(0.6)
        open_list(sw, 3)
        key(0x1B)                                          # Esc closes the list, not the wall
        time.sleep(0.6)
        r["esc_closes_list"] = wall.poll() is None and log_text().count("recent picked") == 2
        user32.SetCursorPos(sw // 2, 500); time.sleep(0.6)
        key(0x51)                                          # Q quits the wall
        code = wall.wait(45)
    finally:
        user32.SetCursorPos(orig.x, orig.y)
        if wall.poll() is None:
            wall.kill()
    for k, v in r.items():
        print(f"{k:22} {v}")
    log = log_text()
    assert code == 0 and "callback error" not in log and "Traceback" not in log
    assert r["rows"] == ["r0", "r1"]
    assert r["star_pins"] and r["list_stays_open"], "the star should pin without closing the list"
    assert r["star_unpins"] and r["nasa_still_pinned"]
    assert r["row_picks"] and r["closes_after_pick"], "clicking the row should pick it and close the list"
    assert r["enter_picks"], "Down and Enter should pick the first row"
    assert r["esc_closes_list"], "Esc should close the list and leave the wall running"
    print("RECENT TEST PASSED")


if __name__ == "__main__":
    main()
