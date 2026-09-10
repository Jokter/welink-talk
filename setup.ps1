$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python was not found. Install Python 3.10 or later and add it to PATH."
}

if (-not (Get-Command welink-cli -ErrorAction SilentlyContinue)) {
    throw "welink-cli was not found. Install it and add it to PATH."
}

if (-not (Test-Path (Join-Path $ProjectDir "config.json"))) {
    Copy-Item (Join-Path $ProjectDir "config.example.json") (Join-Path $ProjectDir "config.json")
    Write-Host "Created config.json. Configure the WeLink account, group ID, and ZCode command."
}

welink-cli auth status
Write-Host "Setup check completed. Configure config.json, then run .\start.ps1"
