"""Run with: python test_templates.py   (no pytest, no mpv, no display; exits non-zero on failure)
Covers templates.py (format, shape matching, choosing files, Best fit) and probe.py's pure parts."""
import os
import random
import tempfile
import tomllib

import probe as P
import templates as T


def folder():
    """6 x 16:9, 3 x 4:3, 5 x 9:16, 1 square, 1 unreadable."""
    s = {f"L{i}": 16 / 9 for i in range(6)}
    s.update({f"F{i}": 4 / 3 for i in range(3)})
    s.update({f"P{i}": 9 / 16 for i in range(5)})
    s.update({"S0": 1.0, "bad": None})
    return s


def test_built_ins_use_the_user_format():
    data = tomllib.loads(T.BUILT_IN_TOML)
    assert data["version"] == T.FORMAT_VERSION
    assert [t.name for t in T.BUILT_IN] == [t["name"] for t in data["template"]]
    assert T.find(T.BUILT_IN, "2x2 Landscape").count == 4, "x for × and any case"


def test_shapes():
    assert T.parse_shape("16:9") == ("ratio", 16 / 9) and T.parse_shape("1.85")[1] == 1.85
    assert T.parse_shape(" Portrait ") == ("portrait", None)
    for bad in ("wide", "16:0", "-1", "", 5, "1:2:3"):
        assert T.parse_shape(bad) is None, bad
    assert T.matches("landscape", 16 / 9) and not T.matches("landscape", 1.1)
    assert T.matches("square", 1.1) and T.matches("portrait", 9 / 16) and T.matches("any", 3.0)
    assert T.matches("16:9", 1920 / 1088), "within 3 %"
    assert not T.matches("16:9", 4 / 3)


def test_user_file_checks():
    good = {"version": 1, "best_fit_max": 6,
            "template": [{"name": "Mine", "rows": [["16:9"], ["9:16", "9:16"]]}]}
    ts, problems, opts = T.parse(good)
    assert [t.name for t in ts] == ["Mine"] and not problems and opts == {"best_fit_max": 6}
    bad = {"version": 1, "best_fit_max": 99, "template": [
        {"rows": [["landscape"]]}, {"name": "Empty", "rows": []}, {"name": "Typo", "rows": [["landscap"]]},
        {"name": "Best fit", "rows": [["any"]]}, {"name": "Fine", "rows": [["any", "any"]]}]}
    ts, problems, opts = T.parse(bad)
    assert [t.name for t in ts] == ["Fine"] and len(problems) == 5 and opts == {}, problems
    ts, problems, _ = T.parse({"version": 2, "template": good["template"]})
    assert not ts and "version" in problems[0], "a newer format is refused, not misread"


def test_load_merges_and_replaces():
    path = os.path.join(tempfile.mkdtemp(prefix="tesserawall-tpl-"), "templates.toml")
    T.ensure_user_file(path)
    ts, problems, _ = T.load_templates(path)
    assert len(ts) == len(T.BUILT_IN) and not problems, "the example file adds nothing until uncommented"
    with open(path, "a", encoding="utf-8") as f:
        f.write('\n[[template]]\nname = "2x2 LANDSCAPE"\nrows = [["4:3", "4:3"]]\n')
    ts, problems, _ = T.load_templates(path)
    assert len(ts) == len(T.BUILT_IN) and T.find(ts, "2×2 landscape").rows == [["4:3", "4:3"]]
    with open(path, "w", encoding="utf-8") as f:
        f.write("version = [")
    ts, problems, _ = T.load_templates(path)
    assert len(ts) == len(T.BUILT_IN) and problems, "a broken file still leaves the built-ins"


def test_groups():
    g = T.groups(folder())
    assert [len(p) for _, p in g] == [6, 5, 3, 1] and abs(g[0][0] - 16 / 9) < 1e-9


def test_plan_full_template_keeps_one_ratio_and_slot_order():
    p = T.plan(T.find(T.BUILT_IN, "2x2 landscape"), folder(), random.Random(3))
    assert p.rows == [2, 2] and len(p.items) == 4 and not p.message
    assert all(i.startswith("L") for i in p.items), "the largest landscape group, never mixed with 4:3"
    p = T.plan(T.find(T.BUILT_IN, "Feature + 3 portrait"), folder(), random.Random(3))
    assert p.rows == [1, 3] and p.items[0].startswith("L") and all(i.startswith("P") for i in p.items[1:])
    assert abs(p.shape_of[p.items[1]] - 9 / 16) < 1e-9


def test_plan_short_folder_opens_what_there_is():
    p = T.plan(T.find(T.BUILT_IN, "3x3 landscape"), folder(), random.Random(3))
    assert len(p.items) == 6 and p.rows is None, "rows are not forced on a partial template"
    assert "found 6 of 9" in p.message
    assert T.availability(T.find(T.BUILT_IN, "3x3 landscape"), folder()) == (6, 9)
    empty = T.plan(T.find(T.BUILT_IN, "2 square side by side"), {"L": 16 / 9}, random.Random(3))
    assert not empty.items and "no square" in empty.message


def test_specific_slots_pick_first_and_files_are_used_once():
    t = T.Template("mix", [["landscape", "16:9", "any"]])
    p = T.plan(t, {"a": 16 / 9, "b": 16 / 9, "c": 2.39, "d": 2.39, "e": 2.39}, random.Random(1))
    assert len(set(p.items)) == 3
    assert p.items[1] in ("a", "b") and p.items[0] in ("c", "d", "e"), "16:9 slot took the 16:9 files"


def test_best_fit():
    b = T.best_fit(folder(), 2560, 1440, rng=random.Random(1))
    assert b.rows == [2, 2] and all(i.startswith("L") for i in b.items), b
    many = {f"L{i}": 16 / 9 for i in range(30)}
    b = T.best_fit(many, 2560, 1440, most=9, rng=random.Random(1))
    assert len(b.items) == 9 and b.rows == [3, 3, 3], "as many as fill the screen, up to the limit"
    b = T.best_fit({f"P{i}": 9 / 16 for i in range(8)}, 2560, 1440, rng=random.Random(1))
    assert len(b.rows) == 1 and T.coverage([9 / 16] * len(b.items), b.rows, 2560, 1440, 0.1) > 0.85, b
    assert not T.best_fit({"x": None}, 2560, 1440).items


def test_coverage():
    assert abs(T.coverage([16 / 9] * 4, [2, 2], 1920, 1080, 0.1) - 1) < 1e-6
    assert T.coverage([16 / 9] * 3, [3], 1920, 1080, 0.0) < 0.4, "a row of three leaves big bars"


def test_slot_folders_format():
    data = {"version": 1, "template": [
        {"name": "Mixed", "rows": [["landscape", "landscape"],
                                    [{"shape": "portrait", "folder": "Phone"}, {"folder": "D:\\Any"}]]},
        {"name": "Bad key", "rows": [[{"shape": "portrait", "dir": "x"}]]},
        {"name": "Bad shape", "rows": [[{"shape": "tall", "folder": "x"}]]},
        {"name": "Empty folder", "rows": [[{"shape": "portrait", "folder": " "}]]},
        {"name": "Number", "rows": [[5]]}]}
    ts, problems, _ = T.parse(data)
    assert [t.name for t in ts] == ["Mixed"] and len(problems) == 4, problems
    t = ts[0]
    assert t.rows == [["landscape", "landscape"], ["portrait", "any"]]
    assert t.folders == [[None, None], ["Phone", "D:\\Any"]] and t.own_folders == ["Phone", "D:\\Any"]
    assert t.needs_chosen_folder and t.needs() == {("landscape", None): 2, ("portrait", "Phone"): 1,
                                                     ("any", "D:\\Any"): 1}
    assert all(b.folders is None for b in T.BUILT_IN), "plain shapes stay plain"


def test_resolve_folder():
    base = os.path.join(tempfile.gettempdir(), "base")
    assert T.resolve_folder("Phone", base) == os.path.normpath(os.path.join(base, "Phone"))
    assert T.resolve_folder("C:\\Clips", base) == "C:\\Clips" and T.resolve_folder("Phone", None) is None
    os.environ["TW_TEST_DIR"] = "C:\\Env"
    assert T.resolve_folder("%TW_TEST_DIR%\\x", None) == "C:\\Env\\x"
    assert T.resolve_folder("~", None) == os.path.normpath(os.path.expanduser("~"))


def test_plan_with_slot_folders():
    t = T.parse({"template": [{"name": "Two sources", "rows": [
        ["landscape", "landscape"], [{"shape": "portrait", "folder": "Phone"}] * 3]}]})[0][0]
    chosen = {f"L{i}": 16 / 9 for i in range(4)}
    chosen.update({f"P{i}": 9 / 16 for i in range(5)})           # portrait here must NOT be used
    phone = {f"Phone/p{i}": 9 / 16 for i in range(4)}
    p = T.plan(t, chosen, random.Random(2), {"Phone": phone})
    assert p.rows == [2, 3] and all(i.startswith("L") for i in p.items[:2])
    assert all(i.startswith("Phone/") for i in p.items[2:]), p.items
    assert abs(p.shape_of[p.items[2]] - 9 / 16) < 1e-9
    short = T.plan(t, chosen, random.Random(2), {"Phone": {"Phone/p0": 9 / 16}})
    assert short.rows is None and "found 1 of 3 portrait videos in Phone" in short.message, short.message
    none = T.plan(t, chosen, random.Random(2), {})
    assert "no portrait videos in Phone" in none.message
    assert T.availability(t, chosen, {"Phone": phone}) == (5, 5)


def test_gather_reads_everything_once():
    base = tempfile.mkdtemp(prefix="tesserawall-gather-")
    phone = os.path.join(base, "Phone")
    os.makedirs(phone)
    t = T.parse({"template": [{"name": "G", "rows": [["landscape"], [{"shape": "portrait", "folder": "Phone"},
                                                                     {"folder": "Missing"}]]}]})[0][0]
    listing = {base: [os.path.join(base, "a.mp4")], phone: [os.path.join(phone, "b.mp4")]}
    calls = []

    def expand(paths):
        return [f for p in paths for f in listing.get(os.path.normpath(p), [])]

    def probe(files):
        calls.append(sorted(files))
        return {f: (16 / 9 if f.endswith("a.mp4") else 9 / 16) for f in files}

    shapes, per = T.gather([t], [base], expand, probe)
    assert len(calls) == 1 and len(calls[0]) == 2, "one read for the chosen folder and the slot folders"
    assert shapes == {os.path.join(base, "a.mp4"): 16 / 9}
    assert per["G"]["Phone"] == {os.path.join(phone, "b.mp4"): 9 / 16} and per["G"]["Missing"] == {}


def test_probe_parsing_and_aspect():
    assert P.display_aspect(1920, 1080, 90) == 1080 / 1920, "phone video recorded sideways"
    assert abs(P.display_aspect(720, 480, 0, 32 / 27) - 16 / 9) < 1e-9, "anamorphic pixels"
    assert P.display_aspect(0, 0) is None
    out = P.parse_output("0\t1920\t1080\t0\t1.000000\n2\t1080\t1920\t0\t1.0\nnoise\n3\t0\t0\t0\t1\n")
    assert out == {0: 1920 / 1080, 2: 1080 / 1920, 3: None}


def test_probe_cache_round_trip():
    d = tempfile.mkdtemp(prefix="tesserawall-shapes-")
    clip = os.path.join(d, "a.mp4")
    with open(clip, "wb") as f:
        f.write(b"x")
    cache = os.path.join(d, "shapes.json")
    P.save_cache(cache, {P._key(clip): P._stamp(clip) + [1.5]})
    assert P.cached([clip], cache) == {clip: 1.5}
    with open(clip, "ab") as f:
        f.write(b"changed")
    assert P.cached([clip], cache) == {}, "a changed file is read again"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
