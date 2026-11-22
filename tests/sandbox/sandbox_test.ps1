# Runs INSIDE Windows Sandbox (a fresh Windows): install, use, update, uninstall Tessera Wall, and write a
# report to the mapped out folder. Staged and started by tests\sandbox\run.ps1.
$ErrorActionPreference = 'Continue'
$in  = Split-Path -Parent $MyInvocation.MyCommand.Path
$out = Join-Path (Split-Path -Parent $in) 'out'
$report = Join-Path $out 'report.txt'
function Note($k, $v) { "$k = $v" | Add-Content $report -Encoding utf8 }
function Shot($name) {
    try {
        Add-Type -AssemblyName System.Windows.Forms, System.Drawing
        $b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
        $bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
        $bmp.Save((Join-Path $out "$name.png"), [System.Drawing.Imaging.ImageFormat]::Png)
        $g.Dispose(); $bmp.Dispose()
    } catch { Note "screenshot_$name" "failed: $_" }
}
"Tessera Wall sandbox test, $(Get-Date -Format s)" | Set-Content $report -Encoding utf8
Note 'windows' ((Get-CimInstance Win32_OperatingSystem) | ForEach-Object { "$($_.Caption) $($_.Version)" })
Note 'python_on_path' ([bool](Get-Command python -ErrorAction SilentlyContinue))
Note 'mpv_on_path' ([bool](Get-Command mpv -ErrorAction SilentlyContinue))

# 1. install
$setup = Get-ChildItem $in -Filter 'TesseraWall-setup-*.exe' | Select-Object -First 1
$t = Measure-Command { $p = Start-Process $setup.FullName -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/LOG=$out\install.log" -PassThru -Wait }
Note 'install_exit' $p.ExitCode
Note 'install_seconds' ([int]$t.TotalSeconds)
$app = Join-Path $env:LOCALAPPDATA 'Programs\Tessera Wall'
Note 'installed_version' (Get-Content (Join-Path $app 'VERSION') -ErrorAction SilentlyContinue)
foreach ($f in 'TesseraWall.exe', 'mpv\mpv.exe', 'yt-dlp\yt-dlp.exe', 'yt-dlp\deno.exe', 'yt-dlp\version.txt', 'probe.lua', 'THIRD-PARTY-NOTICES.txt') {
    Note "file $f" (Test-Path (Join-Path $app $f))
}
Note 'start_menu_shortcut' (Test-Path "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Tessera Wall.lnk")
Note 'sendto_shortcuts' (@(Get-ChildItem "$env:APPDATA\Microsoft\Windows\SendTo" -Filter 'Tessera Wall*.lnk').Count)
Note 'menu_video' (Test-Path 'HKCU:\Software\Classes\SystemFileAssociations\video\shell\tesserawall\command')
Note 'menu_mp4' (Test-Path 'HKCU:\Software\Classes\SystemFileAssociations\.mp4\shell\tesserawall\command')
Note 'menu_folder' (Test-Path 'HKCU:\Software\Classes\Directory\shell\tesserawall\command')
Note 'mpv_runs' ((& (Join-Path $app 'mpv\mpv.exe') --version 2>&1 | Select-Object -First 1) -join '')
Note 'ytdlp_runs' ((& (Join-Path $app 'yt-dlp\yt-dlp.exe') --version 2>&1 | Select-Object -Last 1) -join '')
Note 'deno_runs' ((& (Join-Path $app 'yt-dlp\deno.exe') --version 2>&1 | Select-Object -First 1) -join '')
# What yt-dlp makes of YouTube here, warnings included, with the bundled Deno as the wall passes it
$env:DENO_DIR = Join-Path $env:TEMP 'deno-check'; $env:DENO_NO_UPDATE_CHECK = '1'
& (Join-Path $app 'yt-dlp\yt-dlp.exe') --simulate --js-runtimes "deno:$(Join-Path $app 'yt-dlp\deno.exe')" `
    -f 'bestvideo[height<=?1080]+bestaudio/best[height<=?1080]/best' --print '%(format_id)s %(height)s %(protocol)s' `
    'https://www.youtube.com/watch?v=aqz-KE-bpKQ' *> (Join-Path $out 'ytdlp-youtube.txt')
Note 'ytdlp_youtube' ((Get-Content (Join-Path $out 'ytdlp-youtube.txt') -ErrorAction SilentlyContinue) -join ' | ')
Remove-Item Env:DENO_DIR, Env:DENO_NO_UPDATE_CHECK

# 2. a wall: a real file (with a space in its name), a generated clip and a YouTube link, first run of the app
$clip = Join-Path $in 'clip one.mkv'
$lavfi = 'av://lavfi:testsrc2=s=1280x720:r=30'
$yt = 'https://www.youtube.com/watch?v=aqz-KE-bpKQ'
$log = Join-Path $env:TEMP 'tesserawall.log'
$exe = Join-Path $app 'TesseraWall.exe'
$session = Join-Path $out 'session.json'      # saved on exit: each video's position shows it really played
$env:TESSERAWALL_MPV_LOG_DIR = Join-Path $out 'mpv-logs'   # one verbose log per player (wall.spawn)
$w = Start-Process $exe -ArgumentList '--exit-after', '30', '--session', "`"$session`"", "`"$clip`"", $lavfi, $yt -PassThru
Start-Sleep 12
Shot 'wall-12s'
Start-Sleep 10
Shot 'wall-22s'
$w.WaitForExit(60000) | Out-Null
Remove-Item Env:TESSERAWALL_MPV_LOG_DIR
$positions = (Get-Content $session -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json).videos |
             ForEach-Object { "$(Split-Path -Leaf $_.path)=$($_.pos)" }
Note 'session_positions' ($positions -join '; ')
Note 'wall_exit' $w.ExitCode
Copy-Item $log (Join-Path $out 'wall.log') -ErrorAction SilentlyContinue
$text = Get-Content $log -Raw -ErrorAction SilentlyContinue
Note 'wall_file_played' ($text -match 'aspect known [\d.]+ \(\d+x\d+ rot=\d+\): .*clip one\.mkv')
Note 'wall_lavfi_played' ($text -match 'aspect known [\d.]+ \(\d+x\d+ rot=\d+\): av://lavfi')
Note 'wall_youtube_played' ($text -match 'aspect known [\d.]+ \(\d+x\d+ rot=\d+\): https://www\.youtube')
Note 'wall_couldnt_play' ($text -match "could not play|couldn't play")
Note 'settings_file_created' (Test-Path "$env:APPDATA\TesseraWall\config.toml")
Note 'deno_cache_in_own_folder' (Test-Path "$env:LOCALAPPDATA\TesseraWall\deno")
Note 'deno_cache_in_shared_folder' (Test-Path "$env:LOCALAPPDATA\deno")

# 3. a normal launch (no --exit-after): the first daily yt-dlp check sets up the updatable copy
$w = Start-Process $exe -ArgumentList "`"$clip`"" -PassThru
$deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $deadline -and -not (Test-Path "$env:LOCALAPPDATA\TesseraWall\ytdlp.json")) { Start-Sleep 2 }
Start-Sleep 3
Note 'own_ytdlp_copy' (Test-Path "$env:LOCALAPPDATA\TesseraWall\yt-dlp\yt-dlp.exe")
Note 'ytdlp_state' (Get-Content "$env:LOCALAPPDATA\TesseraWall\ytdlp.json" -Raw -ErrorAction SilentlyContinue)
Copy-Item $log (Join-Path $out 'normal.log') -ErrorAction SilentlyContinue
Note 'update_log_line' ((Select-String $log -Pattern 'yt-dlp update' | Select-Object -First 1).Line)
Note 'app_update_check' ((Select-String $log -Pattern 'update check|newer release|latest release' | Select-Object -First 1).Line)
Stop-Process -Id $w.Id -Force -ErrorAction SilentlyContinue
Get-Process mpv -ErrorAction SilentlyContinue | Stop-Process -Force    # inside the throwaway Sandbox only
Start-Sleep 2

# 4. Defender's view of what just ran
try { Note 'defender_detections' (@(Get-MpThreatDetection -ErrorAction Stop).Count) } catch { Note 'defender_detections' "unavailable: $($_.Exception.Message)" }

# 5. uninstall
$unins = Get-ChildItem $app -Filter 'unins*.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
$p = Start-Process $unins.FullName -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -PassThru -Wait
Start-Sleep 5          # the uninstaller finishes from a temporary copy
Note 'uninstall_exit' $p.ExitCode
Note 'left_app_folder' (Test-Path $app)
Note 'left_own_ytdlp' (Test-Path "$env:LOCALAPPDATA\TesseraWall\yt-dlp")
Note 'left_deno_cache' (Test-Path "$env:LOCALAPPDATA\TesseraWall\deno")
Note 'left_ytdlp_state' (Test-Path "$env:LOCALAPPDATA\TesseraWall\ytdlp.json")
Note 'left_localappdata_items' ((Get-ChildItem "$env:LOCALAPPDATA\TesseraWall" -ErrorAction SilentlyContinue | ForEach-Object Name) -join ', ')
Note 'kept_settings' (Test-Path "$env:APPDATA\TesseraWall\config.toml")
Note 'left_menu_video' (Test-Path 'HKCU:\Software\Classes\SystemFileAssociations\video\shell\tesserawall')
Note 'left_menu_mp4' (Test-Path 'HKCU:\Software\Classes\SystemFileAssociations\.mp4\shell\tesserawall')
Note 'left_menu_folder' (Test-Path 'HKCU:\Software\Classes\Directory\shell\tesserawall')
Note 'left_sendto' (@(Get-ChildItem "$env:APPDATA\Microsoft\Windows\SendTo" -Filter 'Tessera Wall*.lnk' -ErrorAction SilentlyContinue).Count)
Note 'left_start_menu' (Test-Path "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Tessera Wall.lnk")

'done' | Set-Content (Join-Path $out 'done.txt')
Start-Sleep 2
Stop-Computer -Force      # closes the Sandbox window; everything in it is discarded
