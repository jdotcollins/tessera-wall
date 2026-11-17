"""Run with: python test_config.py   (no pytest needed; exits non-zero on failure)"""
import os
import tempfile
import tomllib

import config as C


def write(text):
    path = os.path.join(tempfile.mkdtemp(prefix="tesserawall-cfg-"), "config.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def reset():
    C.load(os.path.join(tempfile.gettempdir(), "tesserawall-no-such-file.toml"))


def test_missing_file_means_defaults():
    reset()
    assert all(getattr(C, n) == v for n, v in C.DEFAULTS.items())


def test_overrides_and_types():
    reset()
    changed, problems = C.load(write(
        'focus_scale = 2\ndim_unfocused = 40\nseekbar_show_name = false\nlayout_mode = "grid"\n'
        'extra_mpv_args = ["--volume=70"]\nvideo_exts = [".MP4", ".mkv"]\nmpv_log_dir = "C:\\\\logs"\n'))
    assert not problems, problems
    assert sorted(changed) == ["DIM_UNFOCUSED", "EXTRA_MPV_ARGS", "FOCUS_SCALE", "LAYOUT_MODE", "MPV_LOG_DIR",
                               "SEEKBAR_SHOW_NAME", "VIDEO_EXTS"]
    assert C.FOCUS_SCALE == 2.0 and isinstance(C.FOCUS_SCALE, float), "an int is accepted for a float setting"
    assert C.DIM_UNFOCUSED == 40 and C.SEEKBAR_SHOW_NAME is False and C.LAYOUT_MODE == "grid"
    assert C.EXTRA_MPV_ARGS == ["--volume=70"] and C.VIDEO_EXTS == {".mp4", ".mkv"} and C.MPV_LOG_DIR == r"C:\logs"


def test_removing_a_line_restores_its_default():
    reset()
    C.load(write("focus_scale = 2.5\n"))
    changed, _ = C.load(write("dim_unfocused = 5\n"))
    assert C.FOCUS_SCALE == C.DEFAULTS["FOCUS_SCALE"] and C.DIM_UNFOCUSED == 5
    assert sorted(changed) == ["DIM_UNFOCUSED", "FOCUS_SCALE"]


def test_bad_keys_are_skipped_not_fatal():
    reset()
    changed, problems = C.load(write('focus_scale = "big"\nno_such_thing = 1\ndim_unfocused = true\nanim_ms = 0\n'))
    assert changed == ["ANIM_MS"] and C.ANIM_MS == 0
    assert C.FOCUS_SCALE == C.DEFAULTS["FOCUS_SCALE"] and C.DIM_UNFOCUSED == C.DEFAULTS["DIM_UNFOCUSED"]
    assert len(problems) == 3 and any("no_such_thing" in p for p in problems)


def test_invalid_toml_changes_nothing():
    reset()
    C.load(write("focus_scale = 2.5\n"))
    try:
        C.load(write("focus_scale = = 3\n"))
        raise AssertionError("should have raised")
    except tomllib.TOMLDecodeError:
        pass
    assert C.FOCUS_SCALE == 2.5, "a file that cannot be parsed must leave the current settings alone"


def test_template_is_complete_and_valid():
    text = C.template()
    assert all(f"# {n.lower()} = " in text for n in C.DEFAULTS), "every setting appears in the template"
    live = "\n".join(ln[2:] for ln in text.splitlines() if ln.startswith("# ") and " = " in ln
                     and ln[2:].split(" = ")[0].upper() in C.DEFAULTS)
    reset()
    changed, problems = C.load(write(live))
    assert not problems, problems
    assert changed == [], f"uncommenting the template must reproduce the defaults, changed {changed}"
    path = os.path.join(tempfile.mkdtemp(prefix="tesserawall-cfg-"), "sub", "config.toml")
    C.ensure_user_file(path)
    assert open(path, encoding="utf-8").read() == text
    with open(path, "w", encoding="utf-8") as f:
        f.write("anim_ms = 1\n")
    C.ensure_user_file(path)
    assert open(path, encoding="utf-8").read() == "anim_ms = 1\n", "never overwrite an existing file"


def test_set_user_values():
    reset()
    path = os.path.join(tempfile.mkdtemp(prefix="tesserawall-cfg-"), "config.toml")
    C.set_user_values(path, {"FOCUS_SCALE": 1.8, "DIM_UNFOCUSED": 30, "SEEKBAR_SHOW_NAME": False,
                             "LAYOUT_MODE": "grid"})      # no file yet: the template is created first
    text = open(path, encoding="utf-8").read()
    assert text.count("focus_scale = ") == 1, "the template's commented line is rewritten, not duplicated"
    assert "\nfocus_scale = 1.8\n" in text and "\nseekbar_show_name = false\n" in text
    assert "# How much the hovered video grows." in text, "descriptions stay"
    changed, problems = C.load(path)
    assert not problems and sorted(changed) == ["DIM_UNFOCUSED", "FOCUS_SCALE", "LAYOUT_MODE", "SEEKBAR_SHOW_NAME"]
    assert (C.FOCUS_SCALE, C.DIM_UNFOCUSED, C.SEEKBAR_SHOW_NAME, C.LAYOUT_MODE) == (1.8, 30, False, "grid")

    C.set_user_values(path, {"FOCUS_SCALE": 2.0, "DIM_UNFOCUSED": None})   # change one, reset one
    text = open(path, encoding="utf-8").read()
    assert "\nfocus_scale = 2.0\n" in text and "\n# dim_unfocused = 15\n" in text and text.count("dim_unfocused") == 1
    C.load(path)
    assert C.FOCUS_SCALE == 2.0 and C.DIM_UNFOCUSED == 15 and C.LAYOUT_MODE == "grid"

    # A hand-written file: the user's other lines, comments and order survive; a missing key is appended.
    own = write("# my notes\nmax_videos = 8\n\nanim_ms = 0   # snappy\nfocus_scale=1.2\n")
    C.set_user_values(own, {"FOCUS_SCALE": 1.6, "CROSSFADE_MS": 300})
    assert open(own, encoding="utf-8").read() == \
        "# my notes\nmax_videos = 8\n\nanim_ms = 0   # snappy\nfocus_scale = 1.6\ncrossfade_ms = 300\n"
    C.set_user_values(own, {"CROSSFADE_MS": None, "TICK_MS": None})         # resetting an absent key adds nothing
    assert open(own, encoding="utf-8").read().endswith("focus_scale = 1.6\n# crossfade_ms = 150\n")
    reset()


def test_find_mpv():
    reset()
    found = C.find_mpv()
    assert found is None or os.path.isfile(found)
    C.load(write('mpv_path = "C:\\\\definitely\\\\not\\\\here\\\\mpv.exe"\n'))
    assert C.find_mpv() is None, "an explicit mpv_path that does not exist must not silently fall back"
    reset()
    print("   mpv found at:", found)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
