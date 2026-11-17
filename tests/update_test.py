"""Update notice, end to end, against a local fake of GitHub's API (never the real one).
TAKES OVER MOUSE, KEYBOARD AND SCREEN for ~25 seconds. Run: python tests/update_test.py

  1. wall launch, server says v99.0.0     -> notice on a video after the launch hint; U "opens" the page
  2. start window, answer cached (1.)     -> link shown WITHOUT asking the server again; click "opens" it
  3. wall launch, server says the running version -> no notice
  4. wall launch, server unreachable      -> no notice, no crash
Browser opening is replaced by a log line (TESSERAWALL_NO_BROWSER=1). Settings and the update state live
in a temp folder (--config), so the user's real ones are never touched.
"""
import ctypes
import http.server
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from ctypes import wintypes

ctypes.windll.shcore.SetProcessDpiAwareness(2)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(tempfile.gettempdir(), "tesserawall.log")
APP = os.path.join(ROOT, "tesserawall.pyw")
L = "av://lavfi:testsrc2=s=1280x720:r=30"
REPO = "example/tesserawall"
user32 = ctypes.windll.user32
user32.GetForegroundWindow.restype = wintypes.HWND
KEYUP = 0x0002
CURRENT = open(os.path.join(ROOT, "VERSION")).read().strip()

reply = {"tag_name": "v99.0.0", "html_url": f"https://github.com/{REPO}/releases/tag/v99.0.0"}
requests = []


class Fake(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        requests.append((self.path, self.headers.get("User-Agent")))
        body = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def log_text():
    try:
        return open(LOG, encoding="utf-8", errors="replace").read()
    except FileNotFoundError:
        return ""


def wait_for(pattern, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        m = re.search(pattern, log_text())
        if m:
            return m
        time.sleep(0.1)
    return None


def foreground_is(pid):
    out = wintypes.DWORD()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(out))
    return out.value == pid


def run(cfg, api, args, exit_after):
    if os.path.exists(LOG):
        os.remove(LOG)
    env = dict(os.environ, TESSERAWALL_UPDATE_API=api, TESSERAWALL_NO_BROWSER="1")
    return subprocess.Popen([sys.executable, APP, "--exit-after", str(exit_after), "--config", cfg,
                             "--session", os.path.join(os.path.dirname(cfg), "none.json"), *args], env=env)


def main():
    print("WARNING: this test controls mouse, keyboard and screen for ~25 seconds.")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    api = f"http://127.0.0.1:{server.server_address[1]}/repos/{REPO}/releases/latest"
    tmp = tempfile.mkdtemp(prefix="tesserawall-update-")
    cfg = os.path.join(tmp, "config.toml")
    with open(cfg, "w") as f:
        f.write(f'update_repo = "{REPO}"\n')
    state = os.path.join(tmp, "update.json")
    orig = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(orig))
    r, procs = {}, []
    try:
        # 1. wall: notice, then U
        p = run(cfg, api, [L, L, L], 12); procs.append(p)
        r["notice"] = bool(wait_for(r"update notice shown on tile (\d+): 99\.0\.0"))
        lines = log_text().splitlines()
        hint_i = next((i for i, ln in enumerate(lines) if "tile added #1" in ln), None)
        notice_i = next((i for i, ln in enumerate(lines) if "update notice shown" in ln), None)
        r["notice_after_start"] = hint_i is not None and notice_i is not None and notice_i > hint_i
        user32.SetCursorPos(200, 200); time.sleep(0.2)
        user32.mouse_event(0x0002, 0, 0, 0, 0); user32.mouse_event(0x0004, 0, 0, 0, 0)   # take keyboard focus
        time.sleep(0.5)
        r["wall_foreground"] = foreground_is(p.pid)
        if r["wall_foreground"]:
            user32.keybd_event(0x55, 0, 0, 0); user32.keybd_event(0x55, 0, KEYUP, 0)       # U
            r["u_opens"] = bool(wait_for(r"would open download page: https://github\.com/example/tesserawall/releases/tag/v99\.0\.0", 3))
        r["exit1"] = p.wait(20)
        r["requests_after_1"] = len(requests)
        r["user_agent"] = requests[0][1] if requests else None
        r["state_saved"] = json.load(open(state)).get("latest") if os.path.exists(state) else None

        # 2. start window: cached answer, link, click
        p = run(cfg, api, [], 10); procs.append(p)
        m = wait_for(r"start window update at (-?\d+),(-?\d+) (\d+)x(\d+): 99\.0\.0")
        r["start_link"] = bool(m)
        r["requests_after_2"] = len(requests)
        if m:
            x, y, w, h = map(int, m.groups())
            time.sleep(0.4)
            sx, sy = x - 60, y - 520                          # whole lower part of the window, for a visual check
            shot = os.path.join(tempfile.gettempdir(), "tesserawall-update-link.png")
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Add-Type -AssemblyName System.Drawing;$bmp=New-Object System.Drawing.Bitmap 760,620;"
                            f"$g=[System.Drawing.Graphics]::FromImage($bmp);$g.CopyFromScreen({sx},{sy},0,0,$bmp.Size);"
                            f"$bmp.Save('{shot}')"], creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)
            user32.SetCursorPos(x + w // 2, y + h // 2); time.sleep(0.2)
            user32.mouse_event(0x0002, 0, 0, 0, 0); user32.mouse_event(0x0004, 0, 0, 0, 0)
            r["link_opens"] = bool(wait_for(r"would open download page: https://github\.com/example", 3))
        if p.poll() is None and foreground_is(p.pid):
            user32.keybd_event(0x1B, 0, 0, 0); user32.keybd_event(0x1B, 0, KEYUP, 0)          # Esc closes it
        r["exit2"] = p.wait(15)

        # 3. same version as running: no notice
        os.remove(state)
        reply["tag_name"] = "v" + CURRENT
        reply["html_url"] = f"https://github.com/{REPO}/releases/tag/v{CURRENT}"
        p = run(cfg, api, [L, L], 6); procs.append(p)
        r["exit3"] = p.wait(20)
        r["no_notice_when_current"] = "update notice shown" not in log_text() and "latest release is v" + CURRENT in log_text()

        # 4. server unreachable: no notice, no crash
        os.remove(state)
        p = run(cfg, "http://127.0.0.1:1/unreachable", [L, L], 6); procs.append(p)
        r["exit4"] = p.wait(20)
        r["offline_logged"] = "update check failed" in log_text() and "update notice shown" not in log_text()
    finally:
        user32.SetCursorPos(orig.x, orig.y)
        for q in procs:
            if q.poll() is None:
                q.kill()
        server.shutdown()
    for k, v in r.items():
        print(f"{k:26} {v}")
    assert r["notice"] and r["notice_after_start"]
    assert r["wall_foreground"] and r["u_opens"], "U should open the release page"
    assert r["requests_after_1"] == 1 and r["user_agent"] == f"TesseraWall/{CURRENT}"
    assert r["state_saved"] == "v99.0.0"
    assert r["start_link"] and r["link_opens"], "start window should show and open the update link"
    assert r["requests_after_2"] == 1, "second launch within a week must reuse the cached answer"
    assert r["no_notice_when_current"] and r["offline_logged"]
    assert [r["exit1"], r["exit2"], r["exit3"], r["exit4"]] == [0, 0, 0, 0]
    print("UPDATE TEST PASSED")


if __name__ == "__main__":
    main()
