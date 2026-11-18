# Tessera Wall

Open several videos at once as a single full-screen wall, played by [mpv](https://mpv.io).
The videos are tiled to fill the monitor. Hover one and its audio fades in, it grows, and the others
dim and shrink.

> **Status: beta.** Tessera Wall has so far been developed and tested on one PC (Windows 11, one 2560x1440
> monitor, AMD GPU). Other GPUs, Windows 10, mixed-DPI and multi-monitor setups are untested, and the
> installer has not yet been tried on a second PC. Please
> [report what you find](https://github.com/jdotcollins/tessera-wall/issues).

## Install

1. Download **TesseraWall-setup-x.y.z.exe** from the
   [latest release](https://github.com/jdotcollins/tessera-wall/releases/latest).
2. Run it. Windows will probably show a blue **"Windows protected your PC"** box, because the installer
   is not yet signed by a known publisher. Click **More info**, then **Run anyway**.
3. Follow the installer. It installs for your user account only and does not ask for administrator
   rights.

That is everything: the video player it uses (mpv) is included, and nothing else needs installing.
Windows 10 or 11, 64-bit. To remove it, use *Settings > Apps > Installed apps* like any other program;
your settings are kept.

<details>
<summary>Run from source instead (for developers)</summary>

Needs Python 3.11 or newer and [mpv](https://mpv.io/installation/) installed separately (found on `PATH`
or in the usual folders; otherwise set `mpv_path` in the settings file). Optional:
`pip install tkinterdnd2` to drop files onto an open wall.

```
git clone https://github.com/jdotcollins/tessera-wall.git
cd tessera-wall
powershell -ExecutionPolicy Bypass -File install.ps1
```

`install.ps1` adds **Tessera Wall** and **Tessera Wall (random 6)** to Explorer's *Send to* menu and
puts a shortcut on the Desktop. `install.ps1 -Uninstall` removes them. Re-run it if you move the folder.

</details>

## Use

- Start Tessera Wall with nothing selected and a small window asks what to play: drop videos or folders on
  it, choose them with a button, use a template, reopen the last session where you left it, or open a
  wall you saved.
- Or select videos or a folder in Explorer, right-click, and choose **Play together in Tessera Wall**
  or **Send to > Tessera Wall** (on Windows 11 both are under *Show more options*).
- Or drop them on the Desktop shortcut. Doing either while a wall is open adds to it.
- **Send to > Tessera Wall (random 6)** opens six random videos from the selection.
- Command line: `pythonw tesserawall.pyw [--random N] [--resume] [--template NAME] FILE_OR_FOLDER...`

### Templates

A template opens videos of matching shapes from a folder, picked at random, in fixed rows: for example
four 16:9 videos in a 2×2 grid, or four portrait videos side by side. Choose **Use a template…** in the
start window, or right-click a folder and choose **Play as a template in Tessera Wall** (on Windows 11
under *Show more options*). The first time, Tessera Wall reads every video's shape, which takes a moment
for a large folder; after that it remembers.

- **Best fit** looks at what the folder holds and picks the shape and number of videos that fill your
  screen best (at most 9; `best_fit_max` in the templates file).
- Built in: 2×2, 3×3 and 4×4 landscape; 3 or 4 portrait in a row; 2 square side by side; one landscape
  above three portrait.
- If the folder does not have enough matching videos, the wall opens what it found and says so.
- A template wall keeps its arrangement: pointing at a video brings its sound and outline but does not
  enlarge it (press **L** for *Grow on hover* if you want that).
- **N** in a template wall swaps in another random video *of the same shape*, so the rows keep fitting.
- For an endless random wall, set *When a video ends* to *Random from folder* in Settings (or press
  **E** in the wall): each video that finishes is replaced by another of the same shape.

Your own templates go in `%APPDATA%\TesseraWall\templates.toml` (created with an example). Each row lists
the shape of each slot, `landscape`, `portrait`, `square`, `any` or an exact ratio such as `"16:9"` or
`"2.39"`:

```toml
[[template]]
name = "Big one, four small"
rows = [["landscape"], ["landscape", "landscape", "landscape", "landscape"]]
```

From the command line: `--template "2x2 landscape" FOLDER`, or `--template best FOLDER`.

Press **H** in the wall for the list of controls, or rest the pointer on the top edge of the screen
for a small menu bar with *Add videos*, *Add a folder*, *Controls*, *Save wall…*, *Settings* and *Close*.

A wall holds up to 24 videos. Before opening each one, Tessera Wall estimates whether your graphics card
and free memory have room for it; videos that would not fit are left out and the wall says so, instead
of the whole PC slowing to a crawl.

| Input | Action |
|---|---|
| Hover | Focus a video: audio fades to it, it enlarges, the others dim |
| Click | Pin the video so hover cannot change focus (click again to release) |
| Double-click | Zoom the video to most of the screen (again to undo) |
| Drag onto another video | Swap their positions |
| Click / drag the bottom edge | Seek within the focused video |
| `Tab` / `Shift+Tab` | Focus the next / previous video (moves the pin, if one is set) |
| `Space` / `Shift+Space` | Pause the focused video / pause or resume all |
| `Left` `Right` wheel | Seek the focused video; with `Shift`, seek all |
| `R` | Restart all from the beginning |
| `Up` `Down` | Volume of the focused video (remembered per video) |
| `Page Down` / `Page Up` | Next / previous file from the focused video's folder |
| `N` | Swap the focused video for a random one from its folder (same shape, in a template wall) |
| `E` | When a video ends: play it again, play the next file in its folder, or a random one |
| `G` | Sync: every video follows the focused one (pause, seek, speed, frame steps); again to stop |
| `[` `]` / `Backspace` | Slower / faster; back to normal speed |
| `,` `.` | Step one frame back / forward |
| `B` | Loop a section: set start, set end, clear |
| `T` / `A` | Next subtitle track / next audio track |
| `P` | Save a snapshot of the focused video (in Pictures\Tessera Wall; `snapshot_dir` in the settings file) |
| `X` `Delete` middle-click | Close the focused video |
| `L` | Layout: grow, flat, grid |
| `C` | Crop: limited, fill, fit |
| `S` | Settings |
| `Ctrl+S` | Save this wall under a name, to open it again from the start window |
| `F5` | Reload the settings file |
| `U` | Open the download page, when a newer version is available |
| `0` | Unpin, unfocus and mute everything |
| `Esc` | Close the controls list, else release pin/zoom, else quit |
| `Q` | Quit and remember this session |
| Pointer at the top edge | Menu bar: add videos, controls, save wall, settings, close |

## Settings

The settings most people want are in a **Settings** window: press **S** in the wall, choose *Settings*
on the top-edge menu bar, or click *Settings* in the start window. Changes apply immediately and are
saved as you make them.

Everything else lives in `%APPDATA%\TesseraWall\config.toml`, which the Settings window also writes to. The file is created the first time Tessera Wall runs,
with every setting listed, described and commented out. Remove the `#` from a line, change the value,
and press **F5** in the wall to apply it. A mistake in the file never stops the wall: a bad line is
ignored and reported on screen.

## Updates

Tessera Wall does not update itself. Once a week it asks GitHub for the number of the latest release, and
if it is newer than yours, the start window and the wall tell you and offer the download page. That
request contains nothing about you, your videos or your settings; GitHub sees it like any website visit.
To switch it off, set `update_check = false` in the settings file.

Each Tessera Wall release includes a tested copy of mpv. New mpv versions reach you through new Tessera Wall
releases, not separately.

## Troubleshooting

- **"The video player component (mpv) is missing"**: run the installer again to repair it. If you run
  from source, install mpv or set `mpv_path = "C:\\path\\to\\mpv.exe"` in the settings file.
- **Something misbehaves**: the log is `%TEMP%\tesserawall.log`, rewritten each run.
- **GPU driver timeout or reset popups**: keep `safe_rendering = true` (the default). If they still
  happen, set `anim_ms = 0`, then try `extra_mpv_args = ["--vo=gpu"]`, then `["--hwdec=no"]`.
- **HDR videos look flat**: that is the cost of `safe_rendering`. Setting it to `false` restores HDR
  output, at the risk above.
- **Playback stutters with many large videos**: try `unfocused_skip = "nonref"`.

## Building the installer

`build.ps1` produces the standalone app (no Python needed) with its pinned copy of mpv, and the Windows
installer. It needs PyInstaller and Inno Setup 6; see the comments at the top of the script and the
release checklist in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Development

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for how it works, the rules that keep it stable, and
how to run the tests.

## Licence

MIT. See [LICENSE](LICENSE). mpv is a separate program under its own licence and is not distributed
with Tessera Wall.
