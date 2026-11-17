"""Speed, frame stepping, A-B loop, subtitle/audio tracks, snapshots, crash restart.
TAKES OVER MOUSE, KEYBOARD AND SCREEN for ~40 seconds. Run: python tests/phase2_test.py
Keys are only sent while the wall is the foreground window. Uses its own settings file (--config),
so snapshots go to a temp folder, never to your Pictures folder.
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
from ipc import MpvIpc

LOG = os.path.join(tempfile.gettempdir(), "tesserawall.log")
APP = os.path.join(ROOT, "tesserawall.pyw")
CLIP = os.path.join(tempfile.gettempdir(), "tesserawall-test-clip.y4m")   # 30 s, 10 fps; made by features_test.py
user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
user32.GetForegroundWindow.restype = wintypes.HWND
kernel32.OpenProcess.restype = wintypes.HANDLE
KEYUP = 0x0002
VK = {"[": 0xDB, "]": 0xDD, "BACK": 0x08, ",": 0xBC, ".": 0xBE, "B": 0x42, "T": 0x54, "A": 0x41, "P": 0x50,
      "Q": 0x51}


def log_text():
    try:
        return open(LOG, encoding="utf-8", errors="replace").read()
    except FileNotFoundError:
        return ""


def key(name, pause=0.35):
    user32.keybd_event(VK[name], 0, 0, 0)
    user32.keybd_event(VK[name], 0, KEYUP, 0)
    time.sleep(pause)


def player_pid(wall_pid, pipe_suffix):
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          f"Get-CimInstance Win32_Process -Filter \"ParentProcessId={wall_pid} and Name='mpv.exe'\" | "
                          "ForEach-Object { \"$($_.ProcessId) $($_.CommandLine)\" }"],
                         capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW).stdout
    for line in out.splitlines():
        if f"tesserawall-{wall_pid}-{pipe_suffix} " in line + " ":
            return int(line.split()[0])
    return None


def crash(pid):
    h = kernel32.OpenProcess(0x0001, False, pid)          # PROCESS_TERMINATE
    kernel32.TerminateProcess(wintypes.HANDLE(h), 1)      # exit code 1, like a crash
    kernel32.CloseHandle(wintypes.HANDLE(h))


def wait_for(pattern, count, seconds=8.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        found = re.findall(pattern, log_text())
        if len(found) >= count:
            return found
        time.sleep(0.1)
    return re.findall(pattern, log_text())


def main():
    assert os.path.exists(CLIP), "run tests/features_test.py once first; it generates the clip"
    print("WARNING: this test controls mouse, keyboard and screen for ~40 seconds.")
    tmp = tempfile.mkdtemp(prefix="tesserawall-p2-")
    clip = os.path.join(tmp, "clip.y4m")
    shutil.copyfile(CLIP, clip)
    with open(os.path.join(tmp, "clip.srt"), "w", encoding="utf-8") as f:   # loaded automatically by mpv
        f.write("1\n00:00:00,000 --> 00:00:30,000\nHello from a subtitle\n")
    shots = os.path.join(tmp, "snapshots")
    cfg = os.path.join(tmp, "config.toml")
    with open(cfg, "w", encoding="utf-8") as f:
        f.write(f'snapshot_dir = "{shots.replace(chr(92), chr(92) * 2)}"\n')
    orig = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(orig))
    r = {}
    if os.path.exists(LOG):
        os.remove(LOG)
    wall = subprocess.Popen([sys.executable, APP, "--exit-after", "45", "--config", cfg, clip, clip])
    try:
        time.sleep(3.5)
        p1 = MpvIpc(rf"\\.\pipe\tesserawall-{wall.pid}-1", timeout=3)
        rects = eval(re.findall(r"rects=(\[.*?\]) panscan", log_text())[-1])
        x, y, w, h = rects[0]
        user32.SetCursorPos(x + w // 2 - 1, y + h // 2)
        time.sleep(0.05)
        user32.SetCursorPos(x + w // 2, y + h // 2)
        time.sleep(0.7)
        user32.mouse_event(0x0002, 0, 0, 0, 0)            # click: pin video 0, keyboard focus to the wall
        user32.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(0.5)
        out = wintypes.DWORD()
        user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(out))
        assert out.value == wall.pid, "wall is not the foreground window; not sending keys"

        key("]"); key("]")
        r["speed_after_2_faster"] = round(p1.get("speed"), 3)
        key("[")
        r["speed_after_slower"] = round(p1.get("speed"), 3)
        key("BACK")
        r["speed_after_reset"] = round(p1.get("speed"), 3)

        t0 = p1.get("time-pos")
        key(".", 0.6)
        t1, paused = p1.get("time-pos"), p1.get("pause")
        key(",", 0.8)
        t2 = p1.get("time-pos")
        r["frame_step"] = (round(t1 - t0, 2), paused, round(t2 - t1, 2))
        p1.set("pause", False)

        key("B", 0.6); key("B")
        r["loop_set"] = (p1.get("ab-loop-a"), p1.get("ab-loop-b"))
        key("B")
        r["loop_cleared"] = (p1.get("ab-loop-a"), p1.get("ab-loop-b"))

        r["sid_before"] = p1.get("sid")
        key("T")
        r["sid_after"] = p1.get("sid")
        key("A")
        r["aid_after_audio_key"] = p1.get("aid")              # no audio track: must stay "no", not break

        key("P", 1.5)
        files = os.listdir(shots) if os.path.isdir(shots) else []
        r["snapshot_files"] = files
        if files:
            with open(os.path.join(shots, files[0]), "rb") as f:
                r["snapshot_is_png"] = f.read(8) == b"\x89PNG\r\n\x1a\n"

        # Crash the OTHER video's player (pipe 2): it should come back in place, near the same moment.
        p2 = MpvIpc(rf"\\.\pipe\tesserawall-{wall.pid}-2", timeout=3)
        before = p2.get("time-pos")
        crash(player_pid(wall.pid, 2))
        restarted = wait_for(r"tile restarted \(mpv exited with 1\) at [\d.]+s pipe=\\\\\.\\pipe\\tesserawall-\d+-(\d+)", 1)
        r["restarted"] = bool(restarted)
        if restarted:
            n = restarted[-1]
            time.sleep(1.5)
            pn = MpvIpc(rf"\\.\pipe\tesserawall-{wall.pid}-{n}", timeout=3)
            after = pn.get("time-pos")
            r["resume_position"] = (round(before, 1), round(after or -1, 1))
            for i in range(3):                             # three more crashes inside a minute
                crash(player_pid(wall.pid, n))
                more = wait_for(r"tile restarted .*?pipe=\\\\\.\\pipe\\tesserawall-\d+-(\d+)", 2 + i, 6)
                if len(more) >= 2 + i:
                    n = more[-1]
                    time.sleep(1.0)
            r["restarts_total"] = len(re.findall(r"tile restarted", log_text()))
            r["gave_up"] = "not restarting again" in log_text()
            r["tiles_left"] = log_text().count("tile added") - log_text().count("tile removed")
        key("Q", 1.5)
        code = wall.wait(30)
    finally:
        user32.SetCursorPos(orig.x, orig.y)
        if wall.poll() is None:
            wall.kill()
    for k, v in r.items():
        print(f"{k:22} {v}")
    log = log_text()
    assert code == 0 and "callback error" not in log
    assert abs(r["speed_after_2_faster"] - 1.21) < 0.01 and abs(r["speed_after_slower"] - 1.1) < 0.01
    assert r["speed_after_reset"] == 1.0
    step, paused, back = r["frame_step"]
    assert paused is True and 0.05 <= step <= 0.25 and back < 0, "frame step should pause and move ~one frame"
    assert all(isinstance(v, (int, float)) for v in r["loop_set"]) and r["loop_set"][1] > r["loop_set"][0]
    assert r["loop_cleared"] == ("no", "no")
    assert r["sid_before"] == 1 and r["sid_after"] is False, "T should switch the subtitle off (only one track)"
    assert r["aid_after_audio_key"] is False
    assert len(r["snapshot_files"]) == 1 and r.get("snapshot_is_png"), "P should save one PNG"
    assert r["restarted"] and r["resume_position"][1] >= r["resume_position"][0] - 1.5, "restart in place"
    assert r["restarts_total"] == 3 and r["gave_up"] and r["tiles_left"] == 1, "3 restarts, then give up"
    print("PHASE2 TEST PASSED")


if __name__ == "__main__":
    main()
