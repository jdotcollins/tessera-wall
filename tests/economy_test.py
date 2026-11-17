"""Economy mode (unfocused videos skip B-frames). TAKES OVER THE MOUSE AND SCREEN for ~30 seconds.
Run: python tests/economy_test.py

Uses a real 1080p60 H.264 clip with B-frames, generated once into %TEMP% with mpv's encoder. That
encoder hangs on exit with this mpv build, so the helper waits for the file to stop growing and then
stops its own encoder process.
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
sys.path.insert(0, ROOT)
import config as C
from ipc import MpvIpc

APP = os.path.join(ROOT, "tesserawall.pyw")
LOG = os.path.join(tempfile.gettempdir(), "tesserawall.log")
CLIP = os.path.join(tempfile.gettempdir(), "tesserawall-h264-test.mkv")
N = 6
SKIP = "bidir"
user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
kernel32.OpenProcess.restype = wintypes.HANDLE


def make_clip():
    if os.path.exists(CLIP) and os.path.getsize(CLIP) > 1_000_000:
        return
    enc = subprocess.Popen([C.find_mpv(), "--no-config", "--no-terminal", "av://lavfi:testsrc2=s=1920x1080:r=60:d=20",
                            f"--o={CLIP}", "--of=matroska", "--ovc=libx264",
                            "--ovcopts=preset=veryfast,bf=3,g=120,crf=20", "--no-audio"])
    last, stable = -1, 0
    for _ in range(120):
        time.sleep(0.5)
        size = os.path.getsize(CLIP) if os.path.exists(CLIP) else 0
        stable = stable + 1 if (size == last and size > 0) else 0
        last = size
        if enc.poll() is not None or stable >= 6:
            break
    if enc.poll() is None:
        enc.kill()


def cpu_seconds(pid):
    h = kernel32.OpenProcess(0x1000, False, pid)
    c, e, kt, ut = (wintypes.FILETIME() for _ in range(4))
    kernel32.GetProcessTimes(wintypes.HANDLE(h), ctypes.byref(c), ctypes.byref(e), ctypes.byref(kt), ctypes.byref(ut))
    kernel32.CloseHandle(wintypes.HANDLE(h))
    return sum(((t.dwHighDateTime << 32) | t.dwLowDateTime) / 1e7 for t in (kt, ut))


def gpu_percent(pids):
    """Sum of GPU engine utilisation for the given processes, by engine type (informational)."""
    ps = ("$p=@(%s); (Get-Counter '\\GPU Engine(*)\\Utilization Percentage' -ErrorAction SilentlyContinue).CounterSamples | "
          "Where-Object { $n=$_.InstanceName; $p | Where-Object { $n -like \"pid_$($_)_*\" } } | "
          "Group-Object { ($_.InstanceName -split 'engtype_')[1] } | "
          "ForEach-Object { '{0}={1:N1}' -f $_.Name, ($_.Group | Measure-Object CookedValue -Sum).Sum }"
          % ",".join(map(str, pids)))
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                         creationflags=subprocess.CREATE_NO_WINDOW).stdout.split()
    return " ".join(x for x in out if not x.endswith("=0.0")) or "(no data)"


def move(x, y):
    user32.SetCursorPos(x - 1, y)
    time.sleep(0.05)
    user32.SetCursorPos(x, y)


def main():
    print("WARNING: this test controls the mouse and covers the screen for ~30 seconds.")
    make_clip()
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    orig = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(orig))
    wall = subprocess.Popen([sys.executable, APP, "--exit-after", "30"] + [CLIP] * N,
                            env=dict(os.environ, TESSERAWALL_UNFOCUSED_SKIP=SKIP))   # off by default in config.py
    try:
        time.sleep(5)
        ipc = [MpvIpc(rf"\\.\pipe\tesserawall-{wall.pid}-{n}", timeout=3) for n in range(1, N + 1)]
        pids = [c.get("pid") for c in ipc]

        def sample(label, seconds=5.0):
            time.sleep(1.5)                                   # let fps estimates settle
            c0, t0 = sum(map(cpu_seconds, pids)), time.monotonic()
            gpu = gpu_percent(pids)
            time.sleep(seconds)
            cpu = (sum(map(cpu_seconds, pids)) - c0) / (time.monotonic() - t0) * 100
            skips = [c.get("vd-lavc-skipframe") for c in ipc]
            fps = [round(c.get("estimated-vf-fps") or 0) for c in ipc]
            print(f"{label:26} mpv CPU {cpu:5.1f}% of one core | fps {fps} | skip {skips} | GPU {gpu}")
            return cpu, skips, fps

        idle = sample("nothing focused")
        move(sw // 6, sh // 4)                                # hover the first video
        time.sleep(0.6)
        focused = sample("first video focused")
        mutes = [c.get("mute") for c in ipc]
        move(sw * 5 // 6, sh * 3 // 4)                        # hover a different video
        time.sleep(0.6)
        skips_after = [c.get("vd-lavc-skipframe") for c in ipc]
        time.sleep(2.0)                                       # well past the audio crossfade
        mutes_after = [c.get("mute") for c in ipc]
        fps_after = [round(c.get("estimated-vf-fps") or 0) for c in ipc]
        hw = ipc[0].get("hwdec-current")
        code = wall.wait(40)
    finally:
        user32.SetCursorPos(orig.x, orig.y)
        if wall.poll() is None:
            wall.kill()

    print("hwdec:", hw, "| after moving focus: skip", skips_after, "fps", fps_after)
    print(f"decode+render CPU saving while focused: {100 * (1 - focused[0] / idle[0]):.0f}%")
    assert code == 0 and "callback error" not in open(LOG, encoding="utf-8", errors="replace").read()
    assert idle[1] == ["default"] * N and all(f >= 50 for f in idle[2]), "nothing focused: everything full rate"
    f1 = mutes.index(False)
    assert focused[1] == ["default" if i == f1 else SKIP for i in range(N)]
    assert focused[2][f1] >= 50 and all(f <= 35 for i, f in enumerate(focused[2]) if i != f1), "unfocused should slow down"
    assert skips_after.count("default") == 1, "exactly one video should decode fully"
    f2 = skips_after.index("default")
    assert f2 != f1 and skips_after == ["default" if i == f2 else SKIP for i in range(N)]
    assert mutes_after == [i != f2 for i in range(N)], "audio should have followed the focus"
    assert fps_after[f2] >= 50, "newly focused video must return to full frame rate"
    # The CPU saving is printed, not asserted: with hardware decoding it varied from 14% to 64% between
    # identical runs on this machine, so a threshold would only make the test flaky.
    print("ECONOMY TEST PASSED")


if __name__ == "__main__":
    main()
