$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (Test-Path "$here/livekit-server.exe") { Write-Output "already present"; exit 0 }
$rel = (Invoke-RestMethod "https://api.github.com/repos/livekit/livekit/releases/latest").tag_name
$zip = "$here/lk.zip"
$ok = $false
foreach ($u in @(
  "https://github.com/livekit/livekit/releases/download/$rel/livekit_$($rel.TrimStart('v'))_windows_amd64.zip",
  "https://github.com/livekit/livekit/releases/download/$rel/livekit_windows_amd64.zip")) {
  try { Invoke-WebRequest -Uri $u -OutFile $zip -UseBasicParsing; $ok = $true; break } catch { }
}
if (-not $ok) { throw "download failed" }
Expand-Archive $zip -DestinationPath $here -Force
Remove-Item $zip
Write-Output "installed livekit-server.exe"
