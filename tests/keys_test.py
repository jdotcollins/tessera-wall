"""The wall's menu bar from the keyboard. TAKES OVER KEYBOARD AND SCREEN for ~20 seconds.
Run: python tests/keys_test.py

F10 opens the menu bar with the keys on its first button; Right moves along it; Esc closes it and gives the
keys back to the wall (L then changes the arrangement). F10, Right, Right, Enter opens Search YouTube; Esc
cancels it. Ctrl+F opens Search YouTube directly. Ctrl+O and Ctrl+Shift+O open Windows' own file dialogs,
which a test cannot drive, so they are not covered here. Uses generated videos; no network.
"""
import ctypes
import os
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
KEYUP = 0x0002
VK = {"F10": 0x79, "Right": 0x27, "Enter": 0x0D, "Esc": 0x1B, "L": 0x4C, "Q": 0x51, "Ctrl": 0x11, "F": 0x46}


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


def press(*names):
    for n in names:
        user32.keybd_event(VK[n], 0, 0, 0)
    for n in reversed(names):
        user32.keybd_event(VK[n], 0, KEYUP, 0)
    time.sleep(0.5)


def main():
    print("WARNING: this test controls keyboard and screen for ~20 seconds.")
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    cfg = os.path.join(tempfile.mkdtemp(prefix="tesserawall-keys-"), "config.toml")
    orig = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(orig))
    user32.SetCursorPos(sw // 2, sh // 2)                  # away from the top edge: only F10 opens the bar
    r = {}
    code = None
    wall = subprocess.Popen([sys.executable, APP, "--exit-after", "40", "--config", cfg, L, L, L, L],
                            env=dict(os.environ, TESSERAWALL_NO_DIALOGS="1"))
    try:
        time.sleep(4)
        user32.mouse_event(0x0002, 0, 0, 0, 0); user32.mouse_event(0x0004, 0, 0, 0, 0)   # take the keyboard
        time.sleep(0.4)
        user32.mouse_event(0x0002, 0, 0, 0, 0); user32.mouse_event(0x0004, 0, 0, 0, 0)   # (and unpin again)
        time.sleep(0.6)
        press("F10")
        r["f10_opens"] = wait_count("top bar shown", 1)
        press("Right")
        press("Esc")
        r["esc_closes"] = wait_count("top bar hidden", 1) and wall.poll() is None
        press("L")
        r["keys_back_to_wall"] = wait_count("layout mode -> flat", 1)

        press("F10")
        wait_count("top bar shown", 2)
        press("Right")
        press("Right")                                     # Add videos, Add a folder, Search YouTube
        press("Enter")
        r["enter_on_search"] = wait_count("text dialog shown: Search YouTube", 1)
        press("Esc")                                       # cancels the dialog
        r["bar_gone_after"] = wait_count("top bar hidden", 2)

        press("Ctrl", "F")
        r["ctrl_f_search"] = wait_count("text dialog shown: Search YouTube", 2)
        press("Esc")
        time.sleep(0.6)
        press("L")
        r["keys_back_again"] = wait_count("layout mode -> grid", 1) and wall.poll() is None
        press("Q")
        code = wall.wait(30)
    finally:
        user32.SetCursorPos(orig.x, orig.y)
        if wall.poll() is None:
            wall.kill()
    for k, v in r.items():
        print(f"{k:18} {v}")
    log = log_text()
    assert code == 0 and "callback error" not in log and "Traceback" not in log
    assert r["f10_opens"], "F10 should open the menu bar"
    assert r["esc_closes"], "Esc should close the menu bar and leave the wall running"
    assert r["keys_back_to_wall"], "after Esc the wall's own keys should work again"
    assert r["enter_on_search"] and r["bar_gone_after"], "F10, Right, Right, Enter should open Search YouTube"
    assert r["ctrl_f_search"], "Ctrl+F should open Search YouTube"
    assert r["keys_back_again"]
    print("KEYS TEST PASSED")


if __name__ == "__main__":
    main()
