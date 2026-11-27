"""Hours-long soak: does the wall leak memory, handles or GDI objects, or players, in ordinary use?

TAKES OVER THE MOUSE, KEYBOARD AND SCREEN for the whole run (default 240 minutes).
Run: python tests/soak_test.py [--minutes 240]
No screen pixels are read, so it works with the monitor off. Uses generated clips; no network.

A six-video wall over a folder of ten 20-second clips with "when finished: next video", so files change on
their own. Every 15 s it does one thing a viewer does, in turn: point at another video, N, Page Down,
+ then -, clean mode on and off, help on and off, the right-click menu and Esc. Every 60 s it samples the
wall process (private bytes, handles, GDI and USER objects, threads) and its mpv players (count, private
bytes, handles), writing a CSV to %TEMP%. At the end it compares the first and last quarter-hour (after a
10-minute warm-up), quits with Q, and checks that no player is left running.
"""
import argparse
import csv
import ctypes
import json
import os
import random
import re
import statistics
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes

ctypes.windll.shcore.SetProcessDpiAwareness(2)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(tempfile.gettempdir(), "tesserawall.log")
APP = os.path.join(ROOT, "tesserawall.pyw")
user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
kernel32.OpenProcess.restype = wintypes.HANDLE
KEYUP, EXT = 0x0002, 0x0001
VK = {"N": 0x4E, "Next": 0x22, "Plus": 0xBB, "Minus": 0xBD, "I": 0x49, "H": 0x48, "Esc": 0x1B, "Q": 0x51}
EXTENDED = {"Next"}
# Allowed growth between the first and the last quarter-hour (medians), for a run of four hours.
LIMITS = {"wall_private_mb": 40, "wall_handles": 150, "wall_gdi": 60, "wall_user": 60, "wall_threads": 10,
          "mpv_count": 0, "mpv_handles": 300}


def make_clip(path, seconds=20, fps=10, w=320, h=180, shade=60):
    with open(path, "wb") as f:
        f.write(f"YUV4MPEG2 W{w} H{h} F{fps}:1 Ip A1:1 C420jpeg\n".encode())
        chroma = bytes([128]) * (w * h // 2)
        for i in range(seconds * fps):
            bar = w * i // (seconds * fps)
            f.write(b"FRAME\n" + (bytes([235]) * bar + bytes([shade]) * (w - bar)) * h + chroma)


def log_text():
    try:
        return open(LOG, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""


def rects():
    found = re.findall(r"rects=(\[.*?\]) panscan", log_text())
    return eval(found[-1]) if found else []


def press(name, pause=0.6):
    flag = EXT if name in EXTENDED else 0
    user32.keybd_event(VK[name], 0, flag, 0)
    user32.keybd_event(VK[name], 0, KEYUP | flag, 0)
    time.sleep(pause)


def move(x, y):
    user32.SetCursorPos(x - 1, y)
    time.sleep(0.05)
    user32.SetCursorPos(x, y)
    time.sleep(0.5)


def processes(wall_pid):
    """The wall and its players: {pid: (name, private bytes, handles, threads)} from Win32_Process."""
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -eq %d -or $_.ParentProcessId -eq %d } | "
          "ForEach-Object { @{pid=$_.ProcessId; name=$_.Name; priv=[int64]$_.PrivatePageCount; "
          "handles=$_.HandleCount; threads=$_.ThreadCount} } | ConvertTo-Json -Compress" % (wall_pid, wall_pid))
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                         creationflags=subprocess.CREATE_NO_WINDOW).stdout.strip()
    rows = json.loads(out) if out else []
    rows = [rows] if isinstance(rows, dict) else rows
    return {r["pid"]: (r["name"], r["priv"], r["handles"], r["threads"]) for r in rows}


def gui_objects(pid):
    h = kernel32.OpenProcess(0x1000, False, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return 0, 0
    try:
        return user32.GetGuiResources(h, 0), user32.GetGuiResources(h, 1)   # GDI, USER
    finally:
        kernel32.CloseHandle(h)


def players_for(wall_pid):
    """mpv processes anywhere whose IPC pipe names this wall (orphans keep the name after the wall goes)."""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='mpv.exe'\" | Where-Object { $_.CommandLine -like "
          "'*tesserawall-%d-*' } | ForEach-Object { $_.ProcessId }" % wall_pid)
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                         creationflags=subprocess.CREATE_NO_WINDOW).stdout.split()
    return [int(x) for x in out]


def sample(wall_pid, start):
    procs = processes(wall_pid)
    wall = procs.get(wall_pid)
    if not wall:
        return None
    mpvs = [v for k, v in procs.items() if k != wall_pid and v[0].lower() == "mpv.exe"]
    gdi, usr = gui_objects(wall_pid)
    return {"minute": round((time.monotonic() - start) / 60, 2),
            "wall_private_mb": round(wall[1] / 2**20, 1), "wall_handles": wall[2], "wall_threads": wall[3],
            "wall_gdi": gdi, "wall_user": usr, "mpv_count": len(mpvs),
            "mpv_private_mb": round(sum(m[1] for m in mpvs) / 2**20, 1), "mpv_handles": sum(m[2] for m in mpvs)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=240)
    args = ap.parse_args()
    minutes = args.minutes
    warm, window = min(10.0, minutes / 4), min(15.0, minutes / 4)
    print(f"WARNING: this test controls mouse, keyboard and screen for {minutes:g} minutes.")
    folder = tempfile.mkdtemp(prefix="tesserawall-soak-")
    clips = []
    for i in range(10):
        clips.append(os.path.join(folder, f"clip {i:02}.y4m"))
        make_clip(clips[-1], shade=30 + 15 * i)
    conf = os.path.join(folder, "config.toml")
    with open(conf, "w") as f:
        f.write('show_help_hint = false\nwhen_finished = "next"\nupdate_check = false\n')
    if os.path.exists(LOG):
        os.remove(LOG)
    out_csv = os.path.join(tempfile.gettempdir(), f"tesserawall-soak-{time.strftime('%Y%m%d-%H%M%S')}.csv")
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    orig = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(orig))
    move(sw // 2, sh // 2)
    wall = subprocess.Popen([sys.executable, APP, "--exit-after", str(int(minutes * 60 + 300)), "--config", conf,
                             *clips[:6]])
    rows, start = [], time.monotonic()
    rng = random.Random(1)
    actions = ["point", "N", "pagedown", "plusminus", "clean", "help", "menu"]
    step, next_action, next_sample = 0, start + 20, start + 30
    try:
        with open(out_csv, "w", newline="") as f:
            writer = None
            while time.monotonic() - start < minutes * 60:
                if wall.poll() is not None:
                    print(f"the wall exited early with {wall.returncode}")
                    break
                now = time.monotonic()
                if now >= next_sample:
                    s = sample(wall.pid, start)
                    if s:
                        rows.append(s)
                        writer = writer or csv.DictWriter(f, fieldnames=list(s))
                        if f.tell() == 0:
                            writer.writeheader()
                        writer.writerow(s)
                        f.flush()
                        if len(rows) % 30 == 0:
                            print(s, flush=True)
                    next_sample += 60
                if now >= next_action:
                    rs = rects()
                    what = actions[step % len(actions)]
                    step += 1
                    if rs:
                        x, y, w, h = rng.choice(rs)
                        move(x + w // 2, y + h // 2)
                    if what == "N":
                        press("N")
                    elif what == "pagedown":
                        press("Next")
                    elif what == "plusminus":
                        press("Plus", 2.0)
                        press("Minus")
                    elif what == "clean":
                        press("I", 2.0)
                        press("I")
                    elif what == "help":
                        press("H", 2.0)
                        press("H")
                    elif what == "menu":
                        user32.mouse_event(0x0008, 0, 0, 0, 0)
                        user32.mouse_event(0x0010, 0, 0, 0, 0)
                        time.sleep(1.5)
                        press("Esc")
                    next_action += 15
                time.sleep(0.5)
        press("Q")
        try:
            exit_code = wall.wait(30)
        except subprocess.TimeoutExpired:
            exit_code = None
        time.sleep(3)
        left = players_for(wall.pid)
    finally:
        if wall.poll() is None:
            wall.kill()
        user32.SetCursorPos(orig.x, orig.y)

    text = log_text()
    counts = {k: text.count(k) for k in ("callback error", "Traceback", "tile restarted", "tile failed",
                                          "memory guard", "tile removed")}
    print(f"\nsamples: {len(rows)}  csv: {out_csv}")
    print(f"exit: {exit_code}  players left running: {left}  log: {counts}  log size: {len(text) // 1024} KB")
    early = [r for r in rows if warm <= r["minute"] < warm + window]
    late = [r for r in rows if r["minute"] >= rows[-1]["minute"] - window] if rows else []
    failures = []
    if not early or not late:
        failures.append("not enough samples to compare")
    else:
        scale = max(1.0, (late[-1]["minute"] - early[0]["minute"]) / (240 - warm))
        for key, limit in LIMITS.items():
            a, b = statistics.median(r[key] for r in early), statistics.median(r[key] for r in late)
            grew = b - a
            ok = grew <= limit * scale
            print(f"{key:16} first {a:>9.1f}  last {b:>9.1f}  growth {grew:>+8.1f}  (limit {limit * scale:g})"
                  f"  {'ok' if ok else 'TOO MUCH'}")
            if not ok:
                failures.append(f"{key} grew by {grew:g}")
        peak = max(r["mpv_private_mb"] for r in rows)
        print(f"mpv private bytes: peak {peak:.0f} MB, last {late[-1]['mpv_private_mb']:.0f} MB")
    if exit_code != 0:
        failures.append(f"exit code {exit_code}")
    if left:
        failures.append(f"{len(left)} player(s) left running")
    if counts["callback error"] or counts["Traceback"]:
        failures.append("errors in the log")
    if failures:
        print("SOAK TEST FAILED: " + "; ".join(failures))
        sys.exit(1)
    print("SOAK TEST PASSED")


if __name__ == "__main__":
    main()
