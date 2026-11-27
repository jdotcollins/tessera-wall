"""Unit tests for resources.py: the memory guard. Run: python test_resources.py"""
import os
import winreg

import resources as R


def test_first_video_is_always_allowed():
    assert R.room_for_one_more([], 128, 100) == (True, "")


def test_card_limit():
    ok, why = R.room_for_one_more([170] * 4, 700, 16000)
    assert not ok and "video memory" in why
    assert R.room_for_one_more([170] * 2, 8192, 16000)[0]


def test_ram_limit():
    ok, why = R.room_for_one_more([170], None, R.RAM_RESERVE_MB + R.RAM_PER_VIDEO_MB - 1)
    assert not ok and "free memory" in why
    assert R.room_for_one_more([170], None, R.RAM_RESERVE_MB + R.RAM_PER_VIDEO_MB + 1)[0]


class FakeKey:
    def __init__(self, name):
        self.name = name

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def with_cards(sizes_mb, fn):
    """Run fn with the registry reporting graphics adapters of these sizes (None: no size value)."""
    saved = winreg.OpenKey, winreg.EnumKey, winreg.QueryValueEx, os.environ.pop("TESSERAWALL_VRAM_MB", None)
    keys = [f"{i:04}" for i in range(len(sizes_mb))]

    def enum(k, i):
        if i >= len(keys):
            raise OSError("no more")
        return keys[i]

    def query(k, name):
        size = sizes_mb[keys.index(k.name)]
        if size is None:
            raise OSError("no value")
        return (size * 2**20).to_bytes(8, "little"), winreg.REG_BINARY
    winreg.OpenKey = lambda parent, sub: FakeKey(sub)
    winreg.EnumKey = enum
    winreg.QueryValueEx = query
    try:
        return fn()
    finally:
        winreg.OpenKey, winreg.EnumKey, winreg.QueryValueEx = saved[:3]
        if saved[3] is not None:
            os.environ["TESSERAWALL_VRAM_MB"] = saved[3]


def test_integrated_graphics_set_no_card_limit():
    assert with_cards([128], R.gpu_memory_mb) is None              # Intel's reserved slice
    assert with_cards([512, None], R.gpu_memory_mb) is None        # an AMD APU
    assert with_cards([128, 8192], R.gpu_memory_mb) == 8192        # a laptop with both: the real card
    assert with_cards([16304], R.gpu_memory_mb) == 16304


def test_pretend_card_for_tests_still_works():
    os.environ["TESSERAWALL_VRAM_MB"] = "700"
    try:
        assert R.gpu_memory_mb() == 700
    finally:
        del os.environ["TESSERAWALL_VRAM_MB"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
