"""A frozen player must not freeze the wall. TAKES OVER THE MOUSE AND SCREEN for ~15 seconds.
Run: python tests/freeze_test.py

Suspends one mpv process (as if it hung), points at that video so the wall talks to it (seek bar,
focus, dimming), then points at another video and checks the wall reacts promptly. Before the wall
used pushed values and a background writer, every question to the frozen player blocked the
interface for up to 2 s, so the second focus change took several seconds.
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
LOG = os.path.join(tempfile.gettempdir(), "tesserawall.log")
APP = os.path.join(ROOT, "tesserawall.pyw")
L = "av://lavfi:testsrc2=s=1280x720:r=30"
user32, kernel32, ntdll = ctypes.windll.user32, ctypes.windll.kernel32, ctypes.windll.ntdll
kernel32.OpenProcess.restype = wintypes.HANDLE


def log_text():
    try:
        return open(LOG, encoding="utf-8", errors="replace").read()
    except FileNotFoundError:
        return ""


def rects():
    return eval(re.findall(r"rects=(\[.*?\]) panscan", log_text())[-1])


def move_to(r):
    x, y, w, h = r
    user32.SetCursorPos(x + w // 2 - 1, y + h // 2)
    time.sleep(0.05)
    user32.SetCursorPos(x + w // 2, y + h // 2)


def mpv_child_for_pipe(wall_pid, n):
    """The mpv process serving tile n (its command line names the pipe)."""
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          f"Get-CimInstance Win32_Process -Filter \"ParentProcessId={wall_pid} and Name='mpv.exe'\" | "
                          "ForEach-Object { \"$($_.ProcessId) $($_.CommandLine)\" }"],
                         capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW).stdout
    for line in out.splitlines():
        if f"tesserawall-{wall_pid}-{n} " in line + " ":
            return int(line.split()[0])
    return None


def suspend(pid, on):
    h = kernel32.OpenProcess(0x0800, False, pid)        # PROCESS_SUSPEND_RESUME
    (ntdll.NtSuspendProcess if on else ntdll.NtResumeProcess)(wintypes.HANDLE(h))
    kernel32.CloseHandle(wintypes.HANDLE(h))


def main():
    print("WARNING: this test controls the mouse and covers the screen for ~15 seconds.")
    orig = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(orig))
    r, frozen = {}, None
    if os.path.exists(LOG):
        os.remove(LOG)
    wall = subprocess.Popen([sys.executable, APP, "--exit-after", "16", L, L, L, L])
    try:
        time.sleep(3.0)
        frozen = mpv_child_for_pipe(wall.pid, 1)          # the player of tile 0
        assert frozen, "could not find tile 0's player"
        suspend(frozen, True)
        r["frozen_pid"] = frozen
        move_to(rects()[0])                               # focus the frozen video: seek bar, dimming...
        time.sleep(2.0)
        before = log_text().count("focus ->")
        t0 = time.monotonic()
        move_to(rects()[3])                               # now another video
        while log_text().count("focus ->") == before and time.monotonic() - t0 < 8:
            time.sleep(0.05)
        r["second_focus_after_s"] = round(time.monotonic() - t0, 2)
        r["focus_events"] = re.findall(r"focus -> (\d+)", log_text())
        suspend(frozen, False)
        frozen = None
        code = wall.wait(30)
        r["exit_code"] = code
    finally:
        if frozen:
            suspend(frozen, False)
        user32.SetCursorPos(orig.x, orig.y)
        if wall.poll() is None:
            wall.kill()
    for k, v in r.items():
        print(f"{k:22} {v}")
    log = log_text()
    assert "callback error" not in log
    assert r["focus_events"][:1] == ["0"], "pointing at the frozen video should still focus it"
    assert r["second_focus_after_s"] < 1.0, "the wall must react within a second despite a frozen player"
    assert r["exit_code"] == 0
    print("FREEZE TEST PASSED")


if __name__ == "__main__":
    main()
