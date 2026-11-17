<#
Creates "Tessera Wall" shortcuts in the Explorer Send To menu and on the Desktop.
  Install:    powershell -ExecutionPolicy Bypass -File install.ps1
  Uninstall:  powershell -ExecutionPolicy Bypass -File install.ps1 -Uninstall
Usage afterwards: select videos (or a folder) in Explorer > right-click > Send to > Tessera Wall,
or drop them on the Desktop shortcut. Doing it again while a wall is open adds to that wall.
"Send to > Tessera Wall (random 6)" opens 6 random videos from the selection (change with -RandomCount N).
Double-clicking the Desktop shortcut with no files reopens the last session.
Re-run this script if you move the Tessera Wall folder. Nothing is written to the registry.
#>
param([switch]$Uninstall, [int]$RandomCount = 6)
$ErrorActionPreference = 'Stop'

$app = Join-Path $PSScriptRoot 'tesserawall.pyw'
$sendTo = [Environment]::GetFolderPath('SendTo')
# Shortcut path -> extra arguments placed before the files Explorer appends.
$links = [ordered]@{
    (Join-Path $sendTo 'Tessera Wall.lnk')                                   = ''
    (Join-Path $sendTo "Tessera Wall (random $RandomCount).lnk")             = " --random $RandomCount"
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Tessera Wall.lnk') = ''
}

if ($Uninstall) {
    foreach ($l in @($links.Keys) + @(Get-ChildItem $sendTo -Filter 'Tessera Wall (random *).lnk' | ForEach-Object FullName) | Select-Object -Unique) {
        if (Test-Path -LiteralPath $l) { Remove-Item -LiteralPath $l; "removed $l" }
    }
    return
}

if (-not (Test-Path $app)) { throw "tesserawall.pyw not found next to this script" }
$pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pythonw) {
    $py = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
    if ($py) { $pythonw = Join-Path (Split-Path $py) 'pythonw.exe' }
}
if (-not $pythonw -or -not (Test-Path $pythonw)) { throw "pythonw.exe not found; install Python or add it to PATH" }

$mpv = (Get-Command mpv.exe -ErrorAction SilentlyContinue).Source   # only used for the shortcut icon
$shell = New-Object -ComObject WScript.Shell
foreach ($l in $links.Keys) {
    $s = $shell.CreateShortcut($l)
    $s.TargetPath = $pythonw
    $s.Arguments = '"' + $app + '"' + $links[$l]
    $s.WorkingDirectory = $PSScriptRoot
    $s.Description = 'Open videos together as an mpv wall'
    if ($mpv -and (Test-Path $mpv)) { $s.IconLocation = "$mpv,0" }
    $s.Save()
    "created $l"
}
