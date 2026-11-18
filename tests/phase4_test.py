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
  G. when_finished = "random" in that 2x2 wall: videos that end are replaced by other 16:9 videos (or
     played again when none is free); rows stay [2, 2]
  H. one video with the default "loop"; E switches to "next"; when it ends land1 follows land0
  I. switch_seconds = 4, random_start: videos move on (16:9 only) and start at random points; the
     pinned video stays
  J. a template with a slot folder of its own (relative path): 2 x 16:9 from the chosen folder above
     3 portrait clips from the other folder
  K. a template with a folder on every slot is a button in the start window; one click opens it
  L. the start window's "Use a template...": the chooser appears over it (it used to stay invisible and
     freeze the start window), and Best fit opens the wall
"""
import ctypes
import os
import re
import shutil
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
        r["A_flat"] = "template: layout flat" in log_text() and bool(re.search(r"relayout rows=\[2, 2\] focus=\S+ flat", log_text()))
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
        time.sleep(0.5)
        after = eval(re.findall(r"rects=(\[.*?\]) panscan", log_text())[-1])
        r["B_focus_moved_nothing"] = after == rects and "focus -> 0" in log_text()
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
        press(0x4C)                                     # L: flat -> even grid, within the template's rows
        time.sleep(0.6)
        grid_line = re.findall(r"relayout rows=\[1, 3\] focus=\S+ grid rects=(\[.*?\]) panscan", log_text())
        if grid_line:
            g = eval(grid_line[-1])
            sw, sh = g[0][2], g[0][3] * 2
            r["D_grid"] = (g[0][:2] == (0, 0) and all(c[1] == sh // 2 for c in g[1:])
                           and max(c[2] for c in g[1:]) - min(c[2] for c in g[1:]) <= 1 and sum(c[2] for c in g[1:]) == sw)
        else:
            r["D_grid"] = False
        r["D_exit"] = p.wait(30)

        p = run(base + ["--template", "3x3 landscape", CLIPS])
        r["E_added"], r["E_message"] = added(), "found 6 of 9 landscape videos" in log_text()
        r["E_forced"] = "template rows" in log_text()

        p = run(base + ["--template", "nope", CLIPS])
        r["F_exit"], r["F_added"] = p.returncode, added()
        r["F_logged"] = "There is no template called" in log_text()

        # G. when_finished = "random" in a 2x2 template wall: ended videos are replaced by other 16:9 ones
        cfg_random = os.path.join(tmp, "random", "config.toml")
        os.makedirs(os.path.dirname(cfg_random))
        with open(cfg_random, "w") as f:
            f.write('when_finished = "random"\n')
        p = run(["--config", cfg_random, "--session", os.path.join(tmp, "s2.json"),
                 "--template", "2x2 landscape", CLIPS], exit_after=15)
        text = log_text()
        r["G_finished"] = text.count("finished (random)")
        r["G_swapped_to"] = [os.path.basename(m) for m in re.findall(r"file swapped: .* -> (.*)", text)]
        r["G_rows"], r["G_exit"] = last_rows(), p.returncode
        r["G_removed"] = text.count("tile removed")

        # H. one video, default "loop"; E switches to "next"; when it ends the next file plays
        p = run(base + [os.path.join(CLIPS, "land0.mkv")], wait=False, exit_after=14)
        procs.append(p)
        time.sleep(2.5)
        rects = eval(re.findall(r"rects=(\[.*?\]) panscan", log_text())[-1])
        x, y, w, h = rects[0]
        click(x + w // 2, y + h // 2)
        press(0x45)                                     # E
        r["H_mode"] = "when finished -> next" in log_text()
        r["H_exit"] = p.wait(30)
        swaps = re.findall(r"file swapped: .*?\\(\w+\.mkv) -> .*?\\(\w+\.mkv)", log_text())
        r["H_swaps"] = swaps

        # I. switch_seconds = 4 and random_start in a 2x2 wall: videos move on (16:9 only), start at random
        #    points, and the pinned (focused) video is never moved on
        cfg_switch = os.path.join(tmp, "switch", "config.toml")
        os.makedirs(os.path.dirname(cfg_switch))
        with open(cfg_switch, "w") as f:
            f.write("switch_seconds = 4\nrandom_start = true\n")
        p = run(["--config", cfg_switch, "--session", os.path.join(tmp, "s3.json"),
                 "--template", "2x2 landscape", CLIPS], wait=False, exit_after=13)
        procs.append(p)
        time.sleep(2.0)
        lines = re.findall(r"rects=(\[.*?\]) panscan", log_text())
        pinned = None
        if lines:
            x, y, w, h = eval(lines[-1])[0]
            click(x + w // 2, y + h // 2)
            first = re.findall(r"tile added #\d+ pipe=\S+ path=(.*)", log_text())
            pinned = first[0] if first else None
        r["I_exit"] = p.wait(30)
        text = log_text()
        moved = re.findall(r"moved on after 4s: (.*)", text)
        r["I_moved"] = len(moved)
        r["I_pinned_moved"] = bool(pinned) and any(m.strip() == pinned.strip() for m in moved)
        r["I_swapped_to"] = [os.path.basename(m) for m in re.findall(r"file swapped: .* -> (.*)", text)]
        r["I_random_starts"] = len(re.findall(r"random start (\d+)%", text))
        r["I_rows"] = last_rows()

        # J. a template whose bottom row takes portrait clips from a folder of its own (written relative to
        #    the chosen folder): two 16:9 videos from CLIPS above three from the extra folder
        extra = os.path.join(os.path.dirname(CLIPS), "tesserawall-p4-extra")
        os.makedirs(extra, exist_ok=True)
        for i in range(3):
            target = os.path.join(extra, f"xport{i}.mkv")
            if not os.path.exists(target):
                shutil.copyfile(os.path.join(CLIPS, f"port{i}.mkv"), target)
        cfg_two = os.path.join(tmp, "two", "config.toml")
        os.makedirs(os.path.dirname(cfg_two))
        open(cfg_two, "w").close()
        slot = "{ shape = \"portrait\", folder = '..\\tesserawall-p4-extra' }"
        with open(os.path.join(tmp, "two", "templates.toml"), "w", encoding="utf-8") as f:
            f.write(f'version = 1\n[[template]]\nname = "Two sources"\n'
                    f'rows = [["landscape", "landscape"], [{slot}, {slot}, {slot}]]\n')
        p = run(["--config", cfg_two, "--session", os.path.join(tmp, "s4.json"),
                 "--template", "Two sources", CLIPS])
        r["J_exit"], r["J_added"], r["J_rows"] = p.returncode, added(), last_rows()

        # K. a template with a folder on every slot shows in the start window and opens with one click
        cfg_ready = os.path.join(tmp, "ready", "config.toml")
        os.makedirs(os.path.dirname(cfg_ready))
        open(cfg_ready, "w").close()
        top = "{ shape = \"landscape\", folder = '%s' }" % CLIPS
        bottom = "{ shape = \"portrait\", folder = '%s' }" % extra
        with open(os.path.join(tmp, "ready", "templates.toml"), "w", encoding="utf-8") as f:
            f.write(f'version = 1\n[[template]]\nname = "Ready made"\n'
                    f'rows = [[{top}, {top}], [{bottom}, {bottom}, {bottom}]]\n')
        p = run(["--config", cfg_ready, "--session", os.path.join(tmp, "none.json")], wait=False, exit_after=12)
        procs.append(p)
        end, m = time.monotonic() + 8, None
        while time.monotonic() < end and not m:
            m = re.search(r"start window tpl0 at (-?\d+),(-?\d+) (\d+)x(\d+)", log_text())
            time.sleep(0.1)
        r["K_listed"] = bool(m)
        if m:
            x, y, w, h = map(int, m.groups())
            time.sleep(0.5)
            click(x + w // 2, y + h // 2)
            time.sleep(3)
        r["K_added"], r["K_rows"] = added(), last_rows()
        r["K_exit"] = p.wait(30)

        # L. the start window's "Use a template..." (folder dialog answered by the test variable): the
        #    chooser must actually appear over the start window, and a click on Best fit opens the wall
        if os.path.exists(LOG):
            os.remove(LOG)
        p = subprocess.Popen([sys.executable, APP, "--exit-after", "12", "--config", cfg,
                              "--session", os.path.join(tmp, "none.json")],
                             env=dict(os.environ, TESSERAWALL_TEST_PICK_FOLDER=CLIPS))
        procs.append(p)
        end, m = time.monotonic() + 8, None
        while time.monotonic() < end and not m:
            m = re.search(r"start window template at (-?\d+),(-?\d+) (\d+)x(\d+)", log_text())
            time.sleep(0.1)
        if m:
            x, y, w, h = map(int, m.groups())
            time.sleep(0.5)
            click(x + w // 2, y + h // 2)
        end, m = time.monotonic() + 8, None
        while time.monotonic() < end and not m:
            m = re.search(r"template window best at (-?\d+),(-?\d+) (\d+)x(\d+)", log_text())
            time.sleep(0.1)
        r["L_shown"] = "template window shown: True" in log_text()
        r["L_laid_out"] = bool(m) and int(m.group(3)) > 1
        if r["L_laid_out"]:
            x, y, w, h = map(int, m.groups())
            time.sleep(0.4)
            click(x + w // 2, y + h // 2)
            time.sleep(3)
        r["L_added"], r["L_rows"] = len(added()), last_rows()
        r["L_exit"] = p.wait(30)
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
    assert r["A_flat"], "a template wall starts without growth on hover"
    assert r["B_focus_moved_nothing"], "pointing at / pinning a video must not change the template's rects"
    assert r["D_grid"], "L to even grid keeps the template's rows: one full-width cell, then three equal ones"
    assert len(r["B_added"]) == 4 and port(r["B_added"]) and r["B_rows"] == "[4]" and r["B_cached"]
    assert r["B_swap"] and r["B_swap"][1].startswith("port") and r["B_swap"][1] not in r["B_added"], r["B_swap"]
    assert r["B_exit"] == 0
    assert r["C_plan"] and r["C_plan"][1:] == ("4", "[2, 2]") and land(r["C_added"]), r["C_plan"]
    assert r["D_listed"] == 8 and r["D_chosen"] and r["D_exit"] == 0
    assert r["D_added"][0].startswith("land") and port(r["D_added"][1:]) and r["D_rows"] == "[1, 3]"
    assert len(r["E_added"]) == 6 and land(r["E_added"]) and r["E_message"] and not r["E_forced"]
    assert r["F_exit"] == 0 and not r["F_added"] and r["F_logged"]
    assert r["G_finished"] >= 4 and r["G_swapped_to"] and land(r["G_swapped_to"]), "ended videos replaced, 16:9 only"
    assert r["G_rows"] == "[2, 2]" and r["G_exit"] == 0 and not r["G_removed"]
    assert r["H_mode"] and r["H_swaps"] and r["H_swaps"][0] == ("land0.mkv", "land1.mkv") and r["H_exit"] == 0
    assert r["I_exit"] == 0 and r["I_moved"] >= 2 and land(r["I_swapped_to"]), "videos move on, 16:9 only"
    assert not r["I_pinned_moved"], "the pinned video is never moved on"
    assert r["I_random_starts"] >= 4 + r["I_moved"], "every video (first and replacements) starts at random"
    assert r["I_rows"] == "[2, 2]"
    assert r["J_exit"] == 0 and r["J_rows"] == "[2, 3]" and len(r["J_added"]) == 5, r["J_added"]
    assert land(r["J_added"][:2]) and all(n.startswith("xport") for n in r["J_added"][2:]), \
        "bottom row from the slot folder, top row from the chosen folder"
    assert r["K_listed"] and r["K_exit"] == 0 and r["K_rows"] == "[2, 3]", "folder-free template in the start window"
    assert land(r["K_added"][:2]) and all(n.startswith("xport") for n in r["K_added"][2:]) and len(r["K_added"]) == 5
    assert r["L_shown"] and r["L_laid_out"], "the chooser opened from the start window must be visible"
    assert r["L_added"] == 4 and r["L_rows"] == "[2, 2]" and r["L_exit"] == 0, (r["L_added"], r["L_rows"])
    print("PHASE4 TEST PASSED")


if __name__ == "__main__":
    main()
