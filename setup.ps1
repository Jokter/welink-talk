$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "未找到 Python，请先安装 Python 3.10 或更高版本。"
}

if (-not (Get-Command welink-cli -ErrorAction SilentlyContinue)) {
    throw "未找到 welink-cli，请先完成安装。"
}

if (-not (Test-Path (Join-Path $ProjectDir "config.json"))) {
    Copy-Item (Join-Path $ProjectDir "config.example.json") (Join-Path $ProjectDir "config.json")
    Write-Host "已生成 config.json，请填写 WeLink 账号、群ID和 ZCode 命令。"
}

welink-cli auth status
Write-Host "检查完成。配置后执行 .\start.ps1"
