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
| `tesserawall.pyw` | Entry point only: DPI setup, settings, single instance, start window, then the wall |
| `wall.py` | The wall: `Tile`, `Wall` (layout and animation, hover and focus, input, audio, overlays), the controls table |
| `ui.py` | Start window, Settings window, and window helpers (dark title bar, fades, rounded corners, hover, icon, drag-and-drop) |
| `instance.py` | One wall per user: control pipe, gathering multi-file launches, the listener thread |
| `files.py` | Arguments to video paths, Explorer's sort order, the saved session, saved walls |
| `winapi.py` | The shared `user32` handle, the monitor under the pointer, the Pictures folder |
| `resources.py` | Graphics memory and free RAM, and whether there is room for one more video |
| `templates.py` | Template format (built-ins and `templates.toml`), shape matching, choosing files, Best fit |
| `probe.py`, `probe.lua` | Video shapes before opening: one hidden mpv per batch runs the Lua script; cached in `shapes.json` |
| `ytdlp.py` | Web links: which yt-dlp (shipped or updated copy), mpv's options and Deno for a link, links in pasted/dropped text, updates |
| `layout.py` | Pure tiling maths: `arrange`, `layout`, `grid`, `panscan_for`, `Rect` |
| `ipc.py` | mpv JSON-IPC client over a Windows named pipe: pushed property values, non-blocking sends |
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
- Talking to the players (`ipc.py`): the wall never waits on a player. On connecting it calls
  `observe()` for the properties it needs (idle state, size, video params, position, duration, pause);
  mpv pushes every change and the reader thread keeps the latest in `value()`. Commands go through
  `post()`, a bounded queue drained by a writer thread, so a player that stops reading cannot block
  the interface (overflow is dropped and counted in `dropped`). Every incoming message gets a sequence
  number, used after a file swap: the new size only counts once `file-loaded` arrived after the swap
  and a size or `video-reconfig` arrived after that (mpv reports the new path before the new size).
  The blocking `get`/`set`/`command` remain for tests and quitting. `tests/freeze_test.py` suspends a
  player: before this design the wall had not reacted after 8 s; now it reacts in about 0.2 s.
- Layout search (`layout.py`): every possible split into rows is scored up to `EXACT_UP_TO` (12)
  videos; splits are scored from running totals, so 12 videos take about 11 ms. Above 12 a fast search
  is used (near-equal-width cuts, three starting points, cut moves while the score improves): 30 videos
  in about 28 ms, as good as the exact search on 86 % of 13-15 video walls (median 4.5 % worse
  otherwise, worst 24 %). Results are cached. `test_layout.py` keeps the quality and speed floor.
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
- Release checklist: run `update-mpv.ps1 -CheckOnly`; check for newer stable yt-dlp and Deno releases
  and, if worth it, re-pin them in `build.ps1` (version and SHA-256; Deno's `licenses\deno-LICENSE.md`
  from the same tag); run the full test suite; build the installer; run `tests\sandbox\run.ps1` on it;
  commit and tag `v<VERSION>`; publish the GitHub release with the installer **and** the mpv archive
  from `downloads\` attached (the build project deletes old releases, and the archive is the shipped
  build's source pointer for the GPL notice).
- Installer checked 2026-11-12 on the development PC (silent install, upgrade over an existing
  install, shortcuts, Send To targets, right-click on files and folders, uninstall leaves only the
  user's settings). Not yet tried on a clean PC or through the interactive wizard.
- Settings window: `settings_window()` is built from the `SETTINGS_UI` table, laid out after Windows 11's
  Settings app (Fluent 2): two columns (a `("column",)` entry starts the second), one rounded card per
  setting with its title and grey description on the left and its control on the right: a slider, a
  segmented choice or an on/off switch with "On"/"Off" beside it. Sizes are Fluent's (14 px text, 4 px
  grid, 32 px controls) scaled to the screen by `f()`. Every choice control has the same width
  (`control_w`, from its longest option), options split evenly, and sliders take that width too, so the
  controls line up down each column. Cards, controls and buttons are Canvas drawings: `rounded()` draws
  smooth corners from small anti-aliased images (`_aa_corners`), since tk's own ovals have hard edges.
  Entries marked `"clean": True` are greyed while the Clean mode switch is on, because clean mode
  overrides them. Each change is written with `config.set_user_values()`, which
  rewrites only that key's line in the user's file (the commented template line counts, so order and
  descriptions survive) and never touches the user's other lines; "Reset to defaults" comments
  the lines out again. In the wall, `Wall.apply_settings` then calls `reload_config(quiet=True)` and,
  for layout/crop, also switches the current mode, so the wall changes while the window is open.
  Its widgets are removed from the app-wide bindtag `all`, so Space on a switch does not also pause
  a video. A switch's whole card is clickable and its logged rectangle is the card; a segment's logged
  rectangle is its part of the choice control. Opened
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
- Controls list: `Wall.toggle_help` builds a borderless always-on-top `tk.Toplevel` from the
  `HELP_SECTIONS` table (laid out in two columns by `HELP_COLUMNS`) at the top of `wall.py`; key
  names become key chips. **When a key or gesture changes, update `HELP_SECTIONS` and the README.**
  `-topmost` must be set after the window is mapped, and `root.focus_force()` must not be called after
  it (it raises the wall over the list; use `focus_set`).
- Look and feel (checked by `tests/look_test.py`, `start_test.py`, `settings_test.py` and screenshots):
  - Gutters and focus outline: each tile's frame is placed `TILE_GAP // 2` px inside its layout rect
    (`Wall.animate`); the layout maths and logged rects are unchanged. `Wall.ring`, a blue frame below
    the tiles, is placed at the focused tile's full rect so it shows through the gutter.
  - On-screen text: every player gets `--osd-*` options (Segoe UI, fixed pixel size from the screen
    height, `background-box` style, outline colour equal to the box colour so there are no black blobs,
    top margin clear of the menu bar). The seek-bar label sets `\fnSegoe UI` itself and has a soft band
    of five stacked translucent boxes behind it.
  - Dark title bars: `dark_title_bar()` sets Windows' immersive dark mode and caption colour through
    `DwmSetWindowAttribute`. **It must run after the window is mapped**: before that the call reports
    success but changes nothing. Windows are shown at alpha 0, coloured, then faded in.
  - Fades: `fade()` steps `-alpha`; the menu bar, controls list, settings and start window fade in over
    120 ms and out over 80 ms. Ending at 1.0 makes a window opaque again, which screen capture needs.
  - Rounded corners on the borderless controls list: `round_corners()` (Windows 11 only).
  - Hover colours on flat buttons: `hover()`. The start window's drop zone is a Canvas with a dashed
    border that lights up on `<<DropEnter>>` and can be clicked to choose files.
- Log: `%TEMP%\tesserawall.log`, rewritten each run. Under `pythonw.exe` it is the only diagnostic.
- Playback keys (Phase 2): speed, frame step, A-B loop and track keys post mpv commands with the
  `osd-msg` prefix, so mpv itself shows the result on the video. `set` needs its value as text (`"1"`,
  not `1`): mpv silently ignored a number.
- Snapshots: `screenshot-to-file <path> video` (the frame without overlays), named
  `<file> at <m>m<ss>s <date-time>.png`, into `snapshot_dir` or `winapi.pictures_folder()`\Tessera Wall
  (the real Pictures folder via `SHGetKnownFolderPath`, so OneDrive redirection is respected).
- Crash restart: a player that exits with a non-zero code after it had shown video is restarted into
  the same frame at its last pushed `time-pos` (`Wall.restart`, `Wall.spawn`), at most 3 times a minute
  per tile, then the tile is removed as before. A player closed by the user exits with 0 and is removed.
- Memory guard (`resources.py`): before each `Wall.add`, the estimated graphics memory of the open
  videos plus one more 1080p video (60 MB + 110 MB per 1080p of picture, from measurements of 6 x
  1080p60) must fit in 80% of the largest card's dedicated memory (registry
  `HardwareInformation.qwMemorySize`, 64-bit, unlike WMI's `AdapterRAM`), and 220 MB more RAM must
  leave 768 MB free. Refused videos are collected in `Wall.refused` and reported once on screen by
  `report_refused`. These are estimates, not measurements of the running players; integrated GPUs
  report small dedicated memory and may be refused early. `TESSERAWALL_VRAM_MB` overrides the reading
  (tests). `MAX_VIDEOS` is 24.
- Sync (`G`, `Wall.toggle_sync`): seeks every video to the focused one and copies its pause and speed;
  while on, `Wall.cmd` sends speed and frame-step commands to all, `Space` sets one pause state on all,
  seeks go to all, and `keep_in_sync` (every second from `tick`) re-seeks any video more than 0.25 s
  away from the focused one. Meant for videos of the same length (several angles of one event).
- Tab focus: `Wall.move_focus`, bound with `self.root.bind`, not `bind_all`. Tk defines `<<NextWindow>>`
  as `<Key-Tab>`, and its own `<<NextWindow>>` binding on the `all` tag wins over `bind_all("<Tab>")`,
  which then silently never fires (0.4.0 shipped with that bug; Shift+Tab worked). The wall window's
  own tag is checked before `all`, and `"break"` skips Tk's traversal; the Settings window and dialogs
  are other toplevels, so they keep ordinary Tab between fields.
- Saved walls: `files.save_wall` writes `{"name", "videos": [{"path", "pos"}]}` to a `walls` folder
  next to the settings file (so a test's `--config` keeps them separate); `list_walls` returns the four
  newest for the start window. Ctrl+S and the save dialog: while `Wall.dialog_open` is set, every key
  binding ignores its key, or typing a name would also drive the wall.

- Templates (Phase 4):
  - Format: `[[template]]` tables with `name` and `rows` (a list of rows, each a list of slot shapes:
    `landscape` >= 1.2:1, `portrait` <= 1:1.2, `square` between, `any`, or a ratio `"16:9"` / `"2.39"`
    within 3 %), plus top-level `version` (refused if newer than `FORMAT_VERSION`) and `best_fit_max`.
    The built-ins are the TOML string `BUILT_IN_TOML`, parsed by the same `parse()` as the user's
    `templates.toml` (next to the settings file, created with a commented example); a user template with
    the same name (case and x/× ignored) replaces a built-in. Bad entries are skipped and logged.
  - Slot folders: a slot may be a table `{ shape, folder }` (shape defaults to `any`; other keys are
    refused). `Template.folders` runs parallel to `rows` (None when no slot has a folder, so built-ins
    are unchanged). A folder is absolute, or relative to `base_folder(paths)` (the first path, or its
    folder), with `%VARS%` and `~` expanded (`resolve_folder`). `templates.gather` lists the chosen paths
    and every slot folder of the given templates and reads all shapes in one `probe` call; `plan`
    takes the slot folders' shapes as `folder_shapes`. Slots group by (shape, folder), so the "same
    shape, same ratio" rule holds per source. Added as an optional key within format version 1: an
    older Tessera Wall ignores the folder and fills those slots from the chosen folder.
    Templates with a folder on every slot (`not needs_chosen_folder`, at most 4) are start-window
    buttons (`tpl0`…): `play_ready` gathers and plans in a thread, disables the buttons meanwhile, and
    returns the Plan (or shows its message if nothing was found).
  - Choosing (`templates.plan`): the folder's videos are grouped by ratio (3 %). Each distinct slot shape
    takes the single largest matching group, so a 2×2 landscape is four 16:9, never 16:9 mixed with
    4:3; exact ratios pick first, then classes, then `any`; a file is used once. Short of videos: open
    what was found, `rows=None` (the wall arranges them as usual) and a message shown as the notice.
  - Best fit (`templates.best_fit`): for every ratio group and count up to `best_fit_max`, the rows from
    `layout.choose_rows` and the share of the screen that shows picture after the crop limit
    (`templates.coverage`); the most videos within 3 % of the best coverage wins.
  - Shapes (`probe.py`): `probe.lua` runs in `mpv.exe --vo=null --ao=null --pause` over a playlist and,
    on each `file-loaded`, writes the first video track's `demux-w/h`, `demux-rotation` and `demux-par`
    (container values, no decoding), then `playlist-next`. A file-based script because the bundled mpv
    has no console `mpv.com`. Four mpv processes side by side; 15 small clips take ~0.2 s. Cache key: the
    normalised path, checked against size and mtime; files mpv skips are cached as `None`. A batch that
    takes longer than 10 s + 1 s per file is stopped, and its unread files are not cached.
  - Wall: `Wall.template_rows` replaces `arrange()` while the tile count equals the template's (adding or
    closing a video returns to normal layout), keeping the template's order. A complete template wall
    starts in `flat` mode instead of `grow` (main), so pointing at a video changes sound, dimming and the
    outline but not the arrangement; L still cycles. In `grid` mode a template wall uses
    `layout.grid_rows(template_rows)` (equal cells within its rows) rather than `grid()`, which would
    choose its own columns and break a mixed template. `Wall.shape_of` (normalised
    path -> group ratio) makes `swap_random` (N) choose only cached files of that ratio.
  - When a video ends (`when_finished`, `E`, Settings): `loop` keeps `--loop-file=inf`; `next` /
    `random` start players with `--loop-file=no` (changed live with `set loop-file`), so with
    `--keep-open=yes` mpv pauses on the last frame and pushes `eof-reached=true`. `tick` sees it (newer
    than the tile's `swap_seq`, file fully loaded) and calls `Wall.advance`: `pick_file` (shared with
    Page Down/Up and N; shape-filtered in a template wall), `load_into` without the file-name text on
    unfocused tiles, then unpause. No other file free: seek to 0 and play again.
  - Move on (`switch_seconds`, Ctrl+E, Settings) and random start (`random_start`): every tile gets a
    `switch_due` time (switch_seconds ±25 %, so the wall does not change all at once) when added and on
    every `load_into`; `tick` calls `Wall.timed_switch`, which skips the focused tile and sync mode
    (re-checked 5 s later) and otherwise loads `pick_file(next | random)`. Random start: `--start=N%`
    for new players, `loadfile <file> replace -1 start=N%` for swaps (the index argument exists since
    mpv 0.38; checked on the bundled 0.42), N in 5..80; restarts, saved walls and sessions keep their
    positions. Every swap passes its own `start` (`none` when random start is off) plus
    `ab-loop-a=no,ab-loop-b=no`: mpv applies a launch-time `--start` to every file it loads and keeps
    A-B loop points across `loadfile`, so without them the next video would open at the previous
    one's restored position and inherit its loop (checked on 0.42; upstream mpv#18275, mpv#17953). Ctrl+E rather than Shift+E: the wall binds the upper-case letter as the Caps Lock twin.
    Settings choices come back from tk as text; `ui.typed` turns them back into numbers.
  - Small controls: 1–9 `focus_number` (set_pin, again to release). `+` `add_one`: `pick_file(random)`
    from the focused/last tile's folder, shape carried over, queued like any added file (the count no
    longer matches the template, so `arrange()` lays it out). `-` `remove_one`: the last unfocused tile.
    Ctrl+G `toggle_sync(keep_offsets=True)`: records each tile's time-pos; `keep_in_sync` then wants
    `lead_pos + start[t] - start[lead]`, skipping tiles added or swapped since (popped in `load_into`)
    and targets outside the file. Ctrl+B `toggle_blackout`: `place_forget` every frame and the ring
    (mpv's child windows hide with their frame), pause and mute all, remember each pause state;
    `animate` does nothing and `update_audio` targets 0 while blanked; move-on waits. Undo re-places
    frames (placed=None) and restores pauses; Esc undoes it first.
  - Entry points: `--template NAME` (or `best`) plans without asking; `--choose-template` shows
    `ui.template_window` (Explorer's folder verb `tesserawall-template`); the start window's "Use a
    template…" opens it modally after a folder dialog. With a wall already open, the new process claims
    the control pipe with `[]` (detect only), chooses, then hands the chosen files over (rows not applied).

- Web links (`ytdlp.py`): mpv plays direct stream URLs itself; page links go through mpv's built-in
  ytdl_hook. For an item containing `://`, `Wall.spawn` adds `--ytdl=yes`, a format capped at
  `stream_max_height` (`bestvideo[height<=?H]+bestaudio`: mpv joins the two, no ffmpeg needed),
  `--script-opts-append=ytdl_hook-ytdl_path=…` (the yt-dlp chosen below) and
  `--ytdl-raw-options-append=js-runtimes=deno:…` for the shipped `yt-dlp\deno.exe`. YouTube needs a
  JavaScript runtime for its challenges (without one yt-dlp warns that this is deprecated and some formats
  go missing); Deno is the one yt-dlp recommends and enables by default. A link's player gets
  `DENO_DIR` (the state folder's `deno\`, not `%LOCALAPPDATA%\deno`) and `DENO_NO_UPDATE_CHECK=1`
  (`ytdlp.mpv_env`). The seek-bar label shows mpv's `media-title` for links.
- yt-dlp copies (`ytdlp.py`). The shipped `yt-dlp\yt-dlp.exe` (version in `version.txt`) and Deno are
  pinned with SHA-256 in `build.ps1`, like mpv, and never modified. Tessera Wall's own copy lives in the
  state folder (`%LOCALAPPDATA%\TesseraWall`; beside the settings file when `--config` names another one,
  as tests do) with `ytdlp.json` (`version`, `checked`, `recovered`). `candidates()` = the newer of the two
  (a tie keeps the shipped copy, so a release with a newer yt-dlp never gets downgraded), then the other;
  `ytdlp_path` alone if set; else PATH. `refresh()` (worker thread, one at a time) starts the own copy
  from the shipped one when missing or older, runs `yt-dlp --update-to stable` on it (yt-dlp's updater
  checks the SHA-256 from the release's SHA2-256SUMS, no signature, and swaps the exe via `.old`/`.new`
  even while another copy runs), then `--version`; a copy that does not run is deleted. It runs at most
  daily (`update_in_background`, not in `--exit-after` runs) and, via `recover()`, when a link fails to
  open (at most every 3 h). `Wall.open_failed`: a link that fails is retried once after that check (with
  the new copy if it changed) and then with any copy it has not been tried with (`link_tried`), before it
  is reported; the wall stays open while a retry is pending (`retrying`). Only the own copy is ever
  updated, never with `update_check = false` or `ytdlp_path`. The uninstaller removes the own copy,
  `deno\` and `ytdlp.json`.
- Link sources (`sources.py`): `source_of` recognises a YouTube channel (bare = its Videos tab), a tab
  (videos / shorts / streams), a playlist or a search; single videos (watch?v=, even with &list=, shorts/ID,
  youtu.be) are not sources. A search is kept as its results link (`search_url`; filters such as &sp= are
  dropped) and comes from a pasted results link, "ytsearch:words" (`links_from_text` keeps the whole
  line, `expand` converts it, so the command line works too) or *Search YouTube…* (start window,
  `Wall.search_from_dialog`); `load` lists it as `ytsearch100:words` (99 results in 4.7 s, measured). `Wall.add` hands a source to `open_source`, which lists it in a worker thread
  with yt-dlp's flat mode (`--flat-playlist --playlist-items 1:500`, no format lookups: NASA's Shorts tab
  ~2 s, a 5,500-video Videos tab ~50 s for all of it) and caches the list for a day in the state folder
  (`pools\`; an hour with `source_order = "newest"`). `take_pool_results` picks `source_count` entries
  not on the wall (`sources.pick`: random, or the first in the source's order with "newest"; entries in
  `Wall.seen`, shown before this run, only once the rest are used up) and opens them two
  at a time, 0.8 s apart (`pending_links`; YouTube may answer bursts with "confirm you're not a bot").
  Each tile keeps `pool`; `pick_file` then picks from the source (random / next / prev), so N, +,
  Page Down/Up, Move on and when-finished work as with folders. Sessions and saved walls store `pool`
  per video (`load_session` returns (path, pos, pool)); a reopened video re-lists its source quietly
  (n = 0), usually from the cache. An empty wall stays open while `waiting()` (retries, listings, pending
  videos) and shows "Getting videos from …". Shorts start as 9:16 tiles. Quality: `sources.height_for`
  gives each new link the smallest common height covering screen_h / sqrt(videos) (720p for 4-6 on
  1440p), capped by `stream_max_height`; for a Short the cap applies to the width (`ytdlp.mpv_args`).
- Recent and pinned sources: `take_pool_results` calls `sources.remember` for each source opened with
  n > 0 (not for ones re-listed for a reopened session), with the list's own title (`load` also prints
  `playlist:` + `TITLE_LINE` + title once per list; it is saved in the pool cache, `cached_title`), so a
  playlist reads by name (`menu_name`, `label`). `sources.json` next to the settings file holds
  {source, title, pinned, used}: pinned ones are kept, unpinned ones only the latest `RECENT_KEEP` (20).
  The start window lists every pinned one, then recent ones up to `RECENT_SHOWN` (6), each with a star
  (`set_pinned`) and a cross (`forget`); the top bar's *Recent* menu (`Wall.recent_menu`, shown once
  something is remembered) lists up to 12 and puts the pick into `incoming`, like pasting its link.
- Channel videos as a feed. `mode_of(t)`: source videos follow `source_end` (`source_when_finished`,
  default random; E while pointing at one), others `end_mode`; loop-file is set per player. Switching by
  page link costs 3.4-4.1 s (mpv's hook runs yt-dlp + Deno). So `prepare_next` picks each source video's
  successor as soon as it plays and resolves it in a worker (`ytdlp.resolve`: yt-dlp -j with the player's
  format; two at a time, `prepare_slots`); `advance`, `timed_switch` and N take it (`take_ready`, only if
  picked the same way and not on the wall meanwhile) and `load_into` opens the streams directly with
  `ytdlp.loadfile_options`: the audio as `audio-files-append`, the title as `force-media-title`, and, as
  the hook does, yt-dlp's User-Agent and `curl-max-request-size` (YouTube answers 403 to requests over
  ~10 MiB or with another User-Agent). Measured: picture in ~0.4 s. The tile keeps the page link as its
  path. A switch to a link sets brightness -100 (`fade = "hold"`) so the last frame never lingers;
  `update_fades` fades in over 0.35 s once the picture arrives (or after 10 s); relayout leaves
  brightness alone meanwhile.
- Links come in by command line, Send To,
  Ctrl+V (`ui.paste_async`) and browser drops (`DND_TEXT`, `ui.dropped_items`), all through
  `ytdlp.links_from_text`. Ctrl+V never calls Tk's `clipboard_get` on the Tk thread: an app that renders
  its clipboard data on request makes that call wait until it answers (~30 s if it is busy or hung), so
  a worker thread reads the clipboard through Win32 (`winapi.read_clipboard`: CF_HDROP for files copied
  in Explorer, else CF_UNICODETEXT) and lists any folders; the Tk thread polls for the result and gives
  up after 5 s with "not answering".
  Fresh Windows and certificates (found with tests\sandbox): Windows only downloads most root
  certificates when its own networking first needs one (Edge triggers that; Chrome and Firefox keep their
  own lists). Python's OpenSSL and mpv's curl both read the store but never trigger the download, so on a
  fresh PC the update check failed ("unable to get local issuer certificate") and YouTube's video servers
  failed in mpv ("TLS certificate verification failed"; the stream then ends at once at its full length).
  Fixes: `updates.tls_context` verifies through Windows itself (truststore, pinned in
  requirements-build.txt; build.ps1 insists on it), and `ytdlp.warm_tls` does one such handshake per host a
  link needs (`warm_hosts`: the link's own host plus googlevideo / ttvnw for YouTube / Twitch), started when
  a link's player starts and done again before a retry. `Wall.link_stalled` catches a link that jumps to
  the end of a long video within 30 s and sends it through `open_failed`; its first retry always goes ahead
  (warm-up, then update check, then the best yt-dlp). `tests\sandbox\tls_experiment.ps1` shows the effect.
  Verified: Mux and Apple HLS test streams; YouTube at 1080p60 with Deno (extraction ~0.6-1.3 s slower
  than without, warning gone); live Twitch (LofiGirl, on air around the clock: a dependable test channel);
  a real update from 2026.10.21 to 2026.10.26 in 3.8 s with the shipped copy untouched.
  Twitch and `stream_max_height`: this does hold. Twitch offers each quality as one muxed HLS stream;
  mpv's ytdl_hook (default `all_formats`) makes each one an edition of an EDL and selects yt-dlp's choice,
  so only that edition's tracks are listed. The first ~30 s can still decode at 1080p: Twitch stitches a
  pre-roll ad (`twitch-stitched-ad` in the playlist) into every variant, and the ad is 1080p whatever the
  variant. Checked 2026-11-22 on LofiGirl with mpv 0.42 and the 720p cap: mpv opened the 720p playlist,
  decoded 1920x1080 during the ad, then switched to 1280x720 when the stream began ("Reset playback due
  to audio timestamp reset" at the join). An earlier note here blamed mpv for this; it was the ad.

- Magnify (`Wall.magnify`, `drag_pan`, `unmagnify`; maths in `layout.py`: `drawn_size`, `pan_limit`,
  `clamp_pan`, `magnify_at`). Per tile `mag` (mpv `video-zoom`, log2, 0..3 in 0.25 steps) and `pan`
  (`video-pan-x/y`, fractions of the drawn size). The drawn size follows mpv: fit to the window, times
  `1 + panscan * (r - 1)`, times `2**zoom`. Ctrl+wheel keeps the picture point under the pointer fixed;
  pan is clamped so no picture edge comes into the cell (bars stay bars when the video is narrower).
  A press on a magnified tile starts a look-around drag instead of a swap, and a moved drag is not a
  click. Zoom and pan are player properties that survive `loadfile`, so `load_into` resets them; a
  restarted player gets them resent on connect. Not saved in sessions or walls.

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
- The memory guard against a card actually running out of memory; only its estimate and a pretend
  700 MB card were tested.
- Templates on rotated phone videos: `demux-rotation` is read but no rotated test file could be made
  (no ffmpeg here; mpv's encoder writes no rotation). Also folders of hundreds of real files.
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
python test_updates.py
python test_templates.py
python test_ytdlp.py
python tests/smoke.py           # every test below takes over the screen, mouse and often keyboard
python tests/freeze_test.py     # a suspended player must not stall the wall
python tests/phase2_test.py     # speed, frame step, A-B loop, tracks, snapshot, crash restart
python tests/phase3_test.py     # memory guard, Tab focus, sync, Ctrl+S save, saved-wall button
python tests/phase4_test.py     # templates: CLI, Best fit, chooser, N within shape, short folder, when finished
python tests/controls_test.py   # 1-9, + / -, Ctrl+B blackout (screen pixels), Ctrl+G offsets
powershell -ExecutionPolicy Bypass -File tests\sandbox\run.ps1   # clean PC in Windows Sandbox (~3 min, opens a
                                # Sandbox window; this PC is not changed): install, a wall with a file, a clip and
                                # YouTube, first yt-dlp update, update check, uninstall. Report: build\sandbox\out\
python tests/clean_test.py      # clean mode (I): outline, progress line, dimming, messages, menu bar off and on
python tests/magnify_test.py    # Ctrl+wheel, drag and Z on a four-colour clip, checked by screen pixels
python tests/sources_test.py    # NEEDS INTERNET + build.ps1 first: a channel's Shorts tab as a pool, N and +
                                # pick from it, the session keeps it, --resume uses the cached list
python tests/streams_test.py    # NEEDS INTERNET + build.ps1 first (packaged app): HLS, YouTube, live Twitch,
                                # Ctrl+V paste (uses the clipboard), recovery from a broken updated yt-dlp
python tests/add_test.py
python tests/drop_test.py       # needs tkinterdnd2
python tests/features_test.py   # writes a 26 MB clip to %TEMP% on first run; other tests reuse it
python tests/features2_test.py
python tests/modes_test.py
python tests/help_test.py
python tests/stepfile_test.py
python tests/carryover_test.py  # a swapped-in video starts at 0 s with no A-B loop after a restored position
python tests/cursor_test.py
python tests/reload_test.py
python tests/restart_test.py [cycles]
python tests/economy_test.py    # generates a 1080p60 H.264 clip with mpv's encoder on first run
python tests/start_test.py      # the start window: resume button, drop, Esc
python tests/update_test.py     # update notice against a local fake server
python tests/polish_test.py     # top-edge menu bar; "couldn't play" message
python tests/settings_test.py   # settings window: slider, choice, checkbox, reset, S / Esc
python tests/label_test.py      # visual: saves screenshots, asserts only that nothing crashed
python tests/stress_test.py     # GPU driver-timeout check
```

Notes for anyone running or writing them:

- They need a real desktop, a GPU and mpv, so they cannot run in ordinary CI. The four unit-test
  files (`test_layout.py`, `test_config.py`, `test_updates.py`, `test_templates.py`) run on GitHub
  Actions on every push
  (`.github/workflows/unit-tests.yml`, Windows, Python 3.11 and 3.13).
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
- Tests never touch the user's real settings or session. Any `--exit-after` run (every test uses it)
  starts from the defaults with a throwaway settings file unless `--config` names one, and does not
  save a session unless `--session` does. Before this, tests silently picked up the developer's own
  settings: a dimming of 0 made `features_test.py` fail for no fault in the app.
- Environment overrides exist for tests only: `TESSERAWALL_UNFOCUSED_SKIP`, `TESSERAWALL_HELP_OPACITY`,
  `TESSERAWALL_UNSAFE_RENDERING`, `TESSERAWALL_MPV_LOG_DIR`.
- One unexplained event: `features_test.py` once failed "r should restart every video" and passed on
  every run since. 65 looped cycles in `restart_test.py` rule out dropped commands. It now prints
  `wall_logged_restart`; False on a repeat would mean the key press never reached the wall.
