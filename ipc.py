"""Minimal mpv JSON-IPC client for Windows named pipes.

DONE AND TESTED against mpv 0.41 and 0.42 on this machine. Do not rewrite; import from it.
Keep the connect handshake (_handshake): without it mpv occasionally drops the first message.

Why it looks like this: a plain open(r'\\\\.\\pipe\\x', 'r+b') handle is synchronous, so a
blocked read in one thread blocks every write. The pipe MUST be opened with
FILE_FLAG_OVERLAPPED, which is what PipeConnection expects.

Usage:
    ipc = MpvIpc(r"\\\\.\\pipe\\tesserawall-1234-0")      # retries until mpv has created the pipe
    ipc.set("mute", False)
    aspect = ipc.get("video-params/aspect")          # None until the file has loaded
    ipc.command("seek", 5, "relative")
    ipc.close()
"""
from __future__ import annotations

import _winapi
import itertools
import json
import threading
import time
from multiprocessing.connection import PipeConnection


class MpvIpc:
    def __init__(self, pipe: str, timeout: float = 10.0):
        deadline = time.monotonic() + timeout
        while True:
            try:
                handle = _winapi.CreateFile(
                    pipe, _winapi.GENERIC_READ | _winapi.GENERIC_WRITE, 0, _winapi.NULL,
                    _winapi.OPEN_EXISTING, _winapi.FILE_FLAG_OVERLAPPED, _winapi.NULL)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.05)
        self._conn = PipeConnection(handle)
        self._ids = itertools.count(1)
        self._pending: dict[int, tuple[threading.Event, list]] = {}
        self._write_lock = threading.Lock()
        self.alive = True
        threading.Thread(target=self._reader, daemon=True).start()
        self._handshake()

    def _handshake(self, attempts: int = 4, timeout: float = 0.25):
        """Get one reply before any real command is sent.

        mpv on Windows (seen with 0.41 and 0.42) sometimes never processes the FIRST message on a
        new connection: about 1 in 50 connections while players are being resized. The next
        message always gets through, so a lost set_property or get_property would otherwise
        vanish silently (or block for the full timeout). "client_name" is answered by mpv's IPC
        thread without waiting for the player, so this costs ~1 ms when nothing is lost."""
        for _ in range(attempts):
            if self.command("client_name", timeout=timeout) is not None or not self.alive:
                return

    def _reader(self):
        buf = b""
        try:
            while True:
                buf += self._conn.recv_bytes(1 << 20)
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    msg = json.loads(line)
                    entry = self._pending.pop(msg.get("request_id"), None)
                    if entry and "event" not in msg:
                        entry[1].append(msg)
                        entry[0].set()
        except (EOFError, OSError, ValueError):
            pass
        self.alive = False
        for ev, _ in list(self._pending.values()):
            ev.set()

    def command(self, *args, timeout: float = 2.0):
        """Send a command and wait for mpv's reply. Returns the reply's 'data' (may be None).

        Never raises: returns None if mpv has exited, timed out, or reported an error.
        """
        if not self.alive:
            return None
        rid = next(self._ids)
        ev, slot = threading.Event(), []
        self._pending[rid] = (ev, slot)
        try:
            with self._write_lock:
                # A single dict argument is a named-argument command, e.g. {"name": "osd-overlay", ...}
                payload = args[0] if len(args) == 1 and isinstance(args[0], dict) else list(args)
                self._conn.send_bytes(json.dumps({"command": payload, "request_id": rid}).encode() + b"\n")
        except (OSError, ValueError):
            self.alive = False
            self._pending.pop(rid, None)
            return None
        if not ev.wait(timeout) or not slot:
            self._pending.pop(rid, None)
            return None
        reply = slot[0]
        return reply.get("data") if reply.get("error") == "success" else None

    def post(self, *args) -> None:
        """Send a command WITHOUT waiting for the reply. Use for state changes from the UI thread:
        mpv acknowledges some properties (brightness, vd-lavc-skipframe) only on the next video frame,
        which is 16-70 ms per call and adds up across players. Order on one pipe is preserved."""
        if not self.alive:
            return
        try:
            payload = args[0] if len(args) == 1 and isinstance(args[0], dict) else list(args)
            with self._write_lock:
                self._conn.send_bytes(json.dumps({"command": payload}).encode() + b"\n")
        except (OSError, ValueError):
            self.alive = False

    def set_async(self, prop: str, value) -> None:
        self.post("set_property", prop, value)

    def get(self, prop: str):
        return self.command("get_property", prop)

    def set(self, prop: str, value):
        return self.command("set_property", prop, value)

    def close(self):
        self.alive = False
        try:
            self._conn.close()
        except OSError:
            pass
