"""GPU-driver stress test. TAKES OVER THE MOUSE AND SCREEN for ~45 seconds. Run: python tests/stress_test.py

Starts and closes a 6-video wall three times while sweeping the pointer across it so the grid reflows
constantly, then reports whether the AMD driver-timeout popup (AMDBugReportForm.exe) appeared, whether
Radeon Software restarted, and whether any mpv log mentions a lost device.
Set TESSERAWALL_UNSAFE_RENDERING=1 to run with the original HDR flip-model swapchains for comparison.
"""
import ctypes
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import time

ctypes.windll.shcore.SetProcessDpiAwareness(2)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "tesserawall.pyw")
LOG = os.path.join(tempfile.gettempdir(), "tesserawall.log")
LOGDIR = os.path.join(tempfile.gettempdir(), "tesserawall-mpvlogs")
L, P = "av://lavfi:testsrc2=s=1280x720:r=30", "av://lavfi:testsrc2=s=720x1280:r=30"
user32 = ctypes.windll.user32
WATCH = "AMDBugReportForm", "AMDBugReportTool", "RadeonSoftware", "AMDRSServ"


def amd_processes():
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-Process " + ",".join(WATCH) + " -ErrorAction SilentlyContinue | "
                          "ForEach-Object { \"$($_.ProcessName) $($_.Id)\" }"],
                         capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW).stdout
    return set(out.split("\n")) - {""}


def main():
    print("WARNING: this test controls the mouse and covers the screen for ~45 seconds.")
    shutil.rmtree(LOGDIR, ignore_errors=True)
    env = dict(os.environ, TESSERAWALL_MPV_LOG_DIR=LOGDIR)
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    before = amd_processes()
    focus_changes, codes = 0, []
    for cycle in range(3):
        wall = subprocess.Popen([sys.executable, APP, "--exit-after", "13", L, P, L, L, P, L], env=env)
        time.sleep(3)
        t0, i = time.monotonic(), 0
        while time.monotonic() - t0 < 8:          # zig-zag over the whole wall, pausing on each stop
            i += 1
            x = sw * (1 + (i * 3) % 8) // 9
            y = sh * (1 + (i * 5) % 6) // 7
            user32.SetCursorPos(x - 1, y)
            time.sleep(0.03)
            user32.SetCursorPos(x, y)
            time.sleep(0.22)
        codes.append(wall.wait(20))
        focus_changes += open(LOG, encoding="utf-8", errors="replace").read().count("focus ->")
        time.sleep(0.5)
    time.sleep(3)                                  # the popup can lag the timeout by a few seconds
    new = sorted(p.strip() for p in amd_processes() - before)

    lost, flip, fmt = [], set(), set()
    for f in glob.glob(os.path.join(LOGDIR, "*.log")):
        for line in open(f, encoding="utf-8", errors="replace"):
            low = line.lower()
            if "device" in low and ("lost" in low or "removed" in low or "hung" in low) or "dxgi_error" in low:
                lost.append(line.strip()[:160])
            if "presentation" in low:
                flip.add(line.split("]")[-1].strip())
            if "selected swapchain format" in low:
                fmt.add(line.split("]")[-1].strip())
    print("wall exit codes:        ", codes)
    print("focus changes (reflows):", focus_changes)
    print("presentation mode:      ", sorted(flip))
    print("swapchain format:       ", sorted(fmt))
    print("device-lost log lines:  ", lost[:5] or "none")
    print("new AMD processes:      ", new or "none")
    assert all(c == 0 for c in codes) and focus_changes > 30
    assert not lost and not new, "driver timeout detected"
    print("STRESS TEST PASSED (no driver timeout)")


if __name__ == "__main__":
    main()
