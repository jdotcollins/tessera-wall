<#
Moves the bundled mpv to the newest build from shinchiro/mpv-winbuild-cmake.

  powershell -ExecutionPolicy Bypass -File update-mpv.ps1 [-CheckOnly] [-NoBuild]

  -CheckOnly   only report whether a newer build exists; change nothing
  -NoBuild     update build.ps1 and VERSION but do not build

What it does:
  1. Asks GitHub for the project's latest release and picks the plain x86_64 build
     (not -v3, which needs AVX2; not the -dev or ffmpeg archives).
  2. Downloads it into downloads\ and computes its SHA-256. If GitHub publishes a digest for the file,
     the two must match.
  3. Writes the release tag, file name, SHA-256 and mpv commit into build.ps1, and bumps the patch
     number in VERSION (0.1.0 -> 0.1.1) so users can tell the new installer apart.
  4. Runs build.ps1 -SkipInstaller and the two unit-test files.

What it does NOT do, on purpose:
  - run the end-to-end tests (they take over the screen; run them yourself, against the new mpv:
    set mpv_path in a test settings file, or temporarily put the new mpv first on PATH);
  - build the installer, commit, tag or publish.
Revert with:  git checkout build.ps1 VERSION
#>
param([switch]$CheckOnly, [switch]$NoBuild)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$build = Get-Content build.ps1 -Raw
function Pinned($name) {
    if ($build -match "(?m)^\`$$name\s*=\s*'([^']*)'") { return $Matches[1] }
    throw "build.ps1 has no `$$name line"
}
$current = @{ Release = Pinned 'MpvRelease'; Asset = Pinned 'MpvAsset'; Sha = Pinned 'MpvSha256'; Commit = Pinned 'MpvCommit' }

$headers = @{ 'User-Agent' = 'tesserawall-build'; 'Accept' = 'application/vnd.github+json' }
$api = 'https://api.github.com/repos/shinchiro/mpv-winbuild-cmake/releases/latest'
$rel = Invoke-RestMethod $api -Headers $headers
$asset = $rel.assets | Where-Object { $_.name -match '^mpv-x86_64-\d{8}-git-([0-9a-f]+)\.7z$' } | Select-Object -First 1
if (-not $asset) { throw "Release $($rel.tag_name) has no plain mpv-x86_64-<date>-git-<commit>.7z asset" }
$commit = [regex]::Match($asset.name, 'git-([0-9a-f]+)\.7z$').Groups[1].Value

"Pinned: $($current.Release)  $($current.Asset)"
"Latest: $($rel.tag_name)  $($asset.name)  ($([math]::Round($asset.size / 1MB)) MB, published $($rel.published_at))"
if ($asset.name -eq $current.Asset) { "Already on the latest build."; return }
if ($CheckOnly) { "A newer build is available. Run without -CheckOnly to move to it."; return }

$archive = Join-Path $PSScriptRoot "downloads\$($asset.name)"
if (-not (Test-Path $archive)) {
    New-Item -ItemType Directory -Force (Split-Path $archive) | Out-Null
    "Downloading $($asset.browser_download_url)"
    Invoke-WebRequest $asset.browser_download_url -OutFile "$archive.part" -UseBasicParsing -Headers @{ 'User-Agent' = 'tesserawall-build' }
    Move-Item "$archive.part" $archive -Force
}
$sha = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLower()
if ($asset.PSObject.Properties.Name -contains 'digest' -and $asset.digest) {
    if ($asset.digest -ne "sha256:$sha") { throw "Downloaded file has SHA-256 $sha but GitHub lists $($asset.digest). Delete $archive and retry; do not ship it." }
    "SHA-256 matches GitHub's digest."
} else {
    "GitHub lists no digest for this file; recording the hash of the download: $sha"
}

$new = @{ MpvRelease = $rel.tag_name; MpvAsset = $asset.name; MpvSha256 = $sha; MpvCommit = $commit }
foreach ($k in $new.Keys) {
    $build = [regex]::Replace($build, "(?m)^(\`$$k\s*=\s*)'[^']*'", "`${1}'$($new[$k])'")
}
Set-Content build.ps1 $build -Encoding utf8 -NoNewline
$v = (Get-Content VERSION -Raw).Trim().Split('.')
$version = "$($v[0]).$($v[1]).$([int]$v[2] + 1)"
Set-Content VERSION $version -Encoding ascii
"build.ps1 now pins $($asset.name); VERSION is $version"

if ($NoBuild) { return }
& powershell -NoProfile -ExecutionPolicy Bypass -File build.ps1 -SkipInstaller
if ($LASTEXITCODE -ne 0) { throw "build.ps1 failed" }
foreach ($t in 'test_layout.py', 'test_config.py') {
    python $t | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "$t failed" }
}
@"

Done. Next, by hand:
  1. Run the end-to-end tests (docs\DEVELOPMENT.md, Tests) against the new mpv.
  2. powershell -ExecutionPolicy Bypass -File build.ps1      (builds the installer)
  3. Commit, tag v$version, publish the release with the installer AND downloads\$($asset.name) attached.
"@
