<#
Builds the standalone Tessera Wall app and its Windows installer.

  powershell -ExecutionPolicy Bypass -File build.ps1 [-SkipInstaller] [-MpvDir C:\path\to\mpv]

Needs:  pip install pyinstaller tkinterdnd2      (app bundle)
        Inno Setup 6, with ISCC.exe on PATH or in its default folder   (installer)
        Internet access on the first build (to fetch the pinned mpv; cached in downloads\ after that)

Steps:  1. PyInstaller bundles Python + the app into build\dist\TesseraWall\TesseraWall.exe (no Python needed to run).
        2. The pinned mpv build (below) is downloaded once, checked against its SHA-256 and unpacked into
           build\dist\TesseraWall\mpv\ so users never have to install it. -MpvDir ships a local mpv folder
           instead: for trying another build only, never for a release (the notice then says "unpinned").
        3. Inno Setup turns that folder into build\installer\TesseraWall-setup-<version>.exe.

Updating the pinned mpv: pick a release at https://github.com/shinchiro/mpv-winbuild-cmake/releases
(the plain x86_64 build, not -v3, which needs AVX2), copy its tag, file name and the sha256 digest
shown on the release page into the four $Mpv* values below, then re-run the tests against the build.
Releases there are pruned after a while, so keep downloads\ (or the archive) for builds you shipped.
#>
param([string]$MpvDir = '', [switch]$SkipInstaller)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

# The mpv shipped inside the app. mpv is GPL; THIRD-PARTY-NOTICES.txt is generated from these.
$MpvRelease = '20261111'
$MpvAsset   = 'mpv-x86_64-20261111-git-bcde6b93cc.7z'
$MpvSha256  = 'f259b5fd4b5c395617c948a4fadcdf55bba1c5282fb7c13e88626b3f71072baf'
$MpvCommit  = 'bcde6b93cc'

# yt-dlp, which mpv uses to play web links (YouTube, Twitch...). Public domain (Unlicense). Pinned like
# mpv and never modified; the app keeps a separate copy updated from yt-dlp's stable channel (ytdlp.py),
# because sites change faster than releases.
$YtdlpVersion = '2026.10.26'
$YtdlpSha256  = 'f5389d3698ecde4ef06f4e6b1ce0294fe9ac1396228c8990b8d3416d6d177a7d'

# Deno, the JavaScript runtime yt-dlp needs for YouTube (MIT). Pinned per release like mpv; yt-dlp needs
# 2.3.0 or newer. SHA-256 of the official deno-x86_64-pc-windows-msvc.zip (GitHub's published digest).
$DenoVersion = '2.9.8'
$DenoSha256  = 'cba84b26e0df5ae7f74fc66b86a95b866872a86292ebd84fa1e959927edd93bf'

$version = (Get-Content VERSION -Raw).Trim()
"Building Tessera Wall $version"

python -m PyInstaller --version | Out-Null
if ($LASTEXITCODE -ne 0) { throw "PyInstaller is not installed. Run: pip install pyinstaller" }

# 1. app bundle (one folder; --windowed = no console window)
python -m PyInstaller --noconfirm --clean --windowed --name TesseraWall `
    --collect-all tkinterdnd2 `
    --icon (Join-Path $PSScriptRoot 'tesserawall.ico') `
    --distpath build\dist --workpath build\work --specpath build `
    tesserawall.pyw
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
$app = Join-Path $PSScriptRoot 'build\dist\TesseraWall'

# 2. bundled mpv
$pinned = -not $MpvDir
if ($pinned) {
    $archive = Join-Path $PSScriptRoot "downloads\$MpvAsset"
    if (-not (Test-Path $archive)) {
        New-Item -ItemType Directory -Force (Split-Path $archive) | Out-Null
        $url = "https://github.com/shinchiro/mpv-winbuild-cmake/releases/download/$MpvRelease/$MpvAsset"
        "Downloading $url"
        Invoke-WebRequest $url -OutFile "$archive.part" -UseBasicParsing
        Move-Item "$archive.part" $archive -Force
    }
    $hash = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLower()
    if ($hash -ne $MpvSha256) {
        throw "$archive has SHA-256 $hash, expected $MpvSha256. Delete it to download again; if it fails again, do not ship it."
    }
    $MpvDir = Join-Path $PSScriptRoot "build\mpv-$MpvRelease"
    if (Test-Path $MpvDir) { Remove-Item $MpvDir -Recurse -Force }
    New-Item -ItemType Directory -Force $MpvDir | Out-Null
    # Windows' own tar (libarchive) reads .7z. By full path: Git's GNU tar, first on PATH in a Git Bash
    # session, cannot, and reads "C:" as a remote host.
    & (Join-Path $env:SystemRoot 'System32\tar.exe') -xf $archive -C $MpvDir
    if ($LASTEXITCODE -ne 0) { throw "Could not unpack $archive" }
}
$mpvExe = Join-Path $MpvDir 'mpv.exe'
if (-not (Test-Path $mpvExe)) { throw "mpv.exe not found in $MpvDir" }
$dest = Join-Path $app 'mpv'
New-Item -ItemType Directory -Force $dest | Out-Null
# What mpv needs at run time. Not its updater, installer or file-association scripts: the copy inside
# Tessera Wall is updated by building a new Tessera Wall, and must not register itself as the system's player.
foreach ($item in 'mpv.exe', 'd3dcompiler_43.dll', 'mpv', 'doc') {
    $src = Join-Path $MpvDir $item
    if (Test-Path $src) { Copy-Item $src (Join-Path $dest $item) -Recurse -Force }
}
$mpvVersion = (& $mpvExe --version | Select-Object -First 1)
"Bundled: $mpvVersion$(if (-not $pinned) { '  (UNPINNED local build: not for release)' })"

# 2b. bundled yt-dlp (checked against the pinned SHA-256)
$ytdlpCache = Join-Path $PSScriptRoot "downloads\yt-dlp-$YtdlpVersion.exe"
if (-not (Test-Path $ytdlpCache)) {
    New-Item -ItemType Directory -Force (Split-Path $ytdlpCache) | Out-Null
    $url = "https://github.com/yt-dlp/yt-dlp/releases/download/$YtdlpVersion/yt-dlp.exe"
    "Downloading $url"
    Invoke-WebRequest $url -OutFile "$ytdlpCache.part" -UseBasicParsing
    Move-Item "$ytdlpCache.part" $ytdlpCache -Force
}
$hash = (Get-FileHash $ytdlpCache -Algorithm SHA256).Hash.ToLower()
if ($hash -ne $YtdlpSha256) {
    throw "$ytdlpCache has SHA-256 $hash, expected $YtdlpSha256. Delete it to download again; if it fails again, do not ship it."
}
New-Item -ItemType Directory -Force (Join-Path $app 'yt-dlp') | Out-Null
Copy-Item $ytdlpCache (Join-Path $app 'yt-dlp\yt-dlp.exe') -Force
Set-Content (Join-Path $app 'yt-dlp\version.txt') $YtdlpVersion -NoNewline   # read by ytdlp.bundled_version
"Bundled: yt-dlp $YtdlpVersion"

# 2c. bundled Deno, beside yt-dlp (checked against the pinned SHA-256)
$denoCache = Join-Path $PSScriptRoot "downloads\deno-$DenoVersion-x86_64-pc-windows-msvc.zip"
if (-not (Test-Path $denoCache)) {
    $url = "https://github.com/denoland/deno/releases/download/v$DenoVersion/deno-x86_64-pc-windows-msvc.zip"
    "Downloading $url"
    Invoke-WebRequest $url -OutFile "$denoCache.part" -UseBasicParsing
    Move-Item "$denoCache.part" $denoCache -Force
}
$hash = (Get-FileHash $denoCache -Algorithm SHA256).Hash.ToLower()
if ($hash -ne $DenoSha256) {
    throw "$denoCache has SHA-256 $hash, expected $DenoSha256. Delete it to download again; if it fails again, do not ship it."
}
$denoTmp = Join-Path $PSScriptRoot 'build\deno'
if (Test-Path $denoTmp) { Remove-Item $denoTmp -Recurse -Force }
Expand-Archive $denoCache $denoTmp
Copy-Item (Join-Path $denoTmp 'deno.exe') (Join-Path $app 'yt-dlp\deno.exe') -Force
$denoReported = (& (Join-Path $app 'yt-dlp\deno.exe') --version | Select-Object -First 1)
if ($denoReported -notmatch "^deno $([regex]::Escape($DenoVersion)) ") { throw "deno.exe reports '$denoReported'" }
"Bundled: $denoReported"

if ($pinned) {
    $origin = @"
  Build:          $MpvAsset
                  SHA-256 $MpvSha256
  Built by:       the mpv-winbuild-cmake project, release $MpvRelease
                  https://github.com/shinchiro/mpv-winbuild-cmake/releases/tag/$MpvRelease
  mpv source:     https://github.com/mpv-player/mpv/tree/$MpvCommit
  The build scripts, which fetch every library compiled into mpv.exe (FFmpeg, libass, libplacebo and
  others) from its upstream source, are in that project at the same release:
                  https://github.com/shinchiro/mpv-winbuild-cmake/tree/$MpvRelease
"@
} else {
    $origin = "  Build:          unpinned local copy from $MpvDir (development build, not for release)"
}
@"
Tessera Wall $version includes a copy of mpv, a separate program that Tessera Wall starts to play each video.

  $mpvVersion
  Location in this installation: mpv\mpv.exe
$origin

mpv is free software licensed under the GNU General Public License (version 2 or later; builds that
include GPLv3 components are under version 3). It is not part of Tessera Wall and Tessera Wall's MIT licence does
not apply to it. mpv comes with no warranty.

  Project:        https://mpv.io   https://github.com/mpv-player/mpv
  Licence text:   https://github.com/mpv-player/mpv/blob/master/LICENSE.GPL

Tessera Wall also includes yt-dlp $YtdlpVersion (yt-dlp\yt-dlp.exe, SHA-256 $YtdlpSha256), which mpv
uses to play links to web pages such as YouTube. It is released into the public domain (the Unlicense).
This copy is never modified; unless update checks are switched off, Tessera Wall keeps a separate copy in
%LOCALAPPDATA%\TesseraWall\yt-dlp updated from yt-dlp's stable releases.
  Project:        https://github.com/yt-dlp/yt-dlp

Tessera Wall also includes Deno $DenoVersion (yt-dlp\deno.exe, from deno-x86_64-pc-windows-msvc.zip, SHA-256
$DenoSha256), the JavaScript runtime yt-dlp uses for YouTube. Its licence follows at the end of this file.
  Project:        https://deno.com   https://github.com/denoland/deno

Tessera Wall also includes Python (PSF licence), Tcl/Tk (BSD-style licence), tkinterdnd2 (MIT) and tkdnd (BSD).
"@ | Set-Content (Join-Path $app 'THIRD-PARTY-NOTICES.txt') -Encoding utf8
@("", "---- Deno $DenoVersion ----", "") + (Get-Content (Join-Path $PSScriptRoot 'licenses\deno-LICENSE.md')) |
    Add-Content (Join-Path $app 'THIRD-PARTY-NOTICES.txt') -Encoding utf8
Copy-Item tesserawall.ico, tesserawall.png $app -Force          # window icons, loaded by set_icon() at run time
Copy-Item probe.lua $app -Force                                 # mpv script used by probe.py (templates)
Copy-Item LICENSE (Join-Path $app 'LICENSE.txt') -Force
Copy-Item VERSION (Join-Path $app 'VERSION') -Force     # read by the update check (updates.current_version)
Copy-Item README.md (Join-Path $app 'README.md') -Force

"App folder: $app  ($([math]::Round((Get-ChildItem $app -Recurse -File | Measure-Object Length -Sum).Sum / 1MB)) MB)"
if ($SkipInstaller) { return }

# 3. installer
$iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    # per-user install (e.g. winget --scope user) first, then the machine-wide default
    $iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
              "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
              "$env:ProgramFiles\Inno Setup 6\ISCC.exe") |
        Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $iscc -or -not (Test-Path $iscc)) { throw "Inno Setup 6 not found. Install it, or re-run with -SkipInstaller." }
& $iscc "/DAppVersion=$version" installer.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
"Installer: $(Get-ChildItem build\installer\*.exe | Sort-Object LastWriteTime | Select-Object -Last 1 -ExpandProperty FullName)"
