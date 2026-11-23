"""Run with: python test_sources.py   (no pytest, no network, no mpv; exits non-zero on failure)
Covers sources.py: which links are channels / playlists / searches, reading yt-dlp's listing, picking (at
random or newest first, passing over videos already shown), the cache, and the stream quality per video
count."""
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


def test_search():
    lofi = "https://www.youtube.com/results?search_query=lofi+hip+hop"
    assert S.source_of("https://www.youtube.com/results?search_query=lofi+hip+hop&sp=EgIQAQ%3D%3D") == lofi
    assert S.source_of("https://m.youtube.com/results?search_query=lofi%20%20hip+hop") == lofi
    assert S.source_of("ytsearch:lofi hip hop") == lofi and S.source_of("YTSEARCH50:  lofi   hip hop ") == lofi
    assert S.source_of("https://www.youtube.com/results?search_query=") is None
    assert S.source_of("ytsearch:") is None and S.from_search_text("lofi") is None
    assert S.query_of(S.search_url("rock & roll 100%")) == "rock & roll 100%"
    assert S.query_of("https://www.youtube.com/@NASA/videos") is None
    assert S.label(lofi) == "the search \u201clofi hip hop\u201d"
    assert S.label(S.search_url("x" * 60)) == "the search \u201c" + "x" * 39 + "\u2026\u201d"


def test_search_is_listed_with_ytsearch():
    saved_dir, saved_run, saved_find = Y._state_dir, Y.run, Y.find
    calls = []
    Y.init(tempfile.mkdtemp(prefix="tesserawall-search-"))
    Y.find = lambda: "yt-dlp.exe"
    Y.run = lambda args, timeout, env=None: (calls.append(args), (0, "abcdefghijk\tNA\t60\tNA\tA video\n"
                                                                     + S.TITLE_LINE + "lofi hip hop\n"))[1]
    try:
        got = S.load(S.search_url("lofi  hip hop"))
        assert [e["url"] for e in got] == ["https://www.youtube.com/watch?v=abcdefghijk"], "title line skipped"
        assert S.cached_title(S.search_url("lofi hip hop")) == "lofi hip hop", "the list's title is kept"
        assert calls[0][-1] == "ytsearch100:lofi hip hop" and "1:100" in calls[0], calls[0]
        S.load("https://www.youtube.com/@Test/videos")
        assert calls[1][-1] == "https://www.youtube.com/@Test/videos" and "1:500" in calls[1]
    finally:
        Y._state_dir, Y.run, Y.find = saved_dir, saved_run, saved_find


def test_parse_listing():
    text = ("k5Z7sFwFF7Q\thttps://www.youtube.com/shorts/k5Z7sFwFF7Q\tNA\tNA\tBecome a NASA Astronaut!\n"
            "WARNING: something\n"
            "abcdefghijk\tNA\t64.0\tNA\tA title\twith a tab\n"
            "k5Z7sFwFF7Q\thttps://www.youtube.com/shorts/k5Z7sFwFF7Q\tNA\tNA\tduplicate\n"
            "zzzzzzzzzzz\tNA\tNA\tNA\t[Private video]\n"
            "upcomingvid\tNA\tNA\tis_upcoming\tPremieres tomorrow\n"
            "livenow1234\tNA\tNA\tis_live\tOn air\n")
    got = S.parse_listing(text)
    assert [e["url"] for e in got] == ["https://www.youtube.com/shorts/k5Z7sFwFF7Q",
                                       "https://www.youtube.com/watch?v=abcdefghijk",
                                       "https://www.youtube.com/watch?v=livenow1234"], "upcoming ones dropped"
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


def test_pick_order_and_seen():
    entries = [{"url": f"u{i}", "title": str(i), "duration": 1} for i in range(6)]
    assert [e["url"] for e in S.pick(entries, 3, order="newest")] == ["u0", "u1", "u2"]
    assert [e["url"] for e in S.pick(entries, 2, {"u0"}, "newest", {"u1"})] == ["u2", "u3"], \
        "newest first, skipping what is on the wall and what was shown"
    assert [e["url"] for e in S.pick(entries, 3, {"u5"}, "newest", {"u0", "u1", "u2", "u3"})] == ["u4", "u0", "u1"], \
        "once the unseen run out, shown ones come back, in order"
    for _ in range(20):
        got = {e["url"] for e in S.pick(entries, 2, {"u0"}, "random", {"u1", "u2", "u3"})}
        assert got == {"u4", "u5"}, "random also prefers videos not shown yet"
    got = S.pick(entries, 4, set(), "random", {"u0", "u1", "u2", "u3"})
    assert len(got) == 4 and {"u4", "u5"} <= {e["url"] for e in got}


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
        with open(S.cache_path(src), "w") as f:
            json.dump({"source": src, "fetched": time.time() - S.FRESH_AGE - 1, "entries": [{"url": "u"}]}, f)
        assert S.cached(src) == [{"url": "u"}] and S.cached(src, S.FRESH_AGE) is None, "newest order: an hour"
    finally:
        Y._state_dir = saved


def test_names():
    pl = "https://www.youtube.com/playlist?list=PLabc"
    assert S.label(pl, "Hits") == "the playlist “Hits”" and S.menu_name(pl, "Hits") == "Playlist: Hits"
    assert S.menu_name(pl) == "A playlist" or S.cached_title(pl), "no title known"
    assert S.menu_name("https://www.youtube.com/@NASA/shorts") == "@NASA Shorts"
    assert S.menu_name(S.search_url("lofi hip hop")) == "Search: lofi hip hop"
    assert len(S.menu_name(pl, "y" * 80)) == len("Playlist: ") + 46


def test_recent():
    cfg = os.path.join(tempfile.mkdtemp(prefix="tesserawall-recent-"), "config.toml")
    a, b, c = (f"https://www.youtube.com/@{x}/videos" for x in "ABC")
    assert S.recent(cfg) == [] and S.recent(cfg, limit=6) == []
    S.remember(cfg, a)
    time.sleep(0.01)
    S.remember(cfg, b)
    time.sleep(0.01)
    S.remember(cfg, c, "C title")
    assert [d["source"] for d in S.recent(cfg)] == [c, b, a], "most recently opened first"
    assert S.recent(cfg)[0]["title"] == "C title"
    S.set_pinned(cfg, a, True)
    assert [d["source"] for d in S.recent(cfg)] == [a, c, b], "pinned first"
    assert [d["source"] for d in S.recent(cfg, limit=2)] == [a, c]
    time.sleep(0.01)
    S.remember(cfg, c)
    assert S.recent(cfg)[1]["title"] == "C title", "reopening keeps the title"
    time.sleep(0.01)
    S.remember(cfg, a)
    assert S.recent(cfg)[0]["pinned"], "reopening keeps the pin"
    S.forget(cfg, b)
    assert [d["source"] for d in S.recent(cfg)] == [a, c]
    for i in range(S.RECENT_KEEP + 5):
        S.remember(cfg, S.search_url(f"q{i}"))
    got = S.recent(cfg)
    assert len(got) == S.RECENT_KEEP + 1 and got[0]["source"] == a, "only the latest unpinned are kept"
    assert got[1]["source"] == S.search_url(f"q{S.RECENT_KEEP + 4}")
    pinned = [S.search_url(f"p{i}") for i in range(8)]
    for p in pinned:
        S.remember(cfg, p)
        S.set_pinned(cfg, p, True)
    assert len(S.recent(cfg, limit=6)) == 9, "every pinned one is listed, even past the limit"
    with open(S.recent_path(cfg), "w", encoding="utf-8") as f:
        f.write('{"sources": [{"source": "not a source"}, 5, {"source": "' + a + '"}]}')
    assert [d["source"] for d in S.recent(cfg)] == [a], "bad entries ignored"
    with open(S.recent_path(cfg), "w", encoding="utf-8") as f:
        f.write("not json")
    assert S.recent(cfg) == []


def test_height_for():
    assert [S.height_for(n, 1440, 1080) for n in (1, 4, 6, 9, 24)] == [1080, 720, 720, 480, 360]
    assert S.height_for(1, 1440, 720) == 720, "never above stream_max_height"
    assert S.height_for(1, 2160, 2160) == 2160


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
