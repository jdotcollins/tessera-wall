# Clean-PC test in Windows Sandbox. Stages build\sandbox\in (installer, a small clip, sandbox_test.ps1),
# writes a .wsb that maps it read-only plus build\sandbox\out read-write, starts the Sandbox, and waits for
# out\done.txt. Then read build\sandbox\out\report.txt (also wall.png, wall.log, normal.log, install.log).
# NEEDS INTERNET (YouTube, yt-dlp's update check). Opens a Sandbox window for ~3 minutes; nothing on this
# PC is installed or changed. Needs the Windows Sandbox feature.
#   Run: powershell -ExecutionPolicy Bypass -File tests\sandbox\run.ps1 [-Installer path\to\setup.exe]
param([string]$Installer, [string]$Script = 'sandbox_test.ps1')   # -Script: another script in tests\sandbox
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $Installer) {
    $Installer = Get-ChildItem (Join-Path $root 'build\installer') -Filter 'TesseraWall-setup-*.exe' |
                 Sort-Object LastWriteTime | Select-Object -Last 1 -ExpandProperty FullName
}
if (-not $Installer -or -not (Test-Path $Installer)) { throw "No installer: run build.ps1 first, or pass -Installer" }
$clip = Join-Path $env:TEMP 'tesserawall-p4-clips\land0.mkv'
if (-not (Test-Path $clip)) { throw "Run tests\phase4_test.py once first (it makes the clip $clip)" }
if (-not (Test-Path "$env:SystemRoot\System32\WindowsSandbox.exe")) { throw "Windows Sandbox is not enabled" }

$base = Join-Path $root 'build\sandbox'
$in, $out = (Join-Path $base 'in'), (Join-Path $base 'out')
foreach ($d in $in, $out) { if (Test-Path $d) { Remove-Item $d -Recurse -Force }; New-Item -ItemType Directory $d | Out-Null }
Copy-Item $Installer $in
Copy-Item $clip (Join-Path $in 'clip one.mkv')
Copy-Item (Join-Path $PSScriptRoot $Script) $in

$box = 'C:\Users\WDAGUtilityAccount\Desktop\sandbox'
$wsb = Join-Path $base 'tessera-sandbox.wsb'
@"
<Configuration>
  <Networking>Enable</Networking>
  <vGPU>Enable</vGPU>
  <MemoryInMB>8192</MemoryInMB>
  <MappedFolders>
    <MappedFolder><HostFolder>$in</HostFolder><SandboxFolder>$box\in</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>$out</HostFolder><SandboxFolder>$box\out</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder>
  </MappedFolders>
  <LogonCommand>
    <Command>powershell.exe -NoProfile -ExecutionPolicy Bypass -File $box\in\$Script</Command>
  </LogonCommand>
</Configuration>
"@ | Set-Content $wsb -Encoding utf8
"Testing $(Split-Path -Leaf $Installer) in Windows Sandbox..."
# The Sandbox program itself, not the .wsb (opening the file can bring up an "open with" prompt).
Start-Process "$env:SystemRoot\System32\WindowsSandbox.exe" -ArgumentList "`"$wsb`""
$deadline = (Get-Date).AddMinutes(10)
while ((Get-Date) -lt $deadline -and -not (Test-Path (Join-Path $out 'done.txt'))) { Start-Sleep 5 }
if (Test-Path (Join-Path $out 'done.txt')) { Get-Content (Join-Path $out 'report.txt') } else { "Timed out; partial report:"; Get-Content (Join-Path $out 'report.txt') -ErrorAction SilentlyContinue }
