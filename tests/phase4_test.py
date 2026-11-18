"""Templates. TAKES OVER MOUSE, KEYBOARD AND SCREEN for ~60 s.
Run: python tests/phase4_test.py      Uses its own settings folder (templates.toml, shapes.json).

Makes a folder of small H.264 clips once (%TEMP%\\tesserawall-p4-clips): 6 x 16:9, 3 x 4:3, 5 x 9:16, 1 square.
  A. --template "2x2 landscape": 4 videos, all 16:9 (never 4:3), rows [2, 2]; the folder is probed
  B. --template "4 portrait in a row": one row of 4 portrait videos, shapes now come from the cache;
     N on a pinned video swaps in another portrait video
  C. --template best: Best fit chooses 4 x 16:9 in [2, 2] on a 16:9 screen
  D. --choose-template: the chooser window lists the templates; clicking "Feature + 3 portrait" opens
     one 16:9 video above three portrait ones
  E. --template "3x3 landscape" with only 6 x 16:9: opens 6, says so, rows not forced
  F. --template "nope": nothing opens, exit 0
"""
import ctypes
import os
import re
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes

ctypes.windll.shcore.SetProcessDpiAwareness(2)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import config as C

LOG = os.path.join(tempfile.gettempdir(), "tesserawall.log")
APP = os.path.join(ROOT, "tesserawall.pyw")
CLIPS = os.path.join(tempfile.gettempdir(), "tesserawall-p4-clips")
SPECS = [("land", 6, "320x180"), ("four3", 3, "240x180"), ("port", 5, "180x320"), ("sq", 1, "240x240")]
user32 = ctypes.windll.user32


def make_clips():
    os.makedirs(CLIPS, exist_ok=True)
    mpv = C.find_mpv()
    for name, n, size in SPECS:
        for i in range(n):
            out = os.path.join(CLIPS, f"{name}{i}.mkv")
            if not os.path.exists(out) or os.path.getsize(out) < 1000:   # missing, or left by a stopped run
                subprocess.run([mpv, "--no-config", f"av://lavfi:testsrc2=s={size}:r=30:d=6", f"--o={out}",
                                "--of=matroska", "--ovc=libx264", "--no-audio", "--really-quiet",
                                # without a console mpv applies its pseudo-gui profile (idle=once) and
                                # would wait forever after encoding
                                "--idle=no", "--keep-open=no"], check=True, timeout=60)


def log_text():
    try:
        return open(LOG, encoding="utf-8", errors="replace").read()
    except FileNotFoundError:
        return ""


def run(args, wait=True, exit_after=7):
    if os.path.exists(LOG):
        os.remove(LOG)
    p = subprocess.Popen([sys.executable, APP, "--exit-after", str(exit_after), *args])
    if wait:
        p.wait(60)
    return p


def added():
    return [os.path.basename(m) for m in re.findall(r"tile added #\d+ pipe=\S+ path=(.*)", log_text())]


def last_rows():
    rows = re.findall(r"relayout rows=(\[[\d, ]*\])", log_text())
    return rows[-1] if rows else None


def click(x, y):
    user32.SetCursorPos(x - 1, y)
    time.sleep(0.05)
    user32.SetCursorPos(x, y)
    time.sleep(0.3)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.5)


def press(vk):
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, 2, 0)
    time.sleep(0.4)


def main():
    make_clips()
    print("WARNING: this test controls mouse, keyboard and screen for ~60 seconds.")
    tmp = tempfile.mkdtemp(prefix="tesserawall-p4-")
    cfg = os.path.join(tmp, "config.toml")
    open(cfg, "w").close()
    base = ["--config", cfg, "--session", os.path.join(tmp, "s.json")]
    orig = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(orig))
    r, procs = {}, []
    try:
        p = run(base + ["--template", "2x2 landscape", CLIPS])
        r["A_exit"], r["A_added"], r["A_rows"] = p.returncode, added(), last_rows()
        r["A_probe"] = re.search(r"shape probe: (\d+) files, (\d+) read", log_text())
        r["A_probe"] = r["A_probe"].groups() if r["A_probe"] else None

        p = run(base + ["--template", "4 portrait in a row", CLIPS], wait=False, exit_after=14)
        procs.append(p)
        time.sleep(4)
        r["B_added"], r["B_rows"] = added(), last_rows()
        r["B_cached"] = bool(re.search(r"shape probe: 15 files, 0 read, 15 cached", log_text()))
        rects = eval(re.findall(r"rects=(\[.*?\]) panscan", log_text())[-1])
        x, y, w, h = rects[0]
        click(x + w // 2, y + h // 2)                   # pin video 0, keyboard to the wall
        press(0x4E)                                     # N
        time.sleep(1.0)
        swaps = re.findall(r"file swapped: .*?\\(\w+\.mkv) -> .*?\\(\w+\.mkv)", log_text())
        r["B_swap"] = swaps[-1] if swaps else None
        r["B_exit"] = p.wait(30)

        p = run(base + ["--template", "best", CLIPS])
        plan = re.search(r"template plan: (Best fit[^,]*), (\d+) videos, rows=(\[[\d, ]*\])", log_text())
        r["C_plan"], r["C_added"] = plan.groups() if plan else None, added()

        p = run(base + ["--choose-template", CLIPS], wait=False, exit_after=12)
        procs.append(p)
        end, m = time.monotonic() + 8, None
        while time.monotonic() < end and not m:
            m = re.search(r"template window t6 at (-?\d+),(-?\d+) (\d+)x(\d+)", log_text())
            time.sleep(0.1)
        r["D_listed"] = len(re.findall(r"template window (?:best|t\d+) at", log_text()))
        if m:
            x, y, w, h = map(int, m.groups())
            time.sleep(0.4)
            click(x + w // 2, y + h // 2)
            time.sleep(3)
        r["D_chosen"] = bool(re.search(r"template chosen: Feature \+ 3 portrait, 4 videos, rows=\[1, 3\]", log_text()))
        r["D_added"], r["D_rows"] = added(), last_rows()
        r["D_exit"] = p.wait(30)

        p = run(base + ["--template", "3x3 landscape", CLIPS])
        r["E_added"], r["E_message"] = added(), "found 6 of 9 landscape videos" in log_text()
        r["E_forced"] = "template rows" in log_text()

        p = run(base + ["--template", "nope", CLIPS])
        r["F_exit"], r["F_added"] = p.returncode, added()
        r["F_logged"] = "There is no template called" in log_text()
    finally:
        user32.SetCursorPos(orig.x, orig.y)
        for q in procs:
            if q.poll() is None:
                q.kill()
    for k, v in r.items():
        print(f"{k:12} {v}")
    land = lambda names: all(n.startswith("land") for n in names)
    port = lambda names: all(n.startswith("port") for n in names)
    assert r["A_exit"] == 0 and len(r["A_added"]) == 4 and land(r["A_added"]) and r["A_rows"] == "[2, 2]"
    assert r["A_probe"] == ("15", "15"), "the whole folder was read once"
    assert len(r["B_added"]) == 4 and port(r["B_added"]) and r["B_rows"] == "[4]" and r["B_cached"]
    assert r["B_swap"] and r["B_swap"][1].startswith("port") and r["B_swap"][1] not in r["B_added"], r["B_swap"]
    assert r["B_exit"] == 0
    assert r["C_plan"] and r["C_plan"][1:] == ("4", "[2, 2]") and land(r["C_added"]), r["C_plan"]
    assert r["D_listed"] == 8 and r["D_chosen"] and r["D_exit"] == 0
    assert r["D_added"][0].startswith("land") and port(r["D_added"][1:]) and r["D_rows"] == "[1, 3]"
    assert len(r["E_added"]) == 6 and land(r["E_added"]) and r["E_message"] and not r["E_forced"]
    assert r["F_exit"] == 0 and not r["F_added"] and r["F_logged"]
    print("PHASE4 TEST PASSED")


if __name__ == "__main__":
    main()
