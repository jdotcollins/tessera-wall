"""Controls overlay (H / ? / F1). TAKES OVER MOUSE, KEYBOARD AND SCREEN for ~15 seconds.
Run: python tests/help_test.py     Saves screenshots to %TEMP%\\tesserawall-help.png and tesserawall-hint.png.
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
L, P = "av://lavfi:testsrc2=s=1280x720:r=30", "av://lavfi:testsrc2=s=720x1280:r=30"
user32 = ctypes.windll.user32
user32.GetForegroundWindow.restype = wintypes.HWND
LEFTDOWN, LEFTUP, KEYUP = 0x0002, 0x0004, 0x0002
VK_H, VK_ESC, VK_F1, VK_SHIFT, VK_SLASH, VK_Q = 0x48, 0x1B, 0x70, 0x10, 0xBF, 0x51


def key(vk, shift=False):
    if shift:
        user32.keybd_event(VK_SHIFT, 0, 0, 0)
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, KEYUP, 0)
    if shift:
        user32.keybd_event(VK_SHIFT, 0, KEYUP, 0)
    time.sleep(0.6)


def click(x, y):
    user32.SetCursorPos(x - 1, y)
    time.sleep(0.05)
    user32.SetCursorPos(x, y)
    time.sleep(0.2)
    user32.mouse_event(LEFTDOWN, 0, 0, 0, 0)
    user32.mouse_event(LEFTUP, 0, 0, 0, 0)
    time.sleep(0.6)


def shot(name):
    path = os.path.join(tempfile.gettempdir(), name)
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
                    "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
                    "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
                    "$g=[System.Drawing.Graphics]::FromImage($bmp);$g.CopyFromScreen(0,0,0,0,$bmp.Size);"
                    f"$bmp.Save('{path}')"], creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)
    return path


def events():
    log = open(LOG, encoding="utf-8", errors="replace").read()
    return [ln.split(" ", 1)[1].split(" ")[1] for ln in log.splitlines() if " help " in ln]


def main():
    print("WARNING: this test controls mouse, keyboard and screen for ~15 seconds.")
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    orig = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(orig))
    wall = subprocess.Popen([sys.executable, APP, "--exit-after", "30", L, P, L, L],
                            env=dict(os.environ, TESSERAWALL_HELP_OPACITY="1"))   # opaque so the screenshot sees it
    try:
        time.sleep(2.2)
        shot("tesserawall-hint.png")                          # "Press H for controls" should be visible
        click(sw // 4, sh // 4)                           # pin a tile, take keyboard focus
        out = wintypes.DWORD()
        user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(out))
        assert out.value == wall.pid, "wall is not the foreground window; not sending keys"
        key(VK_H)
        after_h = events()
        shot("tesserawall-help.png")
        key(VK_ESC)                                       # closes the list only
        after_esc = events()
        alive_after_esc = wall.poll() is None
        key(VK_H)
        click(sw // 2, sh // 2)                           # a click on the list closes it
        after_click = events()
        key(VK_SLASH, shift=True)                         # ?
        key(VK_F1)
        after_keys = events()
        log_before_quit = open(LOG, encoding="utf-8", errors="replace").read()
        key(VK_Q)
        code = wall.wait(40)
    finally:
        user32.SetCursorPos(orig.x, orig.y)
        if wall.poll() is None:
            wall.kill()
    print("events:", after_keys)
    assert code == 0 and "callback error" not in log_before_quit
    assert after_h == ["shown"], after_h
    assert after_esc == ["shown", "hidden"] and alive_after_esc, "Esc should only close the list"
    assert "pin -> None" not in log_before_quit, "Esc / clicking the list must not release the pin"
    assert log_before_quit.count("pin ->") == 1, "the click on the list must not reach the wall"
    assert after_click == ["shown", "hidden", "shown", "hidden"], after_click
    assert after_keys == ["shown", "hidden"] * 3, after_keys
    print("HELP TEST PASSED")


if __name__ == "__main__":
    main()
