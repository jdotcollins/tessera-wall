"""A failed video keeps its place and says why; the right-click menu; "Loading…" on a link on its way.

TAKES OVER THE MOUSE, KEYBOARD AND SCREEN for ~40 seconds. Run: python tests/batch2_test.py
Part B serves a clip from a local web server (127.0.0.1) as a link; nothing leaves the PC.
"""
import ctypes
import http.server
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from ctypes import wintypes

ctypes.windll.shcore.SetProcessDpiAwareness(2)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

LOG = os.path.join(tempfile.gettempdir(), "tesserawall.log")
APP = os.path.join(ROOT, "tesserawall.pyw")
user32 = ctypes.windll.user32
LEFTDOWN, LEFTUP, RIGHTDOWN, RIGHTUP, KEYUP = 0x0002, 0x0004, 0x0008, 0x0010, 0x0002
VK_ESC, VK_Q = 0x1B, 0x51


def make_clip(path, seconds=6, fps=10, w=320, h=180, luma=None):
    with open(path, "wb") as f:
        f.write(f"YUV4MPEG2 W{w} H{h} F{fps}:1 Ip A1:1 C420jpeg\n".encode())
        chroma = bytes([128]) * (w * h // 2)
        for i in range(seconds * fps):
            if luma is not None:
                f.write(b"FRAME\n" + bytes([luma]) * (w * h) + chroma)
            else:
                bar = w * i // (seconds * fps)
                f.write(b"FRAME\n" + (bytes([235]) * bar + bytes([60]) * (w - bar)) * h + chroma)


user32.GetWindow.restype = wintypes.HWND
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def window_classes(pid):
    """Class names of every visible window inside the wall's windows (process pid), top of the z-order
    first. Screen capture cannot be trusted here: on some PCs it does not see mpv's video at all, so what is
    on top of a tile is read from the window tree. Tk labels are "Static"; a player's window is "mpv"."""
    tops, out = [], []

    def each(h, _):
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid and user32.IsWindowVisible(h):
            tops.append(h)
        return True
    user32.EnumWindows(EnumProc(each), 0)

    def walk(h):
        c = user32.GetWindow(h, 5)                         # GW_CHILD
        while c:
            if user32.IsWindowVisible(c):
                name = ctypes.create_unicode_buffer(64)
                user32.GetClassNameW(c, name, 64)
                out.append(name.value)
                walk(c)
            c = user32.GetWindow(c, 2)                     # GW_HWNDNEXT
    for h in tops:
        walk(h)
    return out


def move(x, y):
    user32.SetCursorPos(x - 1, y)
    time.sleep(0.05)
    user32.SetCursorPos(x, y)


def click(x, y, right=False):
    move(x, y)
    time.sleep(0.15)
    user32.mouse_event(RIGHTDOWN if right else LEFTDOWN, 0, 0, 0, 0)
    user32.mouse_event(RIGHTUP if right else LEFTUP, 0, 0, 0, 0)
    time.sleep(0.6)


def key(vk):
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, KEYUP, 0)
    time.sleep(0.3)


def log_text():
    try:
        return open(LOG, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""


def wait_for(pattern, timeout=10.0, after=0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        m = list(re.finditer(pattern, log_text()))
        if len(m) > after:
            return m[after]
        time.sleep(0.1)
    return None


def rects():
    return eval(re.findall(r"rects=(\[.*?\]) panscan", log_text())[-1])


def menu(after=0):
    m = wait_for(r"tile menu for (\d+): (.*)", 5, after)
    if not m:
        return None, {}
    return int(m.group(1)), {k: tuple(map(int, v.split(","))) for k, v in (p.split("=") for p in m.group(2).split())}


def centre(r):
    x, y, w, h = r
    return x + w // 2, y + h // 2


def part_a(folder, r):
    good_a, good_b = os.path.join(folder, "clip a.y4m"), os.path.join(folder, "clip b.y4m")
    bad = os.path.join(folder, "broken video.mp4")
    make_clip(good_a), make_clip(good_b)
    with open(bad, "w") as f:
        f.write("this is not a video\n" * 50)
    conf = os.path.join(tempfile.mkdtemp(prefix="tesserawall-batch2-"), "config.toml")
    with open(conf, "w") as f:
        f.write('layout_mode = "flat"\nshow_help_hint = false\n')
    if os.path.exists(LOG):
        os.remove(LOG)
    wall = subprocess.Popen([sys.executable, APP, "--exit-after", "40", "--config", conf, good_a, bad, good_b])
    try:
        failed = wait_for(r"tile failed \(.*\): .*broken video", 10)
        t_fail = time.monotonic()
        r["failed_logged"] = bool(failed)
        r["shown_in_place"] = bool(wait_for(r"could not play: .*broken video.* \(shown in its place\)", 3))
        time.sleep(0.5)
        text = log_text()
        r["tiles_while_shown"] = text.count("tile added") - text.count("tile removed")

        # right-click the failed tile: its menu has only Close; Esc leaves it
        click(*centre(rects()[1]), right=True)
        i, items = menu()
        r["failed_menu"] = (i, sorted(items))
        key(VK_ESC)
        gone = wait_for(r"tile removed \(could not play\): .*broken video", 8)
        r["gone_after_s"] = round(time.monotonic() - t_fail, 1) if gone else None
        time.sleep(0.6)

        # right-click a playing video: its menu, then "Keep the sound on this video"
        click(*centre(rects()[0]), right=True)
        i, items = menu(1)
        r["menu"] = (i, list(items))
        if "keep-the-sound-on-this-video" in items:
            click(*centre(items["keep-the-sound-on-this-video"]))
            r["sound_kept"] = bool(wait_for(r"audio lock -> 0", 3))

        # right-click near the bottom-right corner: the menu stays on the screen
        x, y, w, h = rects()[-1]
        click(x + w - 6, y + h - 6, right=True)
        _, items = menu(2)
        sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        r["corner_menu_on_screen"] = bool(items) and all(0 <= bx and bx + bw <= sw and by + bh <= sh
                                                         for bx, by, bw, bh in items.values())
        key(VK_ESC)
        key(VK_Q)
        r["exit"] = wall.wait(20)
    finally:
        if wall.poll() is None:
            wall.kill()
    r["callback_error"] = "callback error" in log_text()


class Slow(http.server.BaseHTTPRequestHandler):
    clip = b""

    def do_HEAD(self):
        time.sleep(1.5)
        self.send_response(200)
        self.send_header("Content-Type", "video/x-yuv4mpeg")
        self.send_header("Content-Length", str(len(self.clip)))
        self.end_headers()

    def do_GET(self):
        self.do_HEAD()
        try:
            self.wfile.write(self.clip)
        except OSError:
            pass

    def log_message(self, *a):
        pass


def part_b(folder, r):
    path = os.path.join(folder, "dark.y4m")
    make_clip(path, seconds=20, luma=16)
    Slow.clip = open(path, "rb").read()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/dark.y4m"
    conf = os.path.join(tempfile.mkdtemp(prefix="tesserawall-batch2b-"), "config.toml")
    with open(conf, "w") as f:
        f.write("show_help_hint = false\n")
    if os.path.exists(LOG):
        os.remove(LOG)
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    move(sw - 2, sh // 2)                                  # out of the way
    wall = subprocess.Popen([sys.executable, APP, "--exit-after", "40", "--config", conf, url])
    try:
        r["loading_logged"] = bool(wait_for(r"loading note: http://127\.0\.0\.1", 10))
        time.sleep(0.8)
        r["windows_while_loading"] = window_classes(wall.pid)
        r["played"] = bool(wait_for(r"aspect known .*dark\.y4m|aspect unknown, keeping 16:9: .*dark", 25))
        time.sleep(1.5)
        r["windows_while_playing"] = window_classes(wall.pid)
        wall.terminate()
        wall.wait(10)
    finally:
        if wall.poll() is None:
            wall.kill()
        server.shutdown()


def main():
    print("WARNING: this test controls mouse, keyboard and screen for ~40 seconds.")
    folder = tempfile.mkdtemp(prefix="tesserawall-batch2-clips-")
    orig = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(orig))
    r = {}
    try:
        part_a(folder, r)
        part_b(folder, r)
    finally:
        user32.mouse_event(LEFTUP, 0, 0, 0, 0)
        user32.SetCursorPos(orig.x, orig.y)
    for k, v in r.items():
        print(f"{k:24} {v}")
    assert r["failed_logged"] and r["shown_in_place"], "the broken file should fail in its place"
    assert r["tiles_while_shown"] == 3, "the failed tile should keep its place for a while"
    assert r["failed_menu"] == (1, ["close"]), "a failed tile's menu has only Close"
    assert r["gone_after_s"] is not None and 4.0 <= r["gone_after_s"] <= 7.5, "it should go after about 5 s"
    assert r["menu"][0] == 0 and r["menu"][1][:2] == ["pause", "pin"] and "close" in r["menu"][1]
    assert r.get("sound_kept"), "the menu's sound item should lock the sound to that video"
    assert r["corner_menu_on_screen"], "a menu opened in the corner must stay on the screen"
    assert r["exit"] == 0 and not r["callback_error"]
    assert r["loading_logged"] and r["played"]
    loading, playing = r["windows_while_loading"], r["windows_while_playing"]
    assert loading.count("Static") == 2 and "mpv" not in loading, "Loading… should be all there is on the tile"
    assert "mpv" in playing and "Static" not in playing, "once it plays, the note is gone and the player shows"
    print("BATCH2 TEST PASSED")


if __name__ == "__main__":
    main()
