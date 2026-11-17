"""Folder scanning + single-instance test. Covers the screen for ~8 seconds. Run: python tests/add_test.py

Part 1 checks expand() on a temp folder (no playback).
Part 2 starts a wall, launches tesserawall a second time, and checks the extra video joins the running wall.
"""
import os
import re
import subprocess
import sys
import tempfile
import time
from importlib.machinery import SourceFileLoader

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
LOG = os.path.join(tempfile.gettempdir(), "tesserawall.log")
APP = os.path.join(ROOT, "tesserawall.pyw")
L, P = "av://lavfi:testsrc2=s=1280x720:r=30", "av://lavfi:testsrc2=s=720x1280:r=30"


def test_expand():
    app = SourceFileLoader("tesserawall_app", APP).load_module()
    folder = tempfile.mkdtemp(prefix="tesserawall-test-")
    for name in ["b üñî.MKV", "a clip.mp4", "notes.txt", "c.webm"]:
        open(os.path.join(folder, name), "w").close()
    os.mkdir(os.path.join(folder, "sub.mp4"))  # a directory that looks like a video must be skipped
    got = app.expand([folder, L, os.path.join(folder, "notes.txt"), os.path.join(folder, "missing.mp4")])
    names = [os.path.basename(p) if "://" not in p else p for p in got]
    assert names == ["a clip.mp4", "b üñî.MKV", "c.webm", L, "notes.txt"], names
    assert all(os.path.isabs(p) for p in got if "://" not in p)
    print("ok expand")


def test_single_instance():
    first = subprocess.Popen([sys.executable, APP, "--exit-after", "8", L, P])
    time.sleep(3)
    second = subprocess.run([sys.executable, APP, L], timeout=10)
    code = first.wait(20)
    log = open(LOG, encoding="utf-8", errors="replace").read()
    print(log)
    assert second.returncode == 0 and code == 0, (second.returncode, code)
    assert len(re.findall("tile added", log)) == 3, "expected 2 videos + 1 added later"
    assert log.count("aspect known 1.7778") == 2 and log.count("aspect known 0.5625") == 1
    assert "tile removed" not in log and "callback error" not in log
    last = re.findall(r"relayout (?:rows|cols)=(\[.*?\]) focus=\w+ rects=(\[.*?\]) panscan", log)[-1]
    assert len(eval(last[1])) == 3
    print("ok single instance")


def test_simultaneous_launches():
    """Explorer starts one process per selected file, all at once: they must make ONE wall."""
    # Distinct sources whose Explorer name order (numbers compared as numbers) differs from both the
    # launch order and plain string order: r=2 < r=9 < r=10 < r=30.
    srcs = [f"av://lavfi:testsrc2=s=640x360:r={r}" for r in (10, 30, 2, 9)]
    procs = [subprocess.Popen([sys.executable, APP, "--exit-after", "7", src]) for src in srcs]
    codes = [p.wait(25) for p in procs]
    log = open(LOG, encoding="utf-8", errors="replace").read()
    print(log)
    assert codes == [0, 0, 0, 0], codes
    assert log.count("settings file:") == 1, "more than one process became the wall"
    added = re.findall(r"tile added #\d+ pipe=\S+ path=(\S+)", log)
    assert len(added) == 4, "expected all 4 videos in one wall"
    assert added == [srcs[2], srcs[3], srcs[0], srcs[1]], f"not in Explorer name order: {added}"
    assert "listener failed" not in log and "callback error" not in log
    print("ok simultaneous launches")


if __name__ == "__main__":
    test_expand()
    test_single_instance()
    test_simultaneous_launches()
    print("ADD TEST PASSED")
