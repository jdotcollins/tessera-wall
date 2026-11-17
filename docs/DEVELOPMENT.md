# Tessera Wall — developer notes

How Tessera Wall works, the rules that keep it stable, what has and has not been verified, and how to run
the tests. User-facing documentation is in the [README](../README.md).

## Names

The product is **Tessera Wall**. Three spellings are used on purpose:

- `Tessera Wall`: what people read (window titles, shortcuts, installer, right-click entry).
- `TesseraWall`: the program (`TesseraWall.exe`), the settings folder (`%APPDATA%\TesseraWall`), the
  installer file and the update check's User-Agent.
- `tesserawall`: internal identifiers (`tesserawall.pyw`, the log file, pipe names, icon files, the
  registry verb key, and the `TESSERAWALL_` environment overrides used by tests).

Before its first public release it was called mpvwall. `config.migrate_old_settings()` copies a
user's settings from `%APPDATA%\mpvwall` once, if the new folder does not exist yet. The repository
folder on the development PC still has the old name; nothing depends on it.

## Files

| File | Purpose |
|---|---|
| `tesserawall.pyw` | The app: window, tiles, hover, animation, input, single instance |
| `layout.py` | Pure tiling maths: `arrange`, `layout`, `grid`, `panscan_for`, `Rect` |
| `ipc.py` | mpv JSON-IPC client over a Windows named pipe |
| `config.py` | Setting defaults (`SETTINGS` table), the user's TOML file, locating mpv |
| `install.ps1` | Creates Send To + Desktop shortcuts (`-Uninstall` removes them) |
| `test_layout.py`, `test_config.py` | Unit tests; no display needed |
| `tests/*.py` | End-to-end tests that drive a real wall with the real mouse and keyboard |

## How it works

**Players and input**
- One `mpv.exe` per video, embedded with `--wid` into a tkinter `Frame` inside one full-screen window.
  Each mpv has its own named pipe `\\.\pipe\tesserawall-<pid>-<n>`; all control goes through it.
- mpv runs with `--input-cursor=no --input-vo-keyboard=no`, so its window is transparent to input and
  tkinter receives every click, wheel and key. Keys are forwarded to the focused player over IPC.
- `Wall.tick()` runs every `TICK_MS` (16 ms while animating or fading): reap dead players, connect
  pipes, read video sizes, accept paths from later launches, hover check, audio fade, animate.
- Non-blocking mpv calls: `ipc.post()` / `ipc.set_async()` send without waiting for the reply. All
  state changes from the UI thread use them, because mpv acknowledges some properties (brightness,
  `vd-lavc-skipframe`) only on the next video frame; waiting for each made one focus change take up to
  1.45 s with six videos. Keep `ipc.get()` / `ipc.command()` for calls whose answer is needed.

**Hover and layout**
- Hover is polled with `GetCursorPos` + `WindowFromPoint` and hit-tested against **target** rects.
  Focus only changes if the mouse physically moved since the last change and rested on the new tile
  for `HOVER_DELAY_MS`. That rule is what prevents flicker when the layout moves under a still cursor.
- Layout: videos are grouped into rows, or columns if that fits better, and may be regrouped
  portrait-first when that is clearly better (`arrange`). Leftover mismatch is cropped up to
  `Wall.max_crop` with mpv's `panscan`; anything beyond that is black bars. On focus the grouping is
  kept and the focused tile's group and the tile itself grow; the focused rect always contains its
  balanced rect.
- Layout and crop modes: `Wall.layout_mode` ("grow" / "flat" / "grid") and `Wall.crop_mode`
  ("limited" / "fill" / "fit") start from the `layout_mode` / `crop_mode` settings and are cycled by
  `Wall.cycle_mode`; they are not saved in the session. "flat" is the normal layout with no growth on
  focus; "grid" uses `layout.grid()`. Double-click zoom always uses the normal reflow.
  Known limit: with 3 or 7 same-shape videos the grid's last tile spans the full width.
- Pin/zoom: a click pins the tile (`Wall.pin`; hover cannot change focus while set); a double-click
  zooms it (`Wall.zoom`), the same reflow with a much smaller floor for the others (`ZOOM_MIN_SHRINK`).
- Drag to reorder: the mouse protocol is documented above `Wall.on_press`. A press that travels more
  than `DRAG_THRESHOLD` px becomes a drag; releasing over another tile swaps the two. After a manual
  swap `Wall.manual_order` stops `arrange()` regrouping tiles by shape. Hover is ignored while the
  button is down.
- Monitor: the wall opens on the monitor under the pointer (`monitor_under_cursor`). On the primary
  monitor it uses tk's `-fullscreen`; on any other it covers the monitor rectangle with a borderless
  window, and it falls back to that whenever the resulting window does not match the monitor.
  **Only the primary path has ever run**: there was one monitor.

**Focus feedback and audio**
- Seek bar: `Wall.update_bar` draws a progress line, file name, time and `[pinned]` marker on the
  focused tile with mpv's `osd-overlay` command 4 times a second. Clicks/drags within `SEEKBAR_ZONE` px
  of the tile's bottom edge seek instead of pinning. The name is shortened to fit and dropped on tiles
  too narrow for 8 characters; `{`, `}` and `\` are replaced because mpv's OSD reads them as formatting
  codes. The width estimate assumes Latin letters; wide scripts (CJK) may overrun.
- Unfocused tiles are darkened with mpv's `brightness` property (`DIM_UNFOCUSED`).
- The pointer hides after `CURSOR_HIDE_SECONDS` idle by setting the tk cursor to `none`.
- Audio crossfade: every tile has a `gain` (0 = muted, 1 = full). `Wall.update_audio` moves each gain
  towards 1 for the focused tile and 0 for the rest over `CROSSFADE_MS`, by setting mpv's `volume` to
  `tile.volume * gain`, and mutes at 0. `tile.volume` is the video's own level and survives focus changes.
- Economy mode (`UNFOCUSED_SKIP`, off by default): while a video is focused, the others get mpv
  `vd-lavc-skipframe`. Per-frame quality is unchanged; the frame rate drops by an amount that depends
  on the file's B-frame pattern (a 60 fps clip with 3 B-frames became 15 fps with `bidir`, 30 with
  `nonref`). Measured on six 1080p60 H.264 videos with hardware decoding, the whole-wall CPU saving was
  too noisy to claim (four runs 14-64% lower, one run 55% higher). Clean single-player numbers: 5.2% ->
  2.8% of a core with hardware decoding, 21.4% -> 5.3% with software decoding. GPU load was not measured.

**Files, sessions, settings**
- A second launch sends its paths to the running wall through the per-user pipe
  `\\.\pipe\tesserawall-control-<username>` and exits. The payload is raw bytes holding a JSON list.
  **Never use `conn.send()` / `conn.recv()` there**: those pickle, and unpickling data from another
  local program can run code.
- Per-tile playlist: `Wall.step_file(+1/-1)` loads the next/previous video in the focused tile's folder
  (name order, wrapping, skipping files another tile shows) through `Wall.load_into`, which `n`
  (random swap) also uses. The tile is re-measured so the grid re-tiles if the shape changes.
- Session: on quit the open paths and positions are written to `%APPDATA%\TesseraWall\session.json`.
  Starting with no files or with `--resume` reopens them. Runs with `--exit-after` do not save unless
  `--session FILE` is given.
- Settings: defaults are the `SETTINGS` table in `config.py`, exposed as module attributes. The user's
  overrides are TOML in `%APPDATA%\TesseraWall\config.toml` (or `--config FILE`), created on first run from
  `config.template()` with everything commented out. `config.load()` applies the file on top of the
  defaults: an unparseable file raises and changes nothing; a bad or unknown key is skipped and
  reported. `Wall.reload_config` (F5) calls it and re-tiles. This works because the app reads `C.NAME`
  at the moment of use; **do not cache setting values in locals or closures**. Settings used when a
  player is launched (`MPV_PATH`, `SAFE_RENDERING`, `EXTRA_MPV_ARGS`, `USE_USER_MPV_CONFIG`,
  `MPV_LOG_DIR`) only affect videos opened afterwards; `LOG_PATH` is read once at start.
- mpv is located by `config.find_mpv()`: the `mpv_path` setting if set (no fallback if it is wrong),
  else the copy shipped with a packaged build (`mpv\mpv.exe` next to the program, see `app_dir()`),
  else the App Paths registry entry, Scoop's real exe, `PATH`, then common install folders.
- Start window: `start_window()` is shown when Tessera Wall starts with no files and no `--resume`. It is
  its own Tk root, destroyed before the wall's root is created. It offers a drop area (if `tkinterdnd2`
  is present), "Choose videos", "Choose a folder" and, when a session exists, "Reopen last session".
  It logs each button's screen rectangle so `tests/start_test.py` can click the real buttons. The two
  file-picker buttons open native dialogs and are **not covered by any test**.
- Packaging (`build.ps1`, `installer.iss`, `VERSION`): PyInstaller one-folder build, mpv copied into
  `mpv\`, then an Inno Setup per-user installer that adds Start Menu, optional Desktop, Send To and
  optional right-click entries. The right-click entry is registered both for the `video` perceived
  type and per extension: Windows ignores the perceived-type entry for extensions that have their own
  `SystemFileAssociations\.ext` key with a context-menu handler (`.mp4`, `.mkv`, `.mov` do).
- Single instance: Explorer starts one process per selected file (right-click, Send To, drop on a
  shortcut), all at once. `claim_control_pipe()` runs before any window: creating the control-pipe
  `Listener` is an atomic claim (first pipe instance), the losers connect and hand over their files.
  Files handed over while the start window is open close it and open the wall. The siblings arrive in
  a random order, so a launch with exactly one file (the shape of each process in such a batch) waits
  briefly after the claim (`gather_batch`, `BATCH_*`: done when nothing arrived for 0.35 s, at least
  0.5 s, at most 3 s) and sorts the batch in Explorer's name order (`StrCmpLogicalW`). The wait
  overlaps with the window appearing; a plain single-file launch pays roughly the 0.5 s minimum.
  `tests/add_test.py` covers simultaneous launches and their order.
- Bundled mpv: pinned in `build.ps1` (`$Mpv*`: release tag, file, SHA-256, mpv commit) to a
  shinchiro/mpv-winbuild-cmake baseline x86_64 build. Downloaded once into `downloads\` (ignored by
  git), hash-checked on every build, unpacked with Windows' `tar`. `THIRD-PARTY-NOTICES.txt` is
  generated from the pin. That project prunes old releases, so keep the archive of any build you
  ship. To update: change the four values, build, and run the tests against the packaged exe.
- Updating the bundled mpv: `update-mpv.ps1` (`-CheckOnly` to just look) asks GitHub for the build
  project's latest release, picks the plain x86_64 archive, downloads it, checks it against GitHub's
  published SHA-256 digest, rewrites the four `$Mpv*` lines in `build.ps1`, bumps the patch number in
  `VERSION`, builds the app and runs the unit tests. It deliberately stops before the end-to-end tests,
  the installer, committing and publishing. Its rewrite was tested on a copy pinned to a fake older
  build; a real move to a newer mpv has not happened yet (the pin is the latest build as of 2026-11-12).
- Update notice: `updates.py`. At most once a week (`CHECK_INTERVAL`) a background thread asks
  `api.github.com/repos/<update_repo>/releases/latest`; the answer is cached in `update.json` next to the
  settings file, failures are not cached. A release is offered only if its tag is plain dotted numbers,
  newer than `VERSION`, and its page is on `github.com/<update_repo>/releases/`. The start window shows a
  link; the wall shows one on-screen message after the launch hint, and `U` opens the page. It checks
  `jdotcollins/tessera-wall` by default; while that repository is private or has no releases the
  request fails quietly and nothing is shown. `tests/update_test.py` runs it against a local fake server
  (`TESSERAWALL_UPDATE_API`) with browser opening replaced by a log line (`TESSERAWALL_NO_BROWSER=1`).
- Release checklist: run `update-mpv.ps1 -CheckOnly`; run the full test suite; build the installer;
  commit and tag `v<VERSION>`; publish the GitHub release with the installer **and** the mpv archive
  from `downloads\` attached (the build project deletes old releases, and the archive is the shipped
  build's source pointer for the GPL notice).
- Installer checked 2026-11-12 on the development PC (silent install, upgrade over an existing
  install, shortcuts, Send To targets, right-click on files and folders, uninstall leaves only the
  user's settings). Not yet tried on a clean PC or through the interactive wizard.
- Settings window: `settings_window()` is built from the `SETTINGS_UI` table (sliders, choices,
  checkboxes for about ten settings). Each change is written with `config.set_user_values()`, which
  rewrites only that key's line in the user's file (the commented template line counts, so order and
  descriptions survive) and never touches the user's other lines; "Reset these to defaults" comments
  the lines out again. In the wall, `Wall.apply_settings` then calls `reload_config(quiet=True)` and,
  for layout/crop, also switches the current mode, so the wall changes while the window is open.
  Its widgets are removed from the app-wide bindtag `all`, so Space on a checkbox does not also pause
  a video. The sliders are a small Canvas control because `tk.Scale` cannot colour its handle. Opened
  with `S`, the menu bar, or the start window's link; `tests/settings_test.py` covers the wall route
  with real clicks. **The start-window route and "Open the settings file" are not tested.**
- Menu bar: `Wall.update_topbar` shows a slim always-on-top `Toplevel` when the pointer rests on the top
  3 px of the wall for 0.25 s, and hides it when the pointer leaves. Buttons: Add videos, Add a folder
  (native dialogs, **not covered by any test**), Controls, Close. `bar.update()` is called before the
  button rectangles are logged: until the window is laid out they read 0,0 1x1. `tests/polish_test.py`.
- Files that will not play: mpv does **not** exit on an unplayable file (because of `--force-window`);
  it goes idle with an empty default-size window, which used to become a black tile with a made-up
  shape. `tick` now treats `idle-active` seen twice 0.6 s apart, before any video size is known, as a
  failure: the tile is removed and `Wall.report_failed` shows "Couldn't play ..." on another video,
  or a message box and exit if nothing at all could be played (`TESSERAWALL_NO_DIALOGS=1` skips the box in
  tests). The same path covers `n` / Page Down landing on a broken file.
- Icon: `tools/make_icon.py` draws it with the standard library only and writes `tesserawall.ico` (exe,
  shortcuts, installer) and `tesserawall.png`; both are committed. `set_icon()` loads them from `app_dir()`.
- Controls list: `Wall.toggle_help` builds a borderless always-on-top `tk.Toplevel` from the `HELP`
  table at the top of `tesserawall.pyw`. **When a key or gesture changes, update `HELP` and the README.**
  `-topmost` must be set after the window is mapped, and `root.focus_force()` must not be called after
  it (it raises the wall over the list; use `focus_set`).
- Log: `%TEMP%\tesserawall.log`, rewritten each run. Under `pythonw.exe` it is the only diagnostic.

## GPU driver timeouts

On the development PC (AMD GPU, Windows HDR on) the AMD "driver timeout" popup appeared several times
while small walls were starting and re-tiling. Nothing was written to the Windows System log.

At the time each player created a 10-bit PQ (R10G10B10A2, BT.2020) **flip-model** swapchain, and the
wall resizes all of them together on every reflow.

Mitigation, on by default (`SAFE_RENDERING`): every player gets
`--d3d11-flip=no --target-colorspace-hint=no --d3d11-output-csp=srgb --d3d11-output-format=rgba8`
(8-bit SDR, bitblt presentation). With it, `tests/stress_test.py` (3 wall start/stop cycles, 76
reflows) and many full suite runs produced no popup. **This is not proof**: the original settings
were never put through the same stress test, and it is not known whether HDR, flip-model presentation,
or the combination was responsible. That is why the safe path is the default for everyone rather than
only when HDR is detected.

- `tests/stress_test.py` detects the popup by watching for new `AMDBugReportForm` / `RadeonSoftware`
  processes. `TESSERAWALL_UNSAFE_RENDERING=1` runs it with the original swapchains for an A/B comparison.
- The `mpv_log_dir` setting (or env `TESSERAWALL_MPV_LOG_DIR`) writes one verbose mpv log per player.
- Cost of the mitigation: HDR videos are tone-mapped to SDR inside the wall.

## Rules

- Do not replace polling hover with tkinter `<Enter>`/`<Motion>` events, and do not hit-test animated rects.
- Do not open the mpv pipe with `open()`; it must stay overlapped (see `ipc.py` docstring).
- Do not touch tkinter from any thread other than the main one. The listener thread only fills a queue.
- Do not add per-tick IPC reads for every tile; each call is a blocking round trip.
- `SetProcessDpiAwareness(2)` must stay above the tkinter import.
- Do not kill mpv by process name (the user may have their own mpv open); only kill `Tile.proc`.
- Bind letter keys through the `b()` helper in `bind_keys`: it also binds the upper-case twin. Without
  it every letter key stops working with Caps Lock on.
- Known limits, not bugs: with a single row the focused video grows in width only; six or seven 16:9
  videos cannot tile a 16:9 screen without bars beyond the crop limit; mpv's on-screen controller is off.

## What has been verified, and where

Everything below was observed on one PC: Windows 11, mpv 0.41 and later 0.42 (the same build as the
bundled pin), Python 3.13, one 2560x1440 monitor at 100% scaling, AMD GPU with HDR enabled.

- Embedding, resizing and animation with 2-10 players; clean shutdown with no mpv left behind.
- Every feature in the README's controls table except those listed under "Not verified", through the
  end-to-end tests, with real mouse and keyboard input.
- Audio following the hover and the crossfade were confirmed by ear by the original user.

## Not verified

- Any other machine: other GPUs, Windows 10, other DPI scaling, HDR off, more than one monitor.
- Ordinary compressed files in everyday use beyond one manual session; the tests use generated clips.
- Mouse wheel, `Up` (volume), `x` / `Delete` / middle-click, `0`, dragging along the seek bar.
- The installer on a clean PC, or through its interactive wizard (only silent installs were tried).
- Whether the IPC connect handshake (`MpvIpc._handshake`) actually prevents the lost first message:
  the loss was seen about 1 in 50 connections while players were being resized, but a headless loop
  of 600 connections never reproduced it, with or without the handshake. It costs about 0.1 ms per
  connection and the full suite passes with it.
- `install.ps1` (the source-checkout shortcuts) is superseded by the installer for normal users.
- Ten or more 4K videos. If playback stutters, try `extra_mpv_args = ["--profile=fast"]` first.
- File names in wide scripts in the seek-bar label; the narrow-tile case of that label.

## Tests

```
python test_layout.py
python test_config.py
python tests/smoke.py           # every test below takes over the screen, mouse and often keyboard
python tests/add_test.py
python tests/drop_test.py       # needs tkinterdnd2
python tests/features_test.py   # writes a 26 MB clip to %TEMP% on first run; other tests reuse it
python tests/features2_test.py
python tests/modes_test.py
python tests/help_test.py
python tests/stepfile_test.py
python tests/cursor_test.py
python tests/reload_test.py
python tests/restart_test.py [cycles]
python tests/economy_test.py    # generates a 1080p60 H.264 clip with mpv's encoder on first run
python tests/start_test.py      # the start window: resume button, drop, Esc
python tests/update_test.py     # update notice against a local fake server
python tests/polish_test.py     # top-edge menu bar; "couldn't play" message
python tests/settings_test.py   # settings window: slider, choice, checkbox, reset, S / Esc
python test_updates.py          # update-check logic; no display, no network
python tests/label_test.py      # visual: saves screenshots, asserts only that nothing crashed
python tests/stress_test.py     # GPU driver-timeout check
```

Notes for anyone running or writing them:

- They need a real desktop, a GPU and mpv, so they cannot run in ordinary CI. Only the two unit-test
  files can.
- They move the real pointer. A person using the mouse during a run, or a driver popup appearing over
  the wall, makes them fail. Re-run a failing test alone and read `%TEMP%\tesserawall.log` before
  suspecting the app.
- Hovering a tile re-tiles the wall. A test that aims at a tile must wait for the reflow and re-read
  the rectangles from the log first.
- Keys are sent only after checking the wall is the foreground window, so nothing is typed elsewhere.
- tk hides the pointer with a blank cursor whose handle is not NULL, so `GetCursorInfo` never reports
  "hidden"; compare the handle with `IDC_ARROW`.
- Screen captures cannot see translucent windows; `help_test.py` sets `TESSERAWALL_HELP_OPACITY=1`.
- `mpv --o=file` (encoding) hangs on exit with the build used here although the file is written;
  `economy_test.py` waits for the file to stop growing and then stops its own encoder process.
- Tests never touch the user's real settings or session: they pass `--config` / `--session`, and
  `--exit-after` runs do not save a session.
- Environment overrides exist for tests only: `TESSERAWALL_UNFOCUSED_SKIP`, `TESSERAWALL_HELP_OPACITY`,
  `TESSERAWALL_UNSAFE_RENDERING`, `TESSERAWALL_MPV_LOG_DIR`.
- One unexplained event: `features_test.py` once failed "r should restart every video" and passed on
  every run since. 65 looped cycles in `restart_test.py` rule out dropped commands. It now prints
  `wall_logged_restart`; False on a repeat would mean the key press never reached the wall.
