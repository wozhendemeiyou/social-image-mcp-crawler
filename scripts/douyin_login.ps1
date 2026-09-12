param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$env:PYTHONPATH = Join-Path $ProjectRoot "third_party\dy-cli\src"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

# Windows PowerShell 5 defaults to a legacy code page. dy-cli uses Chinese
# messages and Unicode status symbols, so make both the console and child
# Python process UTF-8 before Rich writes anything.
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $Utf8NoBom
[Console]::OutputEncoding = $Utf8NoBom
$OutputEncoding = $Utf8NoBom

Write-Output "A browser window will open. Scan the Douyin QR code to log in."
& $Python -m dy_cli.main login
if ($LASTEXITCODE -ne 0) {
    throw "dy-cli login failed with exit code $LASTEXITCODE"
}
