param(
    [string]$GroupName = "",
    [string]$GroupId = "",
    [string]$UserAccount = ""
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ConfigPath = Join-Path $ProjectDir "config.json"
$ExampleConfigPath = Join-Path $ProjectDir "config.example.json"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python was not found. Install Python 3.10 or later and add it to PATH."
}

if (-not (Get-Command welink-cli -ErrorAction SilentlyContinue)) {
    throw "welink-cli was not found. Install it and add it to PATH."
}

Write-Host "Refreshing the WeLink token..."
& welink-cli auth login --env pro
if ($LASTEXITCODE -ne 0) {
    throw "WeLink login or token refresh failed."
}

$StatusText = (& welink-cli auth status 2>&1 | Out-String)
Write-Host $StatusText.Trim()
if (-not $UserAccount -and $StatusText -match "UID:\s*([^\s]+)") {
    $UserAccount = $Matches[1]
}
if (-not $UserAccount) {
    $UserAccount = Read-Host "Enter your WeLink UID"
}
if (-not $UserAccount) {
    throw "A WeLink UID is required."
}

if (-not $GroupId) {
    if (-not $GroupName) {
        $GroupName = Read-Host "Enter the exact control group name"
    }
    if (-not $GroupName) {
        throw "A control group name or group ID is required."
    }

    Write-Host "Searching for WeLink group '$GroupName'..."
    $SearchText = (& welink-cli search group --text $GroupName 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) {
        throw "The WeLink group search failed."
    }
    try {
        $SearchResult = $SearchText | ConvertFrom-Json
    }
    catch {
        throw "The WeLink group search did not return valid JSON."
    }

    $Groups = @($SearchResult.search_group_cli.data)
    $ExactGroups = @($Groups | Where-Object { $_.groupName -eq $GroupName })
    if ($ExactGroups.Count -eq 1) {
        $GroupId = [string]$ExactGroups[0].groupId
    }
    elseif ($Groups.Count -eq 1) {
        $GroupId = [string]$Groups[0].groupId
    }
    else {
        if ($Groups.Count -gt 0) {
            Write-Host "Multiple groups were found:"
            $Groups | ForEach-Object { Write-Host ("  {0}: {1}" -f $_.groupName, $_.groupId) }
        }
        $GroupId = Read-Host "Enter the control group ID"
    }
}
if (-not $GroupId) {
    throw "A control group ID is required."
}

$ConfigSource = if (Test-Path $ConfigPath) { $ConfigPath } else { $ExampleConfigPath }
$Config = Get-Content $ConfigSource -Raw | ConvertFrom-Json
$Config.private_chats = @()
$Config.group_chats = @(
    [pscustomobject]@{
        group_id = $GroupId
        allowed_senders = @($UserAccount)
        trigger_prefix = "/"
    }
)

$ConfigJson = $Config | ConvertTo-Json -Depth 20
$Utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($ConfigPath, $ConfigJson, $Utf8WithoutBom)

Write-Host "Configured WeLink UID: $UserAccount"
Write-Host "Configured control group ID: $GroupId"
Write-Host "Saved configuration to: $ConfigPath"
Write-Host "Next, configure the zcode section in config.json, then run .\start.ps1"
