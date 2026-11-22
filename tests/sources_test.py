"""Channels, Shorts tabs and playlists as a pool of videos, with the PACKAGED app (build.ps1 first).
NEEDS INTERNET. TAKES OVER MOUSE, KEYBOARD AND SCREEN for ~70 s.   Run: python tests/sources_test.py

Uses NASA's Shorts tab (about 40 Shorts; listed in ~2 s).
  A. the Shorts link opens source_count (4 here) random Shorts (vertical or square), each asking for 720p
     (4 videos on the screen) and capping the width, the short side of a vertical video
  B. N swaps the focused Short for another from the same tab; + adds one more from it
  C. the session saved on exit remembers each video's source
  D. --resume: the tab's list comes from the cache, and N still picks from it
"""
import ctypes
import json
import os
import re
import subprocess
import tempfile
import time

ctypes.windll.shcore.SetProcessDpiAwareness(2)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(tempfile.gettempdir(), "tesserawall.log")
APP = os.path.join(ROOT, "build", "dist", "TesseraWall", "TesseraWall.exe")
SOURCE = "https://www.youtube.com/@NASA/shorts"
user32 = ctypes.windll.user32
KEYUP, VK_N, VK_PLUS = 0x0002, 0x4E, 0xBB
SHORT_PLAYS = r"aspect known [\d.]+ \(\d+x\d+ rot=\d+\): (https://www\.youtube\.com/shorts/[\w-]+)"


def log_text():
    try:
        return open(LOG, encoding="utf-8", errors="replace").read()
    except FileNotFoundError:
        return ""


def wait_for(fn, secs):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.3)
    return fn()


def launch(args, exit_after):
    if os.path.exists(LOG):
        os.remove(LOG)
    return subprocess.Popen([APP, "--exit-after", str(exit_after), *args])


def key(vk):
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, KEYUP, 0)


def click_first_tile():
    rects = re.findall(r"rects=(\[.*?\]) panscan", log_text())
    x, y, w, h = eval(rects[-1])[0]
    user32.SetCursorPos(x + w // 2, y + h // 2)
    time.sleep(0.5)
    user32.mouse_event(2, 0, 0, 0, 0)
    user32.mouse_event(4, 0, 0, 0, 0)
    time.sleep(1.0)


def main():
    assert os.path.exists(APP), "run build.ps1 first (this test uses the packaged app)"
    print("WARNING: this test needs internet and controls mouse, keyboard and screen for ~70 seconds.")
    tmp = tempfile.mkdtemp(prefix="tesserawall-sources-")
    cfg, session = os.path.join(tmp, "config.toml"), os.path.join(tmp, "s.json")
    with open(cfg, "w", encoding="utf-8") as f:
        f.write('source_count = 4\nshow_help_hint = false\nlayout_mode = "flat"\n')
    r, procs = {}, []
    try:
        # A, B, C
        p = launch(["--config", cfg, "--session", session, SOURCE], 40)
        procs.append(p)
        playing = wait_for(lambda: set(re.findall(SHORT_PLAYS, log_text())) if
                           len(set(re.findall(SHORT_PLAYS, log_text()))) >= 4 else None, 30) or set()
        text = log_text()
        r["A_listed"] = bool(re.search(r"source https://www\.youtube\.com/@NASA/shorts: \d+ videos", text))
        r["A_opening"] = re.findall(r"opening (\d+) of (\d+) videos", text)
        r["A_playing"] = len(playing)
        r["A_quality"] = sorted(set(re.findall(r"link uses yt-dlp .*?, up to (\d+)p", text)))
        click_first_tile()
        before = set(re.findall(SHORT_PLAYS, log_text()))
        key(VK_N)
        swapped = wait_for(lambda: re.findall(
            r"file swapped: (https://\S+) -> (https://www\.youtube\.com/shorts/[\w-]+)", log_text()), 10)
        r["B_n_swapped_in_source"] = bool(swapped)
        key(VK_PLUS)
        more = wait_for(lambda: re.findall(r"one more video: (https://www\.youtube\.com/shorts/[\w-]+)", log_text()), 5)
        r["B_plus_from_source"] = bool(more)
        r["B_five_play"] = bool(wait_for(lambda: len(set(re.findall(SHORT_PLAYS, log_text())) - before) >= 2, 20))
        r["A_exit"] = p.wait(60)
        with open(session, encoding="utf-8") as f:
            videos = json.load(f)["videos"]
        r["C_saved"] = len(videos)
        r["C_pool_kept"] = all(v.get("pool") == SOURCE for v in videos)

        # D
        p = launch(["--config", cfg, "--session", session, "--resume"], 25)
        procs.append(p)
        wait_for(lambda: len(set(re.findall(SHORT_PLAYS, log_text()))) >= 3, 25)
        r["D_cached"] = "videos (cached)" in log_text()
        r["D_quality"] = sorted(set(re.findall(r"link uses yt-dlp .*?, up to (\d+)p", log_text())))
        click_first_tile()
        key(VK_N)
        r["D_n_works"] = bool(wait_for(lambda: re.findall(
            r"file swapped: \S+ -> https://www\.youtube\.com/shorts/[\w-]+", log_text()), 10))
        r["D_exit"] = p.wait(45)
    finally:
        for q in procs:
            if q.poll() is None:
                q.kill()
    for k, v in r.items():
        print(f"{k:24} {v}")
    assert r["A_listed"] and r["A_opening"] and r["A_opening"][0][0] == "4", "4 Shorts picked from the tab"
    assert r["A_playing"] >= 4, "they play"
    assert r["A_quality"] == ["720"], "4 videos on a 1440p screen ask for 720p"
    assert r["B_n_swapped_in_source"] and r["B_plus_from_source"] and r["B_five_play"]
    assert r["A_exit"] == 0 and r["C_saved"] >= 5 and r["C_pool_kept"], "the session keeps each video's source"
    assert r["D_quality"] == ["720"], "a reopened session counts all its videos for the quality"
    assert r["D_cached"] and r["D_n_works"] and r["D_exit"] == 0, "after --resume N still picks from the tab"
    print("SOURCES TEST PASSED")


if __name__ == "__main__":
    main()
