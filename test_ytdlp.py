"""Run with: python test_ytdlp.py   (no pytest, no network, no mpv; exits non-zero on failure)
Covers ytdlp.py: links in pasted/dropped text, mpv's options for links, and when the bundled yt-dlp may
update itself."""
import json
import os
import tempfile
import time

import config as C
import ytdlp as Y


def test_links_from_text():
    folder = tempfile.mkdtemp(prefix="tesserawall-links-")
    spaced = os.path.join(folder, "my clip.mp4")
    open(spaced, "w").close()
    text = ("watch https://www.youtube.com/watch?v=aqz-KE-bpKQ and <https://example.com/live.m3u8>\n"
            f"\"{spaced}\"\n"
            "rtmp://host/app/key  ftp://not.a.video  just words\n"
            "https://www.youtube.com/watch?v=aqz-KE-bpKQ\n")
    got = Y.links_from_text(text)
    assert got == ["https://www.youtube.com/watch?v=aqz-KE-bpKQ", "https://example.com/live.m3u8",
                   spaced, "rtmp://host/app/key"], got
    assert Y.links_from_text("") == [] and Y.links_from_text(None) == []


def test_mpv_args():
    saved = C.YTDLP_PATH, C.STREAM_MAX_HEIGHT
    try:
        C.YTDLP_PATH, C.STREAM_MAX_HEIGHT = "", 720
        assert Y.mpv_args(r"C:\videos\a.mp4") == [], "files get nothing extra"
        args = Y.mpv_args("https://www.youtube.com/watch?v=x")
        assert "--ytdl=yes" in args and any("height<=?720" in a for a in args)
        exe = os.path.join(tempfile.mkdtemp(prefix="tesserawall-ytdlp-"), "yt-dlp.exe")
        open(exe, "w").close()
        C.YTDLP_PATH = exe
        assert f"--script-opts-append=ytdl_hook-ytdl_path={exe}" in Y.mpv_args("https://twitch.tv/x")
        C.YTDLP_PATH = exe + ".missing"
        assert Y.find() is None and not any("ytdl_path" in a for a in Y.mpv_args("https://a.b/c"))
        C.STREAM_MAX_HEIGHT = 10
        assert any("height<=?144" in a for a in Y.mpv_args("https://a.b/c")), "a silly cap is raised to 144"
    finally:
        C.YTDLP_PATH, C.STREAM_MAX_HEIGHT = saved


def test_self_update_only_when_allowed():
    saved = C.UPDATE_CHECK, C.YTDLP_PATH, Y.bundled_path
    state = os.path.join(tempfile.mkdtemp(prefix="tesserawall-ytdlp-state-"), "ytdlp.json")
    try:
        exe = os.path.join(tempfile.mkdtemp(prefix="tesserawall-bundled-"), "yt-dlp.exe")
        open(exe, "w").close()
        Y.bundled_path = lambda: exe
        C.UPDATE_CHECK, C.YTDLP_PATH = False, ""
        assert Y.update_in_background(state) is None, "update checks off"
        C.UPDATE_CHECK, C.YTDLP_PATH = True, exe
        assert Y.update_in_background(state) is None, "never update a yt-dlp the user chose"
        C.YTDLP_PATH = ""
        with open(state, "w") as f:
            json.dump({"checked": time.time() - 3600}, f)
        assert Y.update_in_background(state) is None, "checked an hour ago"
        Y.bundled_path = lambda: exe + ".missing"
        os.remove(state)
        assert Y.update_in_background(state) is None, "no bundled copy (running from source)"
    finally:
        C.UPDATE_CHECK, C.YTDLP_PATH = saved[0], saved[1]
        Y.bundled_path = saved[2]


def test_clipboard_items():
    import ui
    folder = tempfile.mkdtemp(prefix="tesserawall-clip-")
    for name in ("a.mp4", "b.mkv", "notes.txt"):
        open(os.path.join(folder, name), "w").close()
    got = ui.items_from_clipboard("https://ignored.example/x.m3u8", [folder])
    assert sorted(os.path.basename(p) for p in got) == ["a.mp4", "b.mkv"], "copied files win; folders expanded"
    assert ui.items_from_clipboard("see https://a.b/c.m3u8", []) == ["https://a.b/c.m3u8"]
    assert ui.items_from_clipboard("", []) == []


def test_paste_never_blocks_the_ui():
    """A clipboard owner that never answers: the Tk thread keeps running and done(None) comes after the
    timeout; a normal read arrives as a list."""
    import threading
    import tkinter as tk
    import ui
    saved = ui.read_clipboard, ui.PASTE_TIMEOUT_MS
    hang = threading.Event()
    root = tk.Tk()
    root.withdraw()
    try:
        got, ticks = [], [0]

        def tick():
            ticks[0] += 1
            root.after(20, tick)
        tick()
        ui.read_clipboard, ui.PASTE_TIMEOUT_MS = (lambda: (hang.wait(), ("", []))[1]), 400
        ui.paste_async(root, got.append)
        start = time.monotonic()
        while not got and time.monotonic() - start < 3:
            root.update()
            time.sleep(0.01)
        assert got == [None], got
        assert ticks[0] >= 10, f"the Tk loop kept running while the clipboard hung ({ticks[0]} ticks)"
        got.clear()
        ui.read_clipboard = lambda: ("https://a.b/c.m3u8", [])
        ui.paste_async(root, got.append)
        start = time.monotonic()
        while not got and time.monotonic() - start < 3:
            root.update()
            time.sleep(0.01)
        assert got == [["https://a.b/c.m3u8"]], got
    finally:
        hang.set()
        ui.read_clipboard, ui.PASTE_TIMEOUT_MS = saved
        root.destroy()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
