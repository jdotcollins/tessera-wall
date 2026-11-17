# Tessera Wall

Open several videos at once as a single full-screen wall, played by [mpv](https://mpv.io).
The videos are tiled to fill the monitor. Hover one and its audio fades in, it grows, and the others
dim and shrink.

> **Status: beta.** Tessera Wall has so far been developed and tested on one PC (Windows 11, one 2560x1440
> monitor, AMD GPU). Other GPUs, Windows 10, mixed-DPI and multi-monitor setups are untested. Please
> report what you find.

## Requirements

- Windows 10 or 11
- [mpv](https://mpv.io/installation/), installed separately. Tessera Wall finds it on `PATH` and in the
  usual install folders; otherwise set `mpv_path` in the settings file.
- Python 3.11 or newer (until a packaged build exists)
- Optional: `pip install tkinterdnd2` to drop files onto a wall that is already open

## Install from source

```
git clone https://github.com/jdotcollins/tessera-wall.git
cd tessera-wall
powershell -ExecutionPolicy Bypass -File install.ps1
```

`install.ps1` adds **Tessera Wall** and **Tessera Wall (random 6)** to Explorer's *Send to* menu and puts a
shortcut on the Desktop. `install.ps1 -Uninstall` removes them. Nothing is written to the registry.
Re-run it if you move the folder.

## Use

- Start Tessera Wall with nothing selected and a small window asks what to play: drop videos or folders on
  it, choose them with a button, or reopen the last session where you left it.
- Or select videos or a folder in Explorer, right-click, **Send to > Tessera Wall**
  (on Windows 11: *Show more options* first).
- Or drop them on the Desktop shortcut. Doing either while a wall is open adds to it.
- **Send to > Tessera Wall (random 6)** opens six random videos from the selection.
- Command line: `pythonw tesserawall.pyw [--random N] [--resume] FILE_OR_FOLDER...`

Press **H** in the wall for the list of controls, or rest the pointer on the top edge of the screen
for a small menu bar with *Add videos*, *Add a folder*, *Controls* and *Close*.

| Input | Action |
|---|---|
| Hover | Focus a video: audio fades to it, it enlarges, the others dim |
| Click | Pin the video so hover cannot change focus (click again to release) |
| Double-click | Zoom the video to most of the screen (again to undo) |
| Drag onto another video | Swap their positions |
| Click / drag the bottom edge | Seek within the focused video |
| `Space` / `Shift+Space` | Pause the focused video / pause or resume all |
| `Left` `Right` wheel | Seek the focused video; with `Shift`, seek all |
| `R` | Restart all from the beginning |
| `Up` `Down` | Volume of the focused video (remembered per video) |
| `Page Down` / `Page Up` | Next / previous file from the focused video's folder |
| `N` | Swap the focused video for a random one from its folder |
| `X` `Delete` middle-click | Close the focused video |
| `L` | Layout: grow, flat, grid |
| `C` | Crop: limited, fill, fit |
| `S` | Settings |
| `F5` | Reload the settings file |
| `U` | Open the download page, when a newer version is available |
| `0` | Unpin, unfocus and mute everything |
| `Esc` | Close the controls list, else release pin/zoom, else quit |
| `Q` | Quit and remember this session |
| Pointer at the top edge | Menu bar: add videos, controls, settings, close |

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

- **"mpv was not found"**: install mpv, or set `mpv_path = "C:\\path\\to\\mpv.exe"` in the settings file.
- **Something misbehaves**: the log is `%TEMP%\tesserawall.log`, rewritten each run.
- **GPU driver timeout or reset popups**: keep `safe_rendering = true` (the default). If they still
  happen, set `anim_ms = 0`, then try `extra_mpv_args = ["--vo=gpu"]`, then `["--hwdec=no"]`.
- **HDR videos look flat**: that is the cost of `safe_rendering`. Setting it to `false` restores HDR
  output, at the risk above.
- **Playback stutters with many large videos**: try `unfocused_skip = "nonref"`.

## Building the installer

`build.ps1` produces a standalone app (no Python needed) with its own copy of mpv, and a Windows
installer for it. It needs PyInstaller and Inno Setup 6 (a per-user install is found automatically);
see the comments at the top of the script. The bundled mpv is a fixed, checksummed build that the
first run downloads into `downloads\`. The installer has not yet been tried on a clean PC.

## Development

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for how it works, the rules that keep it stable, and
how to run the tests.

## Licence

MIT. See [LICENSE](LICENSE). mpv is a separate program under its own licence and is not distributed
with Tessera Wall.
