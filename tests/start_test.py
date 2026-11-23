"""Start window (the front door shown when tesserawall starts with nothing to play).
TAKES OVER MOUSE, KEYBOARD AND SCREEN for ~35 seconds. Run: python tests/start_test.py
Saves a screenshot of the window to %TEMP%\\tesserawall-start.png.

Three launches, each with no files:
  1. a saved session exists  -> click "Reopen last session" -> wall resumes at the saved positions
  2. no session              -> the resume button is absent; drop a file on the window -> wall opens it
  3. press Esc               -> exits without opening a wall
"""
import ctypes
import json
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
from ipc import MpvIpc

LOG = os.path.join(tempfile.gettempdir(), "tesserawall.log")
APP = os.path.join(ROOT, "tesserawall.pyw")
CLIP = os.path.join(tempfile.gettempdir(), "tesserawall-test-clip.y4m")   # made by features_test.py
user32 = ctypes.windll.user32
user32.GetForegroundWindow.restype = wintypes.HWND
user32.WindowFromPoint.restype = wintypes.HWND
user32.WindowFromPoint.argtypes = [wintypes.POINT]
LEFTDOWN, LEFTUP, MOVE, KEYUP = 0x0002, 0x0004, 0x0001, 0x0002

DRAG_SOURCE_PS1 = r"""
param($Path)
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$f = New-Object System.Windows.Forms.Form
$f.Text = 'drag source'; $f.StartPosition = 'Manual'; $f.TopMost = $true
$f.Location = New-Object System.Drawing.Point 50, 50
$f.Size = New-Object System.Drawing.Size 240, 240
$f.Add_MouseDown({
    $d = New-Object System.Windows.Forms.DataObject
    $files = New-Object System.Collections.Specialized.StringCollection
    [void]$files.Add($Path)
    $d.SetFileDropList($files)
    $r = $f.DoDragDrop($d, [System.Windows.Forms.DragDropEffects]::Copy)
    "$r" | Out-File "$Path.result" -Encoding ascii
    $f.Close()
})
$t = New-Object System.Windows.Forms.Timer; $t.Interval = 15000; $t.Add_Tick({ $f.Close() }); $t.Start()
[System.Windows.Forms.Application]::Run($f)
"""


def log_text():
    try:
        return open(LOG, encoding="utf-8", errors="replace").read()
    except FileNotFoundError:      # the app has not started logging yet
        return ""


def widgets():
    """{'videos': (x, y, w, h), ...} as logged by the start window."""
    return {k: tuple(map(int, v)) for k, *v in
            re.findall(r"start window (\w+) at (-?\d+),(-?\d+) (\d+)x(\d+)", log_text())}


def wait_for(fn, seconds=8.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.1)
    return fn()


def click(rect):
    x, y, w, h = rect
    user32.SetCursorPos(x + w // 2 - 1, y + h // 2)
    time.sleep(0.05)
    user32.SetCursorPos(x + w // 2, y + h // 2)
    time.sleep(0.2)
    user32.mouse_event(LEFTDOWN, 0, 0, 0, 0)
    user32.mouse_event(LEFTUP, 0, 0, 0, 0)


def shot(path, rect):
    x, y, w, h = rect
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Add-Type -AssemblyName System.Drawing;"
                    f"$bmp=New-Object System.Drawing.Bitmap {w},{h};"
                    f"$g=[System.Drawing.Graphics]::FromImage($bmp);$g.CopyFromScreen({x},{y},0,0,$bmp.Size);"
                    f"$bmp.Save('{path}')"], creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)


def launch(session, extra=()):
    if os.path.exists(LOG):
        os.remove(LOG)
    return subprocess.Popen([sys.executable, APP, "--exit-after", "8", "--session", session, *extra])


def main():
    assert os.path.exists(CLIP), "run tests/features_test.py once first; it generates the clip"
    print("WARNING: this test controls mouse, keyboard and screen for ~35 seconds.")
    tmp = tempfile.mkdtemp(prefix="tesserawall-start-")
    orig = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(orig))
    r = {}
    procs = []
    try:
        # 1. saved session -> Reopen last session
        session = os.path.join(tmp, "session.json")
        json.dump({"videos": [{"path": CLIP, "pos": 12.0}, {"path": CLIP, "pos": 3.0}]}, open(session, "w"))
        p = launch(session); procs.append(p)
        w = wait_for(lambda: widgets() if "resume" in widgets() else None)
        r["widgets_with_session"] = sorted(w or {})
        r["dark_title_bar"] = "start window dark title bar: True" in log_text()
        time.sleep(0.6)
        x0 = min(v[0] for v in w.values()); y1 = max(v[1] + v[3] for v in w.values())
        shot(os.path.join(tempfile.gettempdir(), "tesserawall-start.png"), (x0 - 40, w["drop"][1] - 130, w["drop"][2] + 80, y1 - w["drop"][1] + 200))
        click(w["resume"])
        time.sleep(3.0)
        r["resume_tiles"] = log_text().count("tile added")
        pos = []
        for n in (1, 2):
            c = MpvIpc(rf"\\.\pipe\tesserawall-{p.pid}-{n}", timeout=3)
            pos.append(c.get("time-pos")); c.close()
        r["resume_positions"] = [round(v, 1) if v is not None else None for v in pos]
        r["resume_exit"] = p.wait(20)

        # 2. no session -> no resume button; drop a file on the window
        p = launch(os.path.join(tmp, "none.json")); procs.append(p)
        w = wait_for(lambda: widgets() if "drop" in widgets() else None)
        time.sleep(0.8)
        r["widgets_without_session"] = sorted(widgets())
        ps1 = os.path.join(tmp, "dragsource.ps1")
        open(ps1, "w").write(DRAG_SOURCE_PS1)
        src = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1, "-Path", CLIP],
                               creationflags=subprocess.CREATE_NO_WINDOW)
        procs.append(src)
        owner = wintypes.DWORD()
        for _ in range(80):
            user32.GetWindowThreadProcessId(user32.WindowFromPoint(wintypes.POINT(150, 170)), ctypes.byref(owner))
            if owner.value == src.pid:
                break
            time.sleep(0.1)
        tx, ty = w["drop"][0] + w["drop"][2] // 2, w["drop"][1] + w["drop"][3] // 2
        for attempt in range(3):
            time.sleep(0.3)
            user32.SetCursorPos(150, 170)
            time.sleep(0.3)
            user32.mouse_event(LEFTDOWN, 0, 0, 0, 0)
            time.sleep(0.4)
            for i in range(1, 31):
                user32.SetCursorPos(150 + (tx - 150) * i // 30, 170 + (ty - 170) * i // 30)
                user32.mouse_event(MOVE, 1, 0, 0, 0)
                user32.mouse_event(MOVE, -1, 0, 0, 0)
                time.sleep(0.03)
            time.sleep(0.6)
            user32.mouse_event(LEFTUP, 0, 0, 0, 0)
            time.sleep(0.8)
            if os.path.exists(CLIP + ".result"):
                os.remove(CLIP + ".result")
                break
        time.sleep(2.5)
        r["drop_tiles"] = log_text().count("tile added")
        r["drop_zone_lit"] = "drop zone highlighted" in log_text()
        r["drop_exit"] = p.wait(20)

        # 3. Esc closes the start window without opening a wall
        p = launch(os.path.join(tmp, "none.json")); procs.append(p)
        wait_for(lambda: "drop" in widgets())
        time.sleep(0.8)
        out = wintypes.DWORD()
        user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(out))
        r["start_window_is_foreground"] = out.value == p.pid
        if r["start_window_is_foreground"]:
            user32.keybd_event(0x1B, 0, 0, 0)
            user32.keybd_event(0x1B, 0, KEYUP, 0)
            r["esc_exit"] = p.wait(10)
            r["esc_tiles"] = log_text().count("tile added")
    finally:
        user32.mouse_event(LEFTUP, 0, 0, 0, 0)
        user32.SetCursorPos(orig.x, orig.y)
        for q in procs:
            if q.poll() is None:
                q.kill()
    for k, v in r.items():
        print(f"{k:28} {v}")
    assert r["widgets_with_session"] == ["drop", "folder", "resume", "search", "settings", "template", "videos"]
    assert r["resume_tiles"] == 2 and r["resume_exit"] == 0
    assert r["resume_positions"][0] >= 11 and r["resume_positions"][1] < 11, "resume should restore positions"
    assert r["widgets_without_session"] == ["drop", "folder", "search", "settings", "template", "videos"], "no resume button without a session"
    assert r["drop_tiles"] == 1 and r["drop_exit"] == 0, "dropping a file on the start window should open it"
    assert r["dark_title_bar"], "the start window should get a dark title bar"
    assert r["drop_zone_lit"], "dragging files over the window should light up the drop zone"
    assert r["start_window_is_foreground"] and r["esc_exit"] == 0 and r["esc_tiles"] == 0
    print("START TEST PASSED")


if __name__ == "__main__":
    main()
