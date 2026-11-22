"""Run with: python test_sources.py   (no pytest, no network, no mpv; exits non-zero on failure)
Covers sources.py: which links are channels / playlists, reading yt-dlp's listing, picking, the cache, and
the stream quality per video count."""
import json
import os
import tempfile
import time

import sources as S
import ytdlp as Y


def test_source_of():
    assert S.source_of("https://www.youtube.com/@NASA") == "https://www.youtube.com/@NASA/videos"
    assert S.source_of("https://youtube.com/@NASA/featured") == "https://www.youtube.com/@NASA/videos"
    assert S.source_of("https://m.youtube.com/@NASA/shorts?app=desktop") == "https://www.youtube.com/@NASA/shorts"
    assert S.source_of("https://www.youtube.com/channel/UCabc/streams") == "https://www.youtube.com/channel/UCabc/streams"
    assert S.source_of("https://www.youtube.com/playlist?list=PLx-1_y&si=z") == "https://www.youtube.com/playlist?list=PLx-1_y"
    for single in ("https://www.youtube.com/watch?v=abc&list=PLx", "https://www.youtube.com/shorts/abc",
                   "https://youtu.be/abc", "https://www.twitch.tv/lofigirl", "https://www.youtube.com/@NASA/about"):
        assert S.source_of(single) is None, single
    assert S.label("https://www.youtube.com/@NASA/shorts") == "@NASA Shorts"
    assert S.label("https://www.youtube.com/playlist?list=x") == "the playlist"


def test_parse_listing():
    text = ("k5Z7sFwFF7Q\thttps://www.youtube.com/shorts/k5Z7sFwFF7Q\tNA\tBecome a NASA Astronaut!\n"
            "WARNING: something\n"
            "abcdefghijk\tNA\t64.0\tA title\twith a tab\n"
            "k5Z7sFwFF7Q\thttps://www.youtube.com/shorts/k5Z7sFwFF7Q\tNA\tduplicate\n"
            "zzzzzzzzzzz\tNA\tNA\t[Private video]\n")
    got = S.parse_listing(text)
    assert [e["url"] for e in got] == ["https://www.youtube.com/shorts/k5Z7sFwFF7Q",
                                       "https://www.youtube.com/watch?v=abcdefghijk"], got
    assert got[0]["duration"] is None and got[1]["duration"] == 64.0 and got[1]["title"] == "A title\twith a tab"
    assert S.is_short(got[0]["url"]) and not S.is_short(got[1]["url"])


def test_pick_and_step():
    entries = [{"url": f"u{i}", "title": str(i), "duration": 1} for i in range(5)]
    picked = S.pick(entries, 3, {"u0", "u1"})
    assert len(picked) == 3 and {e["url"] for e in picked} == {"u2", "u3", "u4"}
    assert len(S.pick(entries, 9)) == 5
    assert S.step(entries, "u1", 1)["url"] == "u2" and S.step(entries, "u4", 1)["url"] == "u0"
    assert S.step(entries, "u0", -1, {"u4"})["url"] == "u3", "wraps, skipping what is on the wall"
    assert S.step(entries, "other", 1)["url"] == "u0" and S.step([], "u0", 1) is None


def test_cache():
    saved = Y._state_dir
    Y.init(tempfile.mkdtemp(prefix="tesserawall-sources-"))
    try:
        src = "https://www.youtube.com/@Test/shorts"
        assert S.cached(src) is None
        os.makedirs(os.path.dirname(S.cache_path(src)))
        with open(S.cache_path(src), "w") as f:
            json.dump({"source": src, "fetched": time.time(), "entries": [{"url": "u", "title": "t"}]}, f)
        assert S.cached(src) == [{"url": "u", "title": "t"}]
        assert S.load(src) == [{"url": "u", "title": "t"}], "a fresh cache is used without running yt-dlp"
        with open(S.cache_path(src), "w") as f:
            json.dump({"source": src, "fetched": time.time() - S.MAX_AGE - 1, "entries": []}, f)
        assert S.cached(src) is None, "older than a day"
    finally:
        Y._state_dir = saved


def test_height_for():
    assert [S.height_for(n, 1440, 1080) for n in (1, 4, 6, 9, 24)] == [1080, 720, 720, 480, 360]
    assert S.height_for(1, 1440, 720) == 720, "never above stream_max_height"
    assert S.height_for(1, 2160, 2160) == 2160


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
