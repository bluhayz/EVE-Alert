# Chocolatey install script for EVE Alert (#179, v8.0 stretch goal).
#
# PLACEHOLDERS to fill in per-release before `choco pack`/`choco push`
# (mirrors packaging/winget/'s pattern -- see ../../WINGET.md):
#   __VERSION__           e.g. 8.0.0
#   __INSTALLER_SHA256__  sha256 of EVE-Alert-Setup-__VERSION__.exe,
#                         from that release's checksums.txt asset

$ErrorActionPreference = 'Stop'

$packageName = 'eve-alert'
$version     = '__VERSION__'
$url         = "https://github.com/bluhayz/EVE-Alert/releases/download/v$version/EVE-Alert-Setup-$version.exe"
$checksum    = '__INSTALLER_SHA256__'

$packageArgs = @{
  packageName    = $packageName
  fileType       = 'exe'
  url            = $url
  checksum       = $checksum
  checksumType   = 'sha256'
  # Matches installer.iss's Inno Setup silent-install switches.
  silentArgs     = '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART'
  validExitCodes = @(0)
}

Install-ChocolateyPackage @packageArgs
