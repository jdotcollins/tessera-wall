"""Unit tests for files.py: folders with and without subfolders, and the small state file."""
import os
import tempfile

import config as C
import files


def make_tree(root):
    for rel in ["b.mp4", "a.mkv", "notes.txt", os.path.join("sub", "c.mp4"), os.path.join("sub", "deep", "d.webm"),
                os.path.join(".hidden", "e.mp4"), os.path.join("$RECYCLE.BIN", "f.mp4"), os.path.join("Z", "g.mp4")]:
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()


def names(paths, root):
    return [os.path.relpath(p, root).replace("\\", "/") for p in paths]


def test_folder_without_subfolders():
    with tempfile.TemporaryDirectory() as root:
        make_tree(root)
        assert names(files.folder_videos(root, False), root) == ["a.mkv", "b.mp4"]


def test_folder_with_subfolders_in_order_skipping_hidden():
    with tempfile.TemporaryDirectory() as root:
        make_tree(root)
        assert names(files.folder_videos(root, True), root) == ["a.mkv", "b.mp4", "sub/c.mp4", "sub/deep/d.webm",
                                                                "Z/g.mp4"]


def test_expand_follows_the_setting():
    with tempfile.TemporaryDirectory() as root:
        make_tree(root)
        try:
            C.INCLUDE_SUBFOLDERS = False
            assert len(files.expand([root])) == 2
            C.INCLUDE_SUBFOLDERS = True
            assert len(files.expand([root])) == 5
        finally:
            C.INCLUDE_SUBFOLDERS = C.DEFAULTS["INCLUDE_SUBFOLDERS"]


def test_subfolder_limit():
    with tempfile.TemporaryDirectory() as root:
        make_tree(root)
        old = files.SUBFOLDER_LIMIT
        try:
            files.SUBFOLDER_LIMIT = 3
            assert len(files.folder_videos(root, True)) == 3
        finally:
            files.SUBFOLDER_LIMIT = old


def test_state_round_trip_and_bad_file():
    with tempfile.TemporaryDirectory() as root:
        cfg = os.path.join(root, "config.toml")
        assert files.read_state(cfg) == {}
        files.write_state(cfg, hint_walls=2)
        files.write_state(cfg, other=True)
        assert files.read_state(cfg) == {"hint_walls": 2, "other": True}
        with open(os.path.join(root, "state.json"), "w") as f:
            f.write("[not a dict")
        assert files.read_state(cfg) == {}


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
