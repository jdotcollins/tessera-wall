"""Clean mode (I) and the on-screen switches. TAKES OVER MOUSE, KEYBOARD AND SCREEN for ~25 s.
Run: python tests/clean_test.py

Two black 60 s clips, so anything drawn over them shows up as bright screen pixels; each video's dimming
and message level are read from the wall's log ("on screen: dim=[...] osd=[...]"). (Not over the players'
own pipes: extra connections made the commands time out and threw the test's timing off.)
  A. normal: the blue outline around the video under the pointer, the progress line and time at its
     bottom, the other video dimmed (brightness -15), messages on (osd-level 1)
  B. I: outline and progress line gone, nothing dimmed, messages off (osd-level 0) - but the clean-mode
     message itself shows (it is sent at osd level 0); the top-edge menu bar does not open
  C. I again: outline, progress line, dimming and messages back
"""
import ctypes
import os
import re
import subprocess
import sys
import tempfile
import time

ctypes.windll.shcore.SetProcessDpiAwareness(2)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(tempfile.gettempdir(), "tesserawall.log")
APP = os.path.join(ROOT, "tesserawall.pyw")
BLACK = "av://lavfi:color=c=black:s=1280x720:d=60"
user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
KEYUP, VK_I = 0x0002, 0x49


def log_text():
    try:
        return open(LOG, encoding="utf-8", errors="replace").read()
    except FileNotFoundError:
        return ""


def bright(x0, y0, x1, y1, step=1):
    """How many screen pixels in the box (every step-th) are clearly not black. The box is copied in one
    BitBlt: GetPixel costs ~5 ms a pixel over video, so thousands of them took longer than the wall stays."""
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    screen = user32.GetDC(0)
    mem = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateCompatibleBitmap(screen, w, h)
    gdi32.SelectObject(mem, bmp)
    gdi32.BitBlt(mem, 0, 0, w, h, screen, x0, y0, 0x00CC0020)          # SRCCOPY

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32), ("biHeight", ctypes.c_int32),
                    ("biPlanes", ctypes.c_uint16), ("biBitCount", ctypes.c_uint16),
                    ("biCompression", ctypes.c_uint32), ("biSizeImage", ctypes.c_uint32),
                    ("biXPelsPerMeter", ctypes.c_int32), ("biYPelsPerMeter", ctypes.c_int32),
                    ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32)]
    info = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)  # top-down BGRA
    buf = (ctypes.c_ubyte * (w * h * 4))()
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(info), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(0, screen)
    data = bytes(buf)
    n = 0
    for y in range(0, h, step):
        row = y * w * 4
        for x in range(0, w, step):
            i = row + x * 4
            if data[i] + data[i + 1] + data[i + 2] > 150:
                n += 1
    return n


def states():
    """(sorted dimming, sorted message levels) of the videos, from the wall's latest log line."""
    found = re.findall(r"on screen: dim=(\[.*?\]) osd=(\[.*?\])", log_text())
    if not found:
        return None
    dims, osds = (eval(x) for x in found[-1])
    return sorted(dims), sorted(osds)


def press_i():
    user32.keybd_event(VK_I, 0, 0, 0)
    user32.keybd_event(VK_I, 0, KEYUP, 0)


def main():
    print("WARNING: this test controls mouse, keyboard and screen for ~25 seconds.")
    tmp = tempfile.mkdtemp(prefix="tesserawall-clean-")
    with open(os.path.join(tmp, "config.toml"), "w") as f:
        f.write("show_help_hint = false\nlayout_mode = \"flat\"\n")
    if os.path.exists(LOG):
        os.remove(LOG)
    p = subprocess.Popen([sys.executable, APP, "--exit-after", "30", "--config", os.path.join(tmp, "config.toml"),
                          "--session", os.path.join(tmp, "s.json"), BLACK, BLACK])
    r = {}
    try:
        end = time.monotonic() + 12
        while time.monotonic() < end and log_text().count("aspect known") < 2:
            time.sleep(0.2)
        rects = eval(re.findall(r"rects=(\[.*?\]) panscan", log_text())[-1])
        (x0, y0, w0, h0) = rects[0]
        user32.SetCursorPos(x0 + w0 // 2, y0 + h0 // 2)
        time.sleep(0.4)
        user32.mouse_event(0x0002, 0, 0, 0, 0)            # click: keyboard focus, and pins this video
        user32.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(1.5)
        ring = lambda: bright(x0, y0 + h0 // 4, x0 + 2, y0 + 3 * h0 // 4, 1)      # left gutter of the tile
        bar = lambda: bright(x0 + 4, y0 + h0 - 60, x0 + w0 // 2, y0 + h0 - 2, 6)  # bottom band of the tile

        r["A_ring"], r["A_bar"], r["A_players"] = ring(), bar(), states()
        press_i()
        time.sleep(0.3)
        r["B_own_message"] = bright(x0 + 10, y0 + 30, x0 + 610, y0 + 130, 6)   # top-left, where mpv puts messages
        time.sleep(1.5)
        r["B_ring"], r["B_bar"], r["B_players"] = ring(), bar(), states()
        before = log_text().count("top bar shown")
        user32.SetCursorPos(x0 + w0 // 2, 0)
        time.sleep(1.0)
        r["B_no_menu"] = log_text().count("top bar shown") == before
        user32.SetCursorPos(x0 + w0 // 2, y0 + h0 // 2)
        time.sleep(0.5)
        press_i()
        time.sleep(1.8)
        r["C_ring"], r["C_bar"], r["C_players"] = ring(), bar(), states()
        r["logged"] = "clean mode on" in log_text() and "clean mode off" in log_text()
        r["exit"] = p.wait(30)
    finally:
        if p.poll() is None:
            p.kill()
    for k, v in r.items():
        print(f"{k:14} {v}")
    assert r["A_ring"] > 0 and r["A_bar"] > 0, "outline and progress line show normally"
    assert r["A_players"] == ([-15, 0], [1, 1]), "the other video dimmed; messages on"
    assert r["B_own_message"] > 0, "the clean-mode message itself shows"
    assert r["B_ring"] == 0 and r["B_bar"] == 0, "clean: no outline, no progress line"
    assert r["B_players"] == ([0, 0], [0, 0]), "clean: nothing dimmed, messages off"
    assert r["B_no_menu"], "clean: no menu bar at the top edge"
    assert r["C_ring"] > 0 and r["C_bar"] > 0 and r["C_players"] == ([-15, 0], [1, 1]), "I again: all back"
    assert r["logged"] and r["exit"] == 0
    print("CLEAN TEST PASSED")


if __name__ == "__main__":
    main()
