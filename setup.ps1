param(
    [string]$ControlUserAccount = ""
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ConfigPath = Join-Path $ProjectDir "config.json"
$ExampleConfigPath = Join-Path $ProjectDir "config.example.json"

function Read-WithDefault {
    param(
        [string]$Prompt,
        [string]$Default = ""
    )
    $Suffix = if ($Default) { " [$Default]" } else { "" }
    $Value = Read-Host "$Prompt$Suffix"
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $Default
    }
    return $Value.Trim()
}

function Read-YesNo {
    param(
        [string]$Prompt,
        [bool]$Default = $true
    )
    $Hint = if ($Default) { "Y/n" } else { "y/N" }
    while ($true) {
        $Value = (Read-Host "$Prompt [$Hint]").Trim().ToLowerInvariant()
        if (-not $Value) {
            return $Default
        }
        if ($Value -in @("y", "yes")) {
            return $true
        }
        if ($Value -in @("n", "no")) {
            return $false
        }
        Write-Host "Please enter y or n."
    }
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python was not found. Install Python 3.10 or later and add it to PATH."
}
if (-not (Get-Command welink-cli -ErrorAction SilentlyContinue)) {
    throw "welink-cli was not found. Install it and add it to PATH."
}

$ConfigSource = if (Test-Path $ConfigPath) { $ConfigPath } else { $ExampleConfigPath }
$Config = Get-Content $ConfigSource -Raw | ConvertFrom-Json

Write-Host ""
Write-Host "=== WeLink Pi Bridge Setup ==="
Write-Host ""
Write-Host "[1/5] Refreshing the WeLink token..."
& welink-cli auth login --env pro
if ($LASTEXITCODE -ne 0) {
    throw "WeLink login or token refresh failed."
}

$StatusText = (& welink-cli auth status 2>&1 | Out-String)
$LoggedInUserAccount = ""
if ($StatusText -match "UID:\s*([^\s]+)") {
    $LoggedInUserAccount = $Matches[1]
}
if ($LoggedInUserAccount) {
    Write-Host "Signed-in WeLink UID: $LoggedInUserAccount"
}

Write-Host ""
Write-Host "[2/5] Configure the private control user."
$ExistingControlUser = ""
if (@($Config.private_chats).Count -gt 0) {
    $CandidateAccount = [string]$Config.private_chats[0].account
    if ($CandidateAccount -and $CandidateAccount -ne "a0012345") {
        $ExistingControlUser = $CandidateAccount
    }
}
if (-not $ControlUserAccount) {
    $DefaultControlUser = if ($ExistingControlUser) { $ExistingControlUser } else { "p_xiaoluban" }
    $ControlUserAccount = Read-WithDefault "WeLink account allowed to control Pi" $DefaultControlUser
}
if (-not $ControlUserAccount) {
    throw "A private control user is required."
}
Write-Host "Using private control user: $ControlUserAccount"

$Config.private_chats = @(
    [pscustomobject]@{
        account = $ControlUserAccount
        allowed_senders = @($ControlUserAccount)
        trigger_prefix = "/"
    }
)
if ($Config.PSObject.Properties.Name -contains "group_chats") {
    $Config.PSObject.Properties.Remove("group_chats")
}

Write-Host ""
Write-Host "[3/5] Discover Pi."
$DiscoveryScript = Join-Path $ProjectDir "discover_pi.py"

function Get-PiDiscovery {
    $Text = (& python $DiscoveryScript 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) {
        return $null
    }
    try {
        return $Text | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

$Discovery = Get-PiDiscovery
if (-not $Discovery -or @($Discovery.command).Count -eq 0) {
    Write-Host "Pi is not installed or is not available on PATH."
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw "npm was not found. Install Node.js, then run setup again."
    }
    if (Read-YesNo "Install Pi now?" $true) {
        & npm install -g --ignore-scripts '@earendil-works/pi-coding-agent'
        if ($LASTEXITCODE -ne 0) {
            throw "Pi installation failed."
        }
        $Discovery = Get-PiDiscovery
    }
}
if (-not $Discovery -or @($Discovery.command).Count -eq 0) {
    throw "The pi command was not found. Restart PowerShell after installing Pi, then run setup again."
}

if ($Discovery.error) {
    Write-Warning $Discovery.error
}
$Models = @($Discovery.models)
if ($Models.Count -eq 0) {
    Write-Host "Start Pi in another terminal, complete /login, then run this setup again."
    throw "No authenticated Pi models were found."
}

$DefaultModel = [string]$Discovery.default_model
if (-not $DefaultModel -or $DefaultModel -notin $Models) {
    $DefaultModel = [string]$Models[0]
}
$PiCommand = @($Discovery.command) + @(
    "--model",
    "{model}",
    "--no-session",
    "--no-approve",
    "-p"
)

$PreviousWorkingDirectory = ""
$PreviousAllowedRoots = @()
if ($Config.PSObject.Properties.Name -contains "pi") {
    $PreviousWorkingDirectory = [string]$Config.pi.working_directory
    $PreviousAllowedRoots = @($Config.pi.allowed_working_roots)
}
elseif ($Config.PSObject.Properties.Name -contains "zcode") {
    $PreviousWorkingDirectory = [string]$Config.zcode.working_directory
}

$PiConfig = [pscustomobject]@{
    command = $PiCommand
    prompt_via_stdin = $true
    working_directory = $PreviousWorkingDirectory
    allowed_working_roots = $PreviousAllowedRoots
    timeout_seconds = 900
    history_turns = 6
    default_model = $DefaultModel
    models = $Models
}
if ($Config.PSObject.Properties.Name -contains "pi") {
    $Config.pi = $PiConfig
}
else {
    $Config | Add-Member -NotePropertyName "pi" -NotePropertyValue $PiConfig
}
if ($Config.PSObject.Properties.Name -contains "zcode") {
    $Config.PSObject.Properties.Remove("zcode")
}

Write-Host ("Detected Pi: {0}" -f (@($Discovery.command) -join " "))
if ($Discovery.version) {
    Write-Host "Detected version: $($Discovery.version)"
}
Write-Host "Detected $($Models.Count) available models."
Write-Host "Default model: $DefaultModel"

Write-Host ""
Write-Host "[4/5] Configure Pi workspaces."
$ExistingWorkingDirectory = [string]$Config.pi.working_directory
if (-not $ExistingWorkingDirectory -or $ExistingWorkingDirectory -eq "C:\work") {
    $ExistingWorkingDirectory = (Get-Location).Path
}
$ExistingRoots = @($Config.pi.allowed_working_roots | Where-Object { $_ })
if ($ExistingRoots.Count -eq 0) {
    $ExistingRoots = @($ExistingWorkingDirectory)
}
$RootsText = Read-WithDefault "Allowed workspace roots, separated by semicolons" ($ExistingRoots -join ";")
$AllowedRoots = @($RootsText -split ";" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
if ($AllowedRoots.Count -eq 0) {
    throw "At least one allowed workspace root is required."
}
foreach ($RootPath in $AllowedRoots) {
    if (-not (Test-Path $RootPath -PathType Container)) {
        throw "Workspace root does not exist: $RootPath"
    }
}
$WorkingDirectory = Read-WithDefault "Default working directory" $ExistingWorkingDirectory
if (-not (Test-Path $WorkingDirectory -PathType Container)) {
    throw "Working directory does not exist: $WorkingDirectory"
}
$ResolvedWorkingDirectory = [System.IO.Path]::GetFullPath($WorkingDirectory)
$InsideAllowedRoot = $false
foreach ($RootPath in $AllowedRoots) {
    $ResolvedRoot = [System.IO.Path]::GetFullPath($RootPath).TrimEnd('\')
    if ($ResolvedWorkingDirectory -eq $ResolvedRoot -or $ResolvedWorkingDirectory.StartsWith($ResolvedRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        $InsideAllowedRoot = $true
        break
    }
}
if (-not $InsideAllowedRoot) {
    throw "Default working directory must be inside an allowed workspace root."
}
$Config.pi.working_directory = $WorkingDirectory
$Config.pi.allowed_working_roots = $AllowedRoots

$ConfigJson = $Config | ConvertTo-Json -Depth 20
$Utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($ConfigPath, $ConfigJson, $Utf8WithoutBom)

Write-Host ""
Write-Host "[5/5] Setup completed."
if ($LoggedInUserAccount) {
    Write-Host "  Signed-in UID:     $LoggedInUserAccount"
}
Write-Host "  Control user:      $ControlUserAccount"
Write-Host ("  Pi command:       {0}" -f (@($Config.pi.command) -join " "))
Write-Host "  Default model:   $DefaultModel"
Write-Host "  Working directory: $WorkingDirectory"
Write-Host ("  Allowed roots:     {0}" -f ($AllowedRoots -join "; "))
Write-Host "  Config file:     $ConfigPath"
Write-Host ""

if (Read-YesNo "Start the bridge now?" $true) {
    & python (Join-Path $ProjectDir "bridge.py") --config $ConfigPath
    exit $LASTEXITCODE
}
Write-Host "Run .\start.ps1 when you are ready."
