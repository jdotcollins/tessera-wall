"""One wall per Windows user: later launches hand their files to the running wall over a named pipe."""
import json
import logging
import os
import queue
import time
from multiprocessing.connection import Client
from multiprocessing.connection import Listener

import config as C

log = logging.getLogger("tesserawall")


# One wall per Windows user: a second user on the same PC gets their own.
CONTROL_PIPE = r"\\.\pipe\tesserawall-control-" + "".join(
    ch if ch.isalnum() else "_" for ch in (os.environ.get("USERNAME") or "user"))
# Gathering a multi-file right-click (see gather_batch), in seconds after this launch became the wall.
BATCH_MIN, BATCH_QUIET, BATCH_MAX = 0.5, 0.35, 3.0


def claim_control_pipe(paths: list) -> Listener | None:
    """Become the one running tesserawall, or hand `paths` to the one that is. Returns the control-pipe
    listener if this process is now the wall, or None if the paths were handed over.

    Explorer starts one process per selected file (right-click verb, Send To, drop on a shortcut),
    all at the same moment, so this must be race-free. It is because creating the listener is the
    claim: Python creates the pipe's first instance with FILE_FLAG_FIRST_PIPE_INSTANCE, so exactly
    one process succeeds and the rest get "access denied" and retry as clients. The claim is made
    before any window opens, so the winner can accept connections within milliseconds."""
    deadline = time.monotonic() + 15
    while True:
        try:  # an empty list just detects a running wall
            with Client(CONTROL_PIPE, family="AF_PIPE") as c:
                c.send_bytes(json.dumps(paths).encode("utf-8"))   # JSON, never pickle (see listen())
            return None
        except OSError:
            pass
        try:
            return Listener(CONTROL_PIPE, family="AF_PIPE")
        except OSError:  # another launch claimed it between our two attempts: send to it instead
            if time.monotonic() > deadline:
                raise
            time.sleep(0.05)


def gather_batch(incoming: queue.Queue, claimed_at: float) -> list:
    """Collect files that sibling launches hand over just after this one became the wall.

    Right-click on several files starts one process per file at the same moment; the others arrive
    here in a random order within a fraction of a second. Waits until nothing has arrived for
    BATCH_QUIET seconds (at least BATCH_MIN after the claim, at most BATCH_MAX)."""
    got, last = [], claimed_at
    while True:
        try:
            got.append(incoming.get(timeout=0.03))
            last = time.monotonic()
            continue
        except queue.Empty:
            pass
        now = time.monotonic()
        if now - claimed_at >= BATCH_MAX or (now - claimed_at >= BATCH_MIN and now - last >= BATCH_QUIET):
            return got


def listen(listener: Listener, incoming: queue.Queue):
    """Receive paths from later launches (Send To / shortcut drop while a wall is running)."""
    while True:
        try:
            with listener.accept() as conn:
                # Raw bytes holding a JSON list of paths. Never conn.recv(): that unpickles, and a
                # pickle from another local program could run code inside the wall.
                paths = json.loads(conn.recv_bytes(1 << 20).decode("utf-8"))
                if isinstance(paths, list):
                    for p in paths[:C.MAX_VIDEOS * 4]:
                        if isinstance(p, str):
                            incoming.put(p)
        except Exception as e:  # a broken client must not kill the listener
            log.error("listener: %s", e)
