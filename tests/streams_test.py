"""Web links. NEEDS INTERNET. TAKES OVER MOUSE, KEYBOARD AND SCREEN for ~40 s; uses the clipboard (its
text is put back afterwards).   Run: python tests/streams_test.py
Uses the pinned yt-dlp from build.ps1's download cache (downloads\\yt-dlp-*.exe; run build.ps1 once).

  A. a public HLS test stream and a YouTube video (Blender's Big Buck Bunny) on the command line: both
     open and report a picture size; the YouTube one is capped at stream_max_height (720 here)
  B. a wall with one local clip; the HLS link is put on the clipboard and Ctrl+V adds it as a second video
"""
import ctypes
import glob
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
HLS = "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8"
YOUTUBE = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
LOCAL = os.path.join(tempfile.gettempdir(), "tesserawall-p4-clips", "land0.mkv")
user32 = ctypes.windll.user32


def log_text():
    try:
        return open(LOG, encoding="utf-8", errors="replace").read()
    except FileNotFoundError:
        return ""


def launch(args, exit_after):
    if os.path.exists(LOG):
        os.remove(LOG)
    return subprocess.Popen([sys.executable, APP, "--exit-after", str(exit_after), *args])


def clipboard(text=None):
    """Read (text=None) or set the clipboard through PowerShell, which stores the text at once. (Setting it
    from a Tk window here does not work: Tk only renders clipboard text when asked, and this script is
    asleep while the wall asks, so the wall's read waited ~30 s and got nothing.)"""
    if text is None:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"], capture_output=True,
                             text=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)
        return out.stdout if out.returncode == 0 else None
    subprocess.run(["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value $env:TW_CLIP"],
                   env=dict(os.environ, TW_CLIP=text), creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)


def wait_for(pattern, secs):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        found = re.findall(pattern, log_text())
        if found:
            return found
        time.sleep(0.2)
    return []


def main():
    found = sorted(glob.glob(os.path.join(ROOT, "downloads", "yt-dlp-*.exe")))
    assert found, "no yt-dlp in downloads\\; run build.ps1 once"
    assert os.path.exists(LOCAL), "run tests/phase4_test.py once first (it makes the local clip)"
    print("WARNING: this test needs internet and controls mouse, keyboard and screen for ~40 seconds.")
    tmp = tempfile.mkdtemp(prefix="tesserawall-streams-")
    cfg = os.path.join(tmp, "config.toml")
    with open(cfg, "w", encoding="utf-8") as f:
        f.write(f"ytdlp_path = '{found[-1]}'\nstream_max_height = 720\n")
    base = ["--config", cfg, "--session", os.path.join(tmp, "s.json")]
    saved_clipboard = clipboard()
    orig = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(orig))
    r, procs = {}, []
    try:
        # A
        p = launch(base + [HLS, YOUTUBE], 25)
        procs.append(p)
        wait_for(r"aspect known [\d.]+ \((\d+)x(\d+) rot=\d+\): (https://\S+)", 20)
        time.sleep(2)
        text = log_text()
        r["A_sizes"] = {u.split("/")[2]: (int(w), int(h)) for w, h, u in
                        re.findall(r"aspect known [\d.]+ \((\d+)x(\d+) rot=\d+\): (https://\S+)", text)}
        r["A_failed"] = "could not open" in text or "couldn't play" in text.lower()
        r["A_exit"] = p.wait(40)

        # B
        p = launch(base + [LOCAL], 20)
        procs.append(p)
        rects = wait_for(r"rects=(\[.*?\]) panscan", 8)
        time.sleep(1)
        clipboard(HLS)
        if rects:
            x, y, w, h = eval(rects[-1])[0]
            user32.SetCursorPos(x + w // 2, y + h // 2)
            time.sleep(0.3)
            user32.mouse_event(2, 0, 0, 0, 0)
            user32.mouse_event(4, 0, 0, 0, 0)
            time.sleep(0.5)
            user32.keybd_event(0x11, 0, 0, 0)
            user32.keybd_event(0x56, 0, 0, 0)
            user32.keybd_event(0x56, 0, 2, 0)
            user32.keybd_event(0x11, 0, 2, 0)
        r["B_pasted"] = bool(wait_for(r"pasted 1 item\(s\)", 5))
        r["B_stream_plays"] = bool(wait_for(r"aspect known [\d.]+ \(\d+x\d+ rot=\d+\): https://test-streams", 15))
        r["B_exit"] = p.wait(40)
    finally:
        user32.SetCursorPos(orig.x, orig.y)
        for q in procs:
            if q.poll() is None:
                q.kill()
        if saved_clipboard:
            clipboard(saved_clipboard.rstrip("\r\n"))
    for k, v in r.items():
        print(f"{k:16} {v}")
    sizes = r["A_sizes"]
    assert "test-streams.mux.dev" in sizes and "www.youtube.com" in sizes, sizes
    assert sizes["www.youtube.com"][1] <= 720, "stream_max_height caps the YouTube picture"
    assert not r["A_failed"] and r["A_exit"] == 0
    assert r["B_pasted"] and r["B_stream_plays"] and r["B_exit"] == 0
    print("STREAMS TEST PASSED")


if __name__ == "__main__":
    main()
