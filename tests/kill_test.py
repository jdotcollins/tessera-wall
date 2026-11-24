"""A wall that is killed takes its players with it. COVERS THE SCREEN for ~10 seconds (no mouse or keys).
Run: python tests/kill_test.py

Opens a wall of 4 generated videos, waits until its 4 mpv players run, then kills the wall's process (as a
crash or Task Manager would: no chance to clean up) and checks that every player it started has ended
within a few seconds (winapi.end_with_app). Any player still running afterwards is ended by its PID.
"""
import ctypes
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "tesserawall.pyw")
L = "av://lavfi:testsrc2=s=1280x720:r=30"


def players_of(pid: int) -> list:
    """PIDs of the mpv processes started by process `pid`."""
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          f"(Get-CimInstance Win32_Process -Filter \"Name='mpv.exe' AND ParentProcessId={pid}\")"
                          ".ProcessId"], capture_output=True, text=True,
                         creationflags=subprocess.CREATE_NO_WINDOW, timeout=30).stdout
    return [int(x) for x in out.split()]


def alive(pid: int) -> bool:
    k32 = ctypes.windll.kernel32
    k32.OpenProcess.restype = ctypes.c_void_p
    h = k32.OpenProcess(0x1000, False, pid)
    if not h:
        return False
    try:
        code = ctypes.c_ulong()
        k32.GetExitCodeProcess(ctypes.c_void_p(h), ctypes.byref(code))
        return code.value == 259
    finally:
        k32.CloseHandle(ctypes.c_void_p(h))


def main():
    print("WARNING: this test covers the screen for ~10 seconds.")
    cfg = os.path.join(tempfile.mkdtemp(prefix="tesserawall-kill-"), "config.toml")
    wall = subprocess.Popen([sys.executable, APP, "--exit-after", "30", "--config", cfg, L, L, L, L],
                            env=dict(os.environ, TESSERAWALL_NO_DIALOGS="1"))
    players, r = [], {}
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and len(players) < 4:
            time.sleep(0.5)
            players = players_of(wall.pid)
        r["players_before"] = len(players)
        wall.kill()                                    # no quit, no cleanup: as a crash would end it
        wall.wait(5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and any(alive(p) for p in players):
            time.sleep(0.2)
        r["players_after"] = sum(alive(p) for p in players)
    finally:
        if wall.poll() is None:
            wall.kill()
        for p in players:                              # never by name: only the ones this wall started
            if alive(p):
                subprocess.run(["taskkill", "/F", "/PID", str(p)], capture_output=True)
    for k, v in r.items():
        print(f"{k:16} {v}")
    assert r["players_before"] == 4, "the wall should have started 4 players"
    assert r["players_after"] == 0, "a killed wall should take its players with it"
    print("KILL TEST PASSED")


if __name__ == "__main__":
    main()
