"""Channels, Shorts tabs and playlists as a pool of videos, with the PACKAGED app (build.ps1 first).
NEEDS INTERNET. TAKES OVER MOUSE, KEYBOARD AND SCREEN for ~2 minutes.   Run: python tests/sources_test.py

Uses NASA's Shorts tab (about 40 Shorts; listed in ~2 s).
  A. the Shorts link opens source_count (4 here) random Shorts (vertical or square), each asking for 720p
     (4 videos on the screen) and capping the width, the short side of a vertical video
  B. each Short's successor is looked up ahead ("next ready"); N then switches to a prepared one, whose
     picture is there within 1.5 s (against 3-4 s for a lookup at the moment); + adds one more
  E. a Short that reaches its end is replaced by another from the tab (source_when_finished = random,
     the default), from a prepared lookup, again within 1.5 s
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


def stamp(line):
    """Seconds of the day from a log line's HH:MM:SS.mmm prefix; -1 for a line without one."""
    try:
        h, m, s = line[:12].split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    except ValueError:
        return -1


def gap_after(marker_re, text):
    """Seconds from the first line matching marker_re (which captures the new URL) to that URL's picture."""
    for line in text.splitlines():
        m = re.search(marker_re, line)
        if m:
            url = m.group(1)
            later = [l for l in text.splitlines() if stamp(l) >= stamp(line) and
                     re.search(r"aspect known .*: " + re.escape(url) + r"$", l)]
            return round(stamp(later[0]) - stamp(line), 2) if later else None
    return None


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
    print("WARNING: this test needs internet and controls mouse, keyboard and screen for ~2 minutes.")
    tmp = tempfile.mkdtemp(prefix="tesserawall-sources-")
    cfg, session = os.path.join(tmp, "config.toml"), os.path.join(tmp, "s.json")
    with open(cfg, "w", encoding="utf-8") as f:
        f.write('source_count = 4\nshow_help_hint = false\nlayout_mode = "flat"\n')
    r, procs = {}, []
    try:
        # A, B, C
        p = launch(["--config", cfg, "--session", session, SOURCE], 110)
        procs.append(p)
        playing = wait_for(lambda: set(re.findall(SHORT_PLAYS, log_text())) if
                           len(set(re.findall(SHORT_PLAYS, log_text()))) >= 4 else None, 30) or set()
        text = log_text()
        r["A_listed"] = bool(re.search(r"source https://www\.youtube\.com/@NASA/shorts: \d+ videos", text))
        r["A_opening"] = re.findall(r"opening (\d+) of (\d+) videos", text)
        r["A_playing"] = len(playing)
        r["A_quality"] = sorted(set(re.findall(r"link uses yt-dlp .*?, up to (\d+)p", text)))
        r["B_prepared"] = len(wait_for(lambda: re.findall(r"next ready: ", log_text()) if
                                       len(re.findall(r"next ready: ", log_text())) >= 4 else None, 30) or [])
        click_first_tile()
        before = set(re.findall(SHORT_PLAYS, log_text()))
        key(VK_N)
        swapped = wait_for(lambda: re.findall(
            r"file swapped: (https://\S+) -> (https://www\.youtube\.com/shorts/[\w-]+)", log_text()), 10)
        r["B_n_swapped_in_source"] = bool(swapped)
        time.sleep(3)
        r["B_n_prepared"] = "(prepared)" in log_text()
        r["B_n_gap"] = gap_after(r"file swapped: \S+ -> (https://www\.youtube\.com/shorts/[\w-]+) \(prepared\)",
                                 log_text())
        key(VK_PLUS)
        more = wait_for(lambda: re.findall(r"one more video: (https://www\.youtube\.com/shorts/[\w-]+)", log_text()), 5)
        r["B_plus_from_source"] = bool(more)
        r["B_five_play"] = bool(wait_for(lambda: len(set(re.findall(SHORT_PLAYS, log_text())) - before) >= 2, 20))
        user32.SetCursorPos(0, 0)                      # point at nothing: "move on" skips the pointed-at video
        ended = wait_for(lambda: re.search(r"finished \(random\): ", log_text()), 75)
        time.sleep(3)
        text = log_text()
        r["E_finished"] = bool(ended)
        if ended:
            after = text[text.rfind(chr(10), 0, ended.start()) + 1:]   # from the start of that line
            m = re.search(r"file swapped: \S+ -> (https://www\.youtube\.com/shorts/[\w-]+)( \(prepared\))?", after)
            r["E_replaced_prepared"] = bool(m and m.group(2))
            r["E_gap"] = gap_after(r"file swapped: \S+ -> (https://www\.youtube\.com/shorts/[\w-]+) \(prepared\)",
                                   after)
        r["A_exit"] = p.wait(120)
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
    assert r["B_prepared"] >= 4, "each Short's successor is looked up ahead"
    assert r["B_n_swapped_in_source"] and r["B_n_prepared"] and r["B_n_gap"] is not None and r["B_n_gap"] < 1.5
    assert r["B_plus_from_source"] and r["B_five_play"]
    assert r["E_finished"] and r["E_replaced_prepared"] and r["E_gap"] is not None and r["E_gap"] < 1.5, \
        "a Short that ends is replaced by a prepared one from the tab"
    assert r["A_exit"] == 0 and r["C_saved"] >= 5 and r["C_pool_kept"], "the session keeps each video's source"
    assert r["D_quality"] == ["720"], "a reopened session counts all its videos for the quality"
    assert r["D_cached"] and r["D_n_works"] and r["D_exit"] == 0, "after --resume N still picks from the tab"
    print("SOURCES TEST PASSED")


if __name__ == "__main__":
    main()
