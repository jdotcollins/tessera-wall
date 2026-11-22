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
        C.YTDLP_PATH = ""
        C.STREAM_MAX_HEIGHT = 10
        assert any("height<=?144" in a for a in Y.mpv_args("https://a.b/c")), "a silly cap is raised to 144"
    finally:
        C.YTDLP_PATH, C.STREAM_MAX_HEIGHT = saved


class FakeYtdlp:
    """A temporary app folder with a shipped yt-dlp, a temporary state folder, and Y.run replaced: each
    exe "reports" the version in self.versions; --update-to moves the own copy to self.latest."""

    def __init__(self, shipped="2026.10.26", latest="2026.10.26"):
        self.app = tempfile.mkdtemp(prefix="tesserawall-app-")
        self.state = tempfile.mkdtemp(prefix="tesserawall-state-")
        os.makedirs(os.path.join(self.app, "yt-dlp"))
        self.shipped = os.path.join(self.app, "yt-dlp", "yt-dlp.exe")
        with open(self.shipped, "w") as f:
            f.write("shipped")
        with open(os.path.join(self.app, "yt-dlp", "version.txt"), "w") as f:
            f.write(shipped)
        self.versions = {self.shipped: shipped}
        self.latest, self.broken_update, self.calls = latest, False, []

    def run(self, args, timeout):
        exe = args[0]
        self.calls.append(args[1:])
        if args[1] == "--version":
            v = self.versions.get(exe) or (self.versions.get(self.shipped) if open(args[0]).read() == "shipped" else None)
            return (0, v + "\n") if v and v != "broken" else (1, "error")
        if args[1] == "--update-to":
            self.versions[exe] = "broken" if self.broken_update else self.latest
            return 0, f"Updated yt-dlp to stable@{self.latest}\n"
        return 1, "?"

    def __enter__(self):
        self.saved = C.app_dir, C.UPDATE_CHECK, C.YTDLP_PATH, Y.run, Y._state_dir
        C.app_dir, C.UPDATE_CHECK, C.YTDLP_PATH, Y.run = (lambda: self.app), True, "", self.run
        Y.init(self.state)
        return self

    def __exit__(self, *exc):
        C.app_dir, C.UPDATE_CHECK, C.YTDLP_PATH, Y.run, Y._state_dir = self.saved


def test_warm_hosts():
    """Which HTTPS hosts get a Windows-checked handshake before a link plays (fresh-PC root certificates)."""
    assert Y.warm_hosts("https://www.youtube.com/watch?v=x") == ["www.youtube.com", "redirector.googlevideo.com"]
    assert Y.warm_hosts("https://m.youtube.com/watch?v=x")[1:] == ["redirector.googlevideo.com"]
    assert Y.warm_hosts("https://youtu.be/x") == ["youtu.be", "www.youtube.com", "redirector.googlevideo.com"]
    assert Y.warm_hosts("https://www.twitch.tv/lofigirl") == ["www.twitch.tv", "usher.ttvnw.net"]
    assert Y.warm_hosts("https://cdn.example.com:8443/a.m3u8") == ["cdn.example.com"]
    assert Y.warm_hosts("https://notyoutube.com/x") == ["notyoutube.com"], "only the site itself or its subdomains"
    assert Y.warm_hosts("http://a.b/c") == [] and Y.warm_hosts("rtmp://a/b") == [] and Y.warm_hosts(r"C:\a.mp4") == []


def test_versions():
    assert Y.parse_version("2026.10.26") == (2026, 10, 26)
    assert Y.parse_version("2026.11.03.233012") > Y.parse_version("2026.11.03")
    assert Y.parse_version("garbage") is None and Y.parse_version(None) is None


def test_updates_only_when_allowed():
    with FakeYtdlp() as f:
        C.UPDATE_CHECK = False
        assert Y.update_in_background() is None and not Y.recover(), "update checks off"
        C.UPDATE_CHECK, C.YTDLP_PATH = True, f.shipped
        assert Y.update_in_background() is None and not Y.recover(), "never update a yt-dlp the user chose"
        C.YTDLP_PATH = ""
        Y.write_state(checked=time.time() - 3600)
        assert Y.update_in_background() is None, "checked an hour ago"
        os.remove(f.shipped)
        Y.write_state(checked=0)
        assert Y.update_in_background() is None, "no shipped copy (running from source)"
        assert f.calls == []


def test_refresh_uses_the_newer_copy_and_never_touches_the_shipped_one():
    with FakeYtdlp(shipped="2026.10.26", latest="2026.11.12") as f:
        assert Y.find() == f.shipped, "before any update: the shipped copy"
        assert Y.refresh() is True
        own = Y.own_path()
        assert os.path.isfile(own) and Y.read_state()["version"] == "2026.11.12"
        assert Y.find() == own and Y.candidates() == [own, f.shipped]
        assert open(f.shipped).read() == "shipped" and f.versions[f.shipped] == "2026.10.26"
        assert Y.refresh() is False, "nothing new"


def test_newer_release_wins_over_an_older_own_copy():
    """Installing a Tessera Wall release whose shipped yt-dlp is newer than the own copy: no downgrade."""
    with FakeYtdlp(shipped="2026.10.26", latest="2026.11.01") as f:
        Y.refresh()
        with open(os.path.join(f.app, "yt-dlp", "version.txt"), "w") as v:
            v.write("2026.12.01")
        f.versions[f.shipped] = "2026.12.01"
        assert Y.find() == f.shipped
        f.latest = "2026.12.01"
        Y.refresh()                                   # starts the own copy again from the newer shipped one
        assert Y.read_state()["version"] == "2026.12.01" and Y.find() == f.shipped, "a tie keeps the shipped copy"


def test_nothing_new_is_no_change():
    """First check with nothing newer: the own copy is set up, but links still use the same yt-dlp."""
    with FakeYtdlp(shipped="2026.10.26", latest="2026.10.26") as f:
        assert Y.refresh() is False and Y.find() == f.shipped and os.path.isfile(Y.own_path())


def test_a_broken_update_is_removed():
    with FakeYtdlp(latest="2026.11.12") as f:
        f.broken_update = True
        Y.refresh()
        assert not os.path.exists(Y.own_path()) and Y.read_state()["version"] is None
        assert Y.candidates() == [f.shipped]


def test_recover_is_rate_limited():
    with FakeYtdlp(latest="2026.11.12"):
        assert Y.recover() is True
        assert Y.recover() is False, "tried a moment ago"
        Y.write_state(recovered=time.time() - Y.RECOVER_INTERVAL - 1)
        assert Y.recover() is False, "allowed again, but nothing newer"


def test_link_env_and_deno():
    with FakeYtdlp() as f:
        assert Y.mpv_env(r"C:\a.mp4") is None
        env = Y.mpv_env("https://a.b/c")
        assert env["DENO_DIR"] == os.path.join(f.state, "deno") and env["DENO_NO_UPDATE_CHECK"] == "1"
        assert not any("js-runtimes" in a for a in Y.mpv_args("https://a.b/c")), "no Deno shipped"
        open(os.path.join(f.app, "yt-dlp", "deno.exe"), "w").close()
        assert f"--ytdl-raw-options-append=js-runtimes=deno:{Y.deno_path()}" in Y.mpv_args("https://a.b/c")
        assert any(f.shipped in a for a in Y.mpv_args("https://a.b/c"))
        other = os.path.join(f.state, "x.exe")
        assert f"--script-opts-append=ytdl_hook-ytdl_path={other}" in Y.mpv_args("https://a.b/c", other)


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
