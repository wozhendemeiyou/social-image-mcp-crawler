param([int]$Port = 8765)
$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
Set-Location $ProjectRoot
$python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $ProjectRoot "scripts\install_desktop.ps1")
}
if (-not (Test-Path -LiteralPath $python)) { throw "桌面版运行环境创建失败。请先双击 安装桌面版.bat 查看错误。" }
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
# Override project-relative source commands from .env so a copied desktop
# version always runs its own bridge scripts instead of the original folder.
$env:MEDIA_CRAWLER_COMMAND = ('"{0}" "{1}" --platform {{platform}} --query "{{query}}" --item-id "{{item_id}}" --url "{{url}}" --limit {{limit}}' -f $python, (Join-Path $ProjectRoot "scripts\media_crawler_bridge.py"))
$env:DOUYIN_SOURCE_COMMAND = ('"{0}" "{1}" --query "{{query}}" --item-id "{{item_id}}" --url "{{url}}" --limit {{limit}}' -f $python, (Join-Path $ProjectRoot "scripts\douyin_cli_bridge.py"))
$env:XHS_DOWNLOADER_COMMAND = ('"{0}" "{1}" --query "{{query}}" --item-id "{{item_id}}" --url "{{url}}" --limit {{limit}}' -f $python, (Join-Path $ProjectRoot "scripts\xhs_downloader_bridge.py"))
$env:MEDIA_CRAWLER_ROOT = Join-Path $ProjectRoot "third_party\MediaCrawler"
& $python (Join-Path $ProjectRoot "scripts\app_server.py") --port $Port
