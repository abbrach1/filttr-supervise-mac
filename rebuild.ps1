# Incremental rebuild: keeps build/ cache, no --clean. Zips exe to Desktop.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$sw = [Diagnostics.Stopwatch]::StartNew()
python -m PyInstaller --noconfirm FilttrSupervise.spec
$ver = (Select-String -Path filttr_supervise\__init__.py -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value
$zip = Join-Path ([Environment]::GetFolderPath('Desktop')) "FilttrSupervise-v$ver-windows.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path dist\FilttrSupervise.exe, config.json -DestinationPath $zip
$sw.Stop()
Write-Host "ZIP: $zip"
Write-Host ("Build time: {0:N0}s" -f $sw.Elapsed.TotalSeconds)
