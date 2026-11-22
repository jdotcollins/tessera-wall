"""Checks, at most once a week, whether a newer tesserawall has been published on GitHub.

What is sent: one HTTPS GET to api.github.com for the repository's latest release, with the
User-Agent "TesseraWall/<version>". Nothing about the user, their files or their settings. GitHub sees the
request like any website visit (IP address). Switched off with `update_check = false`, and inactive
while `update_repo` is empty (the default until tesserawall is published).

The check runs on a background thread and never touches tkinter: the UI reads `Checker.result`.
Any failure (offline, rate-limited, unexpected reply) is logged and simply means "no notice".
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.request

import config as C

CHECK_INTERVAL = 7 * 24 * 3600
log = logging.getLogger("tesserawall")


def current_version() -> str:
    """The running build's version: the VERSION file next to the program (build.ps1 copies it into
    packaged builds; in a source checkout it is the repository's own)."""
    try:
        with open(os.path.join(C.app_dir(), "VERSION"), encoding="utf-8") as f:
            return f.read().strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def parse(version: str) -> tuple[int, ...] | None:
    """'v0.1.10' -> (0, 1, 10). None for anything that is not plain dotted numbers (pre-releases,
    nightly tags), so those are never offered."""
    m = re.fullmatch(r"v?(\d+(?:\.\d+){0,3})", (version or "").strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def is_newer(latest: str, current: str) -> bool:
    a, b = parse(latest), parse(current)
    if a is None or b is None:
        return False
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def release_page_ok(url: str, repo: str) -> bool:
    """Only ever offer to open the repository's own release pages on github.com."""
    return isinstance(url, str) and url.lower().startswith(f"https://github.com/{repo.lower()}/releases/")


def tls_context():
    """HTTPS checked by Windows itself (truststore), not by Python's OpenSSL reading the certificate store.
    A fresh Windows only downloads most root certificates the first time its own networking needs one, so
    OpenSSL finds GitHub's missing there and the check failed ("unable to get local issuer certificate",
    seen in Windows Sandbox). Windows' own verification fetches it, and honours certificates a company
    installs. None (Python's default) when truststore is not installed, e.g. running from source."""
    try:
        import ssl
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        return None


def fetch_latest(repo: str, timeout: float = 5.0) -> tuple[str, str]:
    """(tag, release page URL) of the latest non-pre-release. Raises on any problem."""
    url = os.environ.get("TESSERAWALL_UPDATE_API") or f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(url, headers={"User-Agent": f"TesseraWall/{current_version()}",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout, context=tls_context()) as r:
        data = json.loads(r.read(1 << 20).decode("utf-8"))
    tag, page = data.get("tag_name"), data.get("html_url")
    if not isinstance(tag, str) or not isinstance(page, str):
        raise ValueError("reply has no tag_name / html_url")
    return tag, page


def check(repo: str, state_path: str, current: str, now: float | None = None, fetch=fetch_latest) -> dict | None:
    """{'version': '0.2.0', 'url': ...} when a newer release exists, else None.

    Asks GitHub only if the last successful answer for this repository is older than CHECK_INTERVAL;
    otherwise reuses it from state_path. A failed request is not recorded, so the next launch tries again.
    """
    now = time.time() if now is None else now
    try:
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
        if not isinstance(state, dict):
            state = {}
    except (OSError, ValueError):
        state = {}
    fresh = state.get("repo") == repo and isinstance(state.get("checked"), (int, float)) \
        and 0 <= now - state["checked"] < CHECK_INTERVAL
    if fresh:
        tag, page = state.get("latest"), state.get("url")
        log.info("update check: using answer from %.1f days ago (%s)", (now - state["checked"]) / 86400, tag)
    else:
        try:
            tag, page = fetch(repo)
        except Exception as e:
            log.info("update check failed: %s: %s", type(e).__name__, e)
            return None
        log.info("update check: latest release is %s", tag)
        try:
            os.makedirs(os.path.dirname(state_path), exist_ok=True)
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump({"repo": repo, "checked": now, "latest": tag, "url": page}, f)
        except OSError as e:
            log.info("update check: could not save state: %s", e)
    if isinstance(tag, str) and is_newer(tag, current) and release_page_ok(page, repo):
        return {"version": ".".join(map(str, parse(tag))), "url": page}
    return None


class Checker:
    """Runs check() once on a daemon thread. `result` stays None until (and unless) an update is found."""

    def __init__(self, state_path: str):
        self.result: dict | None = None
        self.repo = (C.UPDATE_REPO or "").strip()
        if not C.UPDATE_CHECK or not re.fullmatch(r"[\w.-]+/[\w.-]+", self.repo):
            log.info("update check: off (update_check=%s, update_repo=%r)", C.UPDATE_CHECK, self.repo)
            return
        threading.Thread(target=self._run, args=(state_path,), daemon=True).start()

    def _run(self, state_path: str):
        found = check(self.repo, state_path, current_version())
        if found:
            log.info("update available: %s (running %s)", found["version"], current_version())
        self.result = found


def open_release_page(url: str) -> None:
    if os.environ.get("TESSERAWALL_NO_BROWSER") == "1":   # tests
        log.info("would open download page: %s", url)
        return
    import webbrowser
    log.info("opening download page: %s", url)
    webbrowser.open(url)
