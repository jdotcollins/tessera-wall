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
import queue
import threading
import time
from multiprocessing.connection import PipeConnection


class MpvIpc:
    """Two ways to talk to mpv:

    - Ask and wait: get(), set(), command(). Simple, but they block the caller until mpv answers
      (up to the timeout). Fine for tests and for quitting; never use them on the UI thread.
    - Never wait: post() / set_async() put the command on a queue that a background writer thread
      sends, and observe() asks mpv to push every change of a property, which the reader thread
      keeps in value(). A frozen or slow player then cannot stall the caller at all.

    Every message mpv sends gets an increasing sequence number, so callers can tell whether a
    property changed after an event (event_seq / value_seq), e.g. "is this size from the new file?".
    """

    def __init__(self, pipe: str, timeout: float = 10.0, handshake: bool = True):
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
        self._outbox: queue.Queue = queue.Queue(maxsize=512)
        self._seq = 0
        self._values: dict[str, tuple[object, int]] = {}   # property -> (latest value, seq)
        self._events: dict[str, tuple[int, int]] = {}      # event name -> (how many, seq of the last)
        self._observe_ids = itertools.count(1)
        self.dropped = 0                                    # posts thrown away because mpv stopped reading
        self.bad_lines = 0                                  # lines from mpv that were not JSON objects
        self.alive = True
        threading.Thread(target=self._reader, daemon=True).start()
        threading.Thread(target=self._writer, daemon=True).start()
        if handshake:
            self._handshake()
        else:
            self.post("client_name")   # a harmless first message: if mpv drops it, nothing is lost

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
                    try:
                        # mpv passes strings from file tags through as they are, so a title need not be
                        # valid UTF-8; one bad line must not end the connection.
                        msg = json.loads(line.decode("utf-8", "replace"))
                    except ValueError:
                        self.bad_lines += 1
                        continue
                    if not isinstance(msg, dict):
                        self.bad_lines += 1
                        continue
                    self._seq += 1
                    event = msg.get("event")
                    if event == "property-change":
                        self._values[msg.get("name")] = (msg.get("data"), self._seq)
                    elif event:
                        count, _ = self._events.get(event, (0, 0))
                        self._events[event] = (count + 1, self._seq)
                    else:
                        entry = self._pending.pop(msg.get("request_id"), None)
                        if entry:
                            entry[1].append(msg)
                            entry[0].set()
        except Exception:
            # EOFError / OSError: mpv closed the pipe. RuntimeError: close() cancelled a pending overlapped
            # read, which PipeConnection reports as "shouldn't get here". Either way the connection is over.
            pass
        self.alive = False
        for ev, _ in list(self._pending.values()):
            ev.set()
        try:
            self._outbox.put_nowait(None)   # wake the writer so it can finish
        except queue.Full:
            pass

    def _writer(self):
        while True:
            data = self._outbox.get()
            if data is None or not self.alive:
                return
            try:
                with self._write_lock:
                    self._conn.send_bytes(data)
            except (OSError, ValueError):
                self.alive = False
                return

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
        """Send a command WITHOUT waiting, not even for the write. Use from the UI thread.

        The command is queued and sent by the writer thread, in order. If mpv has stopped reading
        (frozen) and the queue is full, the command is dropped and counted in `dropped` rather than
        blocking: a frozen player must never freeze the wall."""
        if not self.alive:
            return
        payload = args[0] if len(args) == 1 and isinstance(args[0], dict) else list(args)
        try:
            self._outbox.put_nowait(json.dumps({"command": payload}).encode() + b"\n")
        except queue.Full:
            self.dropped += 1

    def observe(self, *props: str) -> None:
        """Ask mpv to push the current value of each property now and on every change."""
        for p in props:
            self.post("observe_property", next(self._observe_ids), p)

    def value(self, prop: str, default=None):
        """Latest pushed value of an observed property (never waits). None/default until known."""
        v = self._values.get(prop, (None, 0))[0]
        return default if v is None else v

    def value_seq(self, prop: str) -> int:
        """Sequence number of the last change of an observed property (0 = none yet)."""
        return self._values.get(prop, (None, 0))[1]

    def event_seq(self, event: str) -> int:
        """Sequence number of the last occurrence of an mpv event such as "file-loaded" (0 = none)."""
        return self._events.get(event, (0, 0))[1]

    @property
    def seq(self) -> int:
        """Sequence number of the latest message received; anything later is newer than now."""
        return self._seq

    def set_async(self, prop: str, value) -> None:
        self.post("set_property", prop, value)

    def get(self, prop: str):
        return self.command("get_property", prop)

    def set(self, prop: str, value):
        return self.command("set_property", prop, value)

    def close(self):
        self.alive = False
        try:
            self._outbox.put_nowait(None)
        except queue.Full:
            pass
        try:
            self._conn.close()
        except OSError:
            pass
