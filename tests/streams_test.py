"""Web links, with the PACKAGED app (build.ps1 first: it holds the shipped yt-dlp and Deno). NEEDS INTERNET.
TAKES OVER MOUSE, KEYBOARD AND SCREEN for ~70 s; uses the clipboard (its text is put back afterwards).
Run: python tests/streams_test.py

  A. a public HLS test stream, a YouTube video (Blender's Big Buck Bunny) and a live Twitch channel
     (LofiGirl, on air around the clock) on the command line: all open and report a picture size; YouTube
     is capped at stream_max_height (720 here); Twitch is not (mpv's ytdl_hook plays its first, highest
     quality whatever yt-dlp picks; see docs/DEVELOPMENT.md); the YouTube link was given Deno
  B. a wall with one local clip; the HLS link is put on the clipboard and Ctrl+V adds it as a second video
  C. recovery: Tessera Wall's own yt-dlp copy claims a newer version but is broken (a copy of where.exe).
     The YouTube link fails with it, the update check finds the copy does not run and removes it, and the
     link is opened again with the shipped yt-dlp
"""
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes

ctypes.windll.shcore.SetProcessDpiAwareness(2)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(tempfile.gettempdir(), "tesserawall.log")
APP = os.path.join(ROOT, "build", "dist", "TesseraWall", "TesseraWall.exe")
HLS = "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8"
YOUTUBE = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
TWITCH = "https://www.twitch.tv/lofigirl"
LOCAL = os.path.join(tempfile.gettempdir(), "tesserawall-p4-clips", "land0.mkv")
SIZE = r"aspect known [\d.]+ \((\d+)x(\d+) rot=\d+\): (https://\S+)"
user32 = ctypes.windll.user32


def log_text():
    try:
        return open(LOG, encoding="utf-8", errors="replace").read()
    except FileNotFoundError:
        return ""


def launch(args, exit_after):
    if os.path.exists(LOG):
        os.remove(LOG)
    return subprocess.Popen([APP, "--exit-after", str(exit_after), *args])


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


def setup(name, extra=""):
    tmp = tempfile.mkdtemp(prefix=f"tesserawall-streams-{name}-")
    with open(os.path.join(tmp, "config.toml"), "w", encoding="utf-8") as f:
        f.write("stream_max_height = 720\n" + extra)
    return tmp, ["--config", os.path.join(tmp, "config.toml"), "--session", os.path.join(tmp, "s.json")]


def main():
    assert os.path.exists(APP), "run build.ps1 first (this test uses the packaged app)"
    assert os.path.exists(LOCAL), "run tests/phase4_test.py once first (it makes the local clip)"
    print("WARNING: this test needs internet and controls mouse, keyboard and screen for ~70 seconds.")
    saved_clipboard = clipboard()
    orig = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(orig))
    r, procs = {}, []
    try:
        # A
        tmp, base = setup("a")
        p = launch(base + [HLS, YOUTUBE, TWITCH], 30)
        procs.append(p)
        end = time.monotonic() + 25
        while time.monotonic() < end and len(set(u for _, _, u in re.findall(SIZE, log_text()))) < 3:
            time.sleep(0.3)
        text = log_text()
        r["A_sizes"] = {u.split("/")[2]: (int(w), int(h)) for w, h, u in re.findall(SIZE, text)}
        r["A_failed"] = "could not open" in text or "couldn't play" in text.lower()
        r["A_exit"] = p.wait(45)
        r["A_deno_cache"] = os.path.isdir(os.path.join(tmp, "deno"))   # Deno ran, with its cache kept here

        # B
        tmp, base = setup("b")
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

        # C
        tmp, base = setup("c")
        broken = os.path.join(tmp, "yt-dlp", "yt-dlp.exe")
        os.makedirs(os.path.dirname(broken))
        shutil.copy(os.path.join(os.environ["SystemRoot"], "System32", "where.exe"), broken)
        with open(os.path.join(tmp, "ytdlp.json"), "w") as f:
            json.dump({"version": "2099.01.01", "checked": time.time()}, f)
        p = launch(base + [YOUTUBE], 35)
        procs.append(p)
        r["C_plays"] = bool(wait_for(r"aspect known [\d.]+ \(\d+x\d+ rot=\d+\): https://www.youtube", 30))
        text = log_text()
        r["C_first_try_broken"] = bool(re.search(r"link uses yt-dlp .*streams-c-.*yt-dlp\.exe", text))
        r["C_removed"] = "does not run; removed it" in text and not os.path.exists(broken)
        r["C_retried_shipped"] = bool(re.search(r"trying again with .*TesseraWall\\yt-dlp\\yt-dlp\.exe", text))
        r["C_exit"] = p.wait(45)
    finally:
        user32.SetCursorPos(orig.x, orig.y)
        for q in procs:
            if q.poll() is None:
                q.kill()
        if saved_clipboard:
            clipboard(saved_clipboard.rstrip("\r\n"))
    for k, v in r.items():
        print(f"{k:20} {v}")
    sizes = r["A_sizes"]
    assert {"test-streams.mux.dev", "www.youtube.com", "www.twitch.tv"} <= set(sizes), sizes
    assert sizes["www.youtube.com"][1] <= 720, "stream_max_height caps the YouTube picture"
    assert not r["A_failed"] and r["A_exit"] == 0 and r["A_deno_cache"]
    assert r["B_pasted"] and r["B_stream_plays"] and r["B_exit"] == 0
    assert r["C_plays"] and r["C_first_try_broken"] and r["C_removed"] and r["C_retried_shipped"]
    assert r["C_exit"] == 0
    print("STREAMS TEST PASSED")


if __name__ == "__main__":
    main()
