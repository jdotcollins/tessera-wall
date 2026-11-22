# Runs INSIDE Windows Sandbox. Does mpv's built-in curl use Windows' certificate store? A fresh Windows has
# not yet downloaded Google's root certificate, so mpv fails to open YouTube's video stream. Then Windows
# itself connects to youtube.com (which makes it fetch the root), and the same link is played again.
#   Run: powershell -ExecutionPolicy Bypass -File tests\sandbox\run.ps1 -Script tls_experiment.ps1
$in  = Split-Path -Parent $MyInvocation.MyCommand.Path
$out = Join-Path (Split-Path -Parent $in) 'out'
$report = Join-Path $out 'report.txt'
function Note($k, $v) { "$k = $v" | Add-Content $report -Encoding utf8 }
"TLS experiment, $(Get-Date -Format s)" | Set-Content $report -Encoding utf8
$setup = Get-ChildItem $in -Filter 'TesseraWall-setup-*.exe' | Select-Object -First 1
Start-Process $setup.FullName -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -Wait
$app = Join-Path $env:LOCALAPPDATA 'Programs\Tessera Wall'
$env:DENO_DIR = Join-Path $env:TEMP 'deno'; $env:DENO_NO_UPDATE_CHECK = '1'
$url = 'https://www.youtube.com/watch?v=aqz-KE-bpKQ'

function Play($name) {
    $log = Join-Path $out "mpv-$name.log"
    $args = @('--no-config', '--vo=null', '--ao=null', '--idle=no', '--keep-open=no', '--length=5', '--ytdl=yes',
              '"--ytdl-format=bestvideo[height<=?720]+bestaudio/best[height<=?720]/best"',
              "`"--script-opts-append=ytdl_hook-ytdl_path=$app\yt-dlp\yt-dlp.exe`"",
              "`"--ytdl-raw-options-append=js-runtimes=deno:$app\yt-dlp\deno.exe`"", "`"--log-file=$log`"", $url)
    $p = Start-Process "$app\mpv\mpv.exe" -ArgumentList $args -PassThru -WindowStyle Hidden
    $p.WaitForExit(90000) | Out-Null
    $text = Get-Content $log -Raw -ErrorAction SilentlyContinue
    Note "$name exit" $p.ExitCode
    Note "$name tls_failed" ($text -match 'TLS certificate verification failed')
    Note "$name played" ($text -match 'finished playback, success|End of file|reason 0')
    Note "$name video_out" (($text -split "`n" | Select-String 'VO: \[' | Select-Object -First 1).Line)
}

function Roots { @(Get-ChildItem Cert:\LocalMachine\Root).Count + @(Get-ChildItem Cert:\CurrentUser\Root).Count }
Note 'roots_before' (Roots)
Note 'gts_root_r1_before' ([bool](Get-ChildItem Cert:\LocalMachine\Root, Cert:\LocalMachine\AuthRoot -ErrorAction SilentlyContinue | Where-Object Subject -match 'GTS Root R1'))
Play 'first'
# Windows' own HTTPS (.NET over Schannel and the Windows chain engine) fetches a missing root on demand
try { $r = Invoke-WebRequest 'https://www.youtube.com/' -UseBasicParsing -TimeoutSec 30; Note 'windows_fetch' $r.StatusCode } catch { Note 'windows_fetch' "failed: $($_.Exception.Message)" }
Note 'roots_after' (Roots)
Note 'gts_root_r1_after' ([bool](Get-ChildItem Cert:\LocalMachine\Root, Cert:\LocalMachine\AuthRoot -ErrorAction SilentlyContinue | Where-Object Subject -match 'GTS Root R1'))
Play 'second'

'done' | Set-Content (Join-Path $out 'done.txt')
Start-Sleep 2
Stop-Computer -Force
