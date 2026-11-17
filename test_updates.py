"""Run with: python test_updates.py   (no pytest needed; no network; exits non-zero on failure)"""
import json
import os
import tempfile

import updates as U

REPO = "someone/tesserawall"
PAGE = "https://github.com/someone/tesserawall/releases/tag/v0.2.0"


def state_file():
    return os.path.join(tempfile.mkdtemp(prefix="tesserawall-upd-"), "update.json")


class Fake:
    def __init__(self, tag="v0.2.0", page=PAGE, error=None):
        self.calls, self.tag, self.page, self.error = 0, tag, page, error

    def __call__(self, repo):
        self.calls += 1
        if self.error:
            raise self.error
        return self.tag, self.page


def test_versions():
    assert U.parse("v0.1.10") == (0, 1, 10) and U.parse("1.2") == (1, 2)
    assert U.parse("v0.2.0-beta") is None and U.parse("nightly") is None and U.parse("") is None
    assert U.is_newer("v0.1.10", "0.1.9"), "numeric, not alphabetical"
    assert U.is_newer("v1.0", "0.9.9") and not U.is_newer("v0.1.0", "0.1.0") and not U.is_newer("0.1", "0.1.0")
    assert not U.is_newer("v0.0.9", "0.1.0") and not U.is_newer("v2.0-rc1", "0.1.0")
    assert U.current_version() == open("VERSION").read().strip()


def test_newer_release_found_and_cached():
    s, f = state_file(), Fake()
    assert U.check(REPO, s, "0.1.0", now=1000, fetch=f) == {"version": "0.2.0", "url": PAGE}
    assert f.calls == 1
    assert U.check(REPO, s, "0.1.0", now=1000 + 6 * 86400, fetch=f)["version"] == "0.2.0"
    assert f.calls == 1, "within a week: answer reused, GitHub not asked again"
    U.check(REPO, s, "0.1.0", now=1000 + 8 * 86400, fetch=f)
    assert f.calls == 2, "after a week: asked again"
    assert U.check(REPO, s, "0.2.0", now=1000 + 8 * 86400, fetch=f) is None, "already on it: no notice"


def test_other_repo_or_clock_change_asks_again():
    s, f = state_file(), Fake()
    U.check(REPO, s, "0.1.0", now=1000, fetch=f)
    U.check("other/repo", s, "0.1.0", now=1001, fetch=Fake(page="https://github.com/other/repo/releases/x"))
    U.check(REPO, s, "0.1.0", now=1002, fetch=f)
    assert f.calls == 2, "a cached answer for another repository does not count"
    U.check(REPO, s, "0.1.0", now=500, fetch=f)
    assert f.calls == 3, "a clock that went backwards does not suppress the check"


def test_failures_are_quiet_and_not_cached():
    s = state_file()
    assert U.check(REPO, s, "0.1.0", now=1000, fetch=Fake(error=OSError("offline"))) is None
    assert not os.path.exists(s), "a failed check must not be recorded"
    with open(s, "w") as fh:
        fh.write("{not json")
    assert U.check(REPO, s, "0.1.0", now=1000, fetch=Fake())["version"] == "0.2.0", "corrupt state is ignored"
    json.load(open(s))


def test_only_own_release_pages():
    s = state_file()
    assert U.check(REPO, s, "0.1.0", now=1000, fetch=Fake(page="https://evil.example/download")) is None
    assert U.check(REPO, state_file(), "0.1.0", now=1000,
                   fetch=Fake(page="https://github.com/someone-else/tesserawall/releases/tag/v0.2.0")) is None
    assert U.release_page_ok("https://GitHub.com/Someone/tesserawall/releases/tag/v1", REPO)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
