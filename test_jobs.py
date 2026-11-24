"""Run with: python test_jobs.py   (no pytest, no display, no mpv; Windows; exits non-zero on failure)
Covers winapi.end_with_app: a player put in the job ends when the app is killed, and so does anything the
player started (as yt-dlp is started by mpv). A control run without the job shows they would otherwise
survive. Uses stand-in Python processes; every process it starts it also ends, by PID."""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))

# The stand-in app: starts a "player" that itself starts a child, optionally puts the player in the job,
# prints both PIDs, then waits to be killed.
APP = r"""
import subprocess, sys, time
sys.path.insert(0, {here!r})
from winapi import end_with_app
player = subprocess.Popen([sys.executable, "-c",
    "import subprocess, sys, time; c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
    " print(c.pid, flush=True); time.sleep(60)"], stdout=subprocess.PIPE, text=True,
    creationflags=subprocess.CREATE_NO_WINDOW)
joined = end_with_app(player.pid) if {use_job} else False
child = player.stdout.readline().strip()
print(player.pid, child, joined, flush=True)
time.sleep(60)
"""

k32 = ctypes.windll.kernel32
k32.OpenProcess.restype = wintypes.HANDLE


def alive(pid: int) -> bool:
    h = k32.OpenProcess(0x1000, False, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        code = wintypes.DWORD()
        k32.GetExitCodeProcess(h, ctypes.byref(code))
        return code.value == 259                        # STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


def end(pid: int):
    h = k32.OpenProcess(0x0001, False, pid)            # PROCESS_TERMINATE
    if h:
        k32.TerminateProcess(h, 1)
        k32.CloseHandle(h)


def run(use_job: bool):
    """Start the stand-in app, kill it, and return (joined, player alive, its child alive) a moment later."""
    app = subprocess.Popen([sys.executable, "-c", APP.format(here=HERE, use_job=use_job)],
                           stdout=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    player = child = None
    try:
        player, child, joined = app.stdout.readline().split()
        player, child = int(player), int(child)
        assert alive(player) and alive(child), "the stand-ins should be running before the app is killed"
        app.kill()                                     # the app dies without a chance to clean up
        app.wait(5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and (alive(player) or alive(child)):
            time.sleep(0.1)
        return joined == "True", alive(player), alive(child)
    finally:
        if app.poll() is None:
            app.kill()
        for pid in (player, child):
            if pid:
                end(pid)


def test_players_end_with_a_killed_app():
    joined, player, child = run(use_job=True)
    assert joined, "the player should have been put in the job"
    assert not player, "a player in the job should end when the app is killed"
    assert not child, "what the player started should end too"


def test_without_the_job_they_survive():
    joined, player, child = run(use_job=False)
    assert not joined and player and child, "control: outside the job, the stand-ins outlive the app"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
