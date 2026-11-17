"""Drag-and-drop test. TAKES OVER THE MOUSE AND SCREEN for ~14 seconds. Run: python tests/drop_test.py

Needs `pip install tkinterdnd2`. Starts a wall, opens a small helper window that begins a real OLE
file drag when clicked, drags it with the real mouse onto the wall, and checks the file joined the wall.
The dropped file is a generated PNG with a space in its name (mpv shows images as a still tile).
"""
import ctypes
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes

ctypes.windll.shcore.SetProcessDpiAwareness(2)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(tempfile.gettempdir(), "tesserawall.log")
APP = os.path.join(ROOT, "tesserawall.pyw")
L = "av://lavfi:testsrc2=s=1280x720:r=30"
user32 = ctypes.windll.user32
user32.WindowFromPoint.restype = wintypes.HWND
user32.WindowFromPoint.argtypes = [wintypes.POINT]
LEFTDOWN, LEFTUP, MOVE = 0x0002, 0x0004, 0x0001

DRAG_SOURCE_PS1 = r"""
param($Path)
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$bmp = New-Object System.Drawing.Bitmap 640, 360
$g = [System.Drawing.Graphics]::FromImage($bmp); $g.Clear([System.Drawing.Color]::OrangeRed); $g.Dispose()
$bmp.Save($Path); $bmp.Dispose()
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
$t = New-Object System.Windows.Forms.Timer; $t.Interval = 12000; $t.Add_Tick({ $f.Close() }); $t.Start()
"ready" | Out-File "$Path.ready" -Encoding ascii
[System.Windows.Forms.Application]::Run($f)
"""


def main():
    print("WARNING: this test controls the mouse and covers the screen for ~14 seconds.")
    tmp = tempfile.mkdtemp(prefix="tesserawall-drop-")
    png = os.path.join(tmp, "drop test.png")
    ps1 = os.path.join(tmp, "dragsource.ps1")
    open(ps1, "w").write(DRAG_SOURCE_PS1)
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    orig = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(orig))

    wall = subprocess.Popen([sys.executable, APP, "--exit-after", "18", L, L])
    src = None
    try:
        time.sleep(2.5)
        src = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1, "-Path", png],
                               creationflags=subprocess.CREATE_NO_WINDOW)
        for _ in range(80):
            if os.path.exists(png + ".ready"):
                break
            time.sleep(0.1)
        owner = wintypes.DWORD()
        for _ in range(80):                                # wait until the helper window is really on screen
            user32.GetWindowThreadProcessId(user32.WindowFromPoint(wintypes.POINT(150, 170)), ctypes.byref(owner))
            if owner.value == src.pid:
                break
            time.sleep(0.1)
        tx, ty = sw * 5 // 8, sh * 5 // 8
        for attempt in range(3):   # a freshly shown helper window sometimes ignores the first press
            time.sleep(0.3)
            user32.SetCursorPos(150, 170)
            time.sleep(0.3)
            user32.mouse_event(LEFTDOWN, 0, 0, 0, 0)      # starts DoDragDrop in the helper
            time.sleep(0.4)
            for i in range(1, 31):
                user32.SetCursorPos(150 + (tx - 150) * i // 30, 170 + (ty - 170) * i // 30)
                user32.mouse_event(MOVE, 1, 0, 0, 0)
                user32.mouse_event(MOVE, -1, 0, 0, 0)
                time.sleep(0.03)
            time.sleep(0.6)
            user32.mouse_event(LEFTUP, 0, 0, 0, 0)        # drop on the wall
            time.sleep(0.8)
            if os.path.exists(png + ".result"):
                break
            print(f"attempt {attempt + 1}: helper did not start a drag, retrying")
        code = wall.wait(20)
    finally:
        user32.mouse_event(LEFTUP, 0, 0, 0, 0)
        user32.SetCursorPos(orig.x, orig.y)
        for p in (wall, src):
            if p and p.poll() is None:
                p.kill()

    log = open(LOG, encoding="utf-8", errors="replace").read()
    print(log)
    result = open(png + ".result").read().strip() if os.path.exists(png + ".result") else "(drag never started)"
    print("drop effect reported to the drag source:", result)
    assert code == 0, code
    assert "drag-and-drop enabled" in log, "tkinterdnd2 did not load inside the wall"
    assert "callback error" not in log
    assert f"path={png}" in log, "the dropped file was not added to the wall"
    assert log.count("tile added") == 3
    assert "tile removed" not in log, "the dropped tile died"
    print("DROP TEST PASSED")


if __name__ == "__main__":
    main()
