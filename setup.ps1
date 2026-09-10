param(
    [string]$GroupName = "",
    [string]$GroupId = "",
    [string]$UserAccount = ""
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
Write-Host "=== WeLink ZCode Bridge Setup ==="
Write-Host ""
Write-Host "[1/5] Refreshing the WeLink token..."
& welink-cli auth login --env pro
if ($LASTEXITCODE -ne 0) {
    throw "WeLink login or token refresh failed."
}

$StatusText = (& welink-cli auth status 2>&1 | Out-String)
if (-not $UserAccount -and $StatusText -match "UID:\s*([^\s]+)") {
    $UserAccount = $Matches[1]
}
$UserAccount = Read-WithDefault "Allowed WeLink UID" $UserAccount
if (-not $UserAccount) {
    throw "A WeLink UID is required."
}
Write-Host "Using WeLink UID: $UserAccount"

Write-Host ""
Write-Host "[2/5] Configure the control group."
$ExistingGroupId = ""
if (@($Config.group_chats).Count -gt 0) {
    $CandidateGroupId = [string]$Config.group_chats[0].group_id
    if ($CandidateGroupId -and $CandidateGroupId -ne "1234567891011") {
        $ExistingGroupId = $CandidateGroupId
    }
}

if (-not $GroupId) {
    if (-not $GroupName) {
        $GroupName = Read-WithDefault "Exact group name, or enter a numeric group ID" $ExistingGroupId
    }
    if ($GroupName -match "^\d+$") {
        $GroupId = $GroupName
    }
    elseif ($GroupName) {
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
        elseif ($Groups.Count -gt 1) {
            for ($Index = 0; $Index -lt $Groups.Count; $Index++) {
                Write-Host ("  {0}. {1} ({2})" -f ($Index + 1), $Groups[$Index].groupName, $Groups[$Index].groupId)
            }
            $Selection = Read-WithDefault "Select a group number" "1"
            if ($Selection -notmatch "^\d+$" -or [int]$Selection -lt 1 -or [int]$Selection -gt $Groups.Count) {
                throw "Invalid group selection."
            }
            $GroupId = [string]$Groups[[int]$Selection - 1].groupId
        }
    }
}
if (-not $GroupId) {
    throw "The control group was not found."
}
Write-Host "Using control group ID: $GroupId"

$Config.private_chats = @()
$Config.group_chats = @(
    [pscustomobject]@{
        group_id = $GroupId
        allowed_senders = @($UserAccount)
        trigger_prefix = "/"
    }
)

Write-Host ""
Write-Host "[3/5] Configure ZCode."
$ExistingExecutable = [string]$Config.zcode.command[0]
if (-not $ExistingExecutable -or $ExistingExecutable -match "Path.To.zcode") {
    $ExistingExecutable = "zcode"
}
$ZCodeExecutable = Read-WithDefault "ZCode executable name or full path" $ExistingExecutable
$ZCodeCommand = Get-Command $ZCodeExecutable -ErrorAction SilentlyContinue
if (-not $ZCodeCommand -and -not (Test-Path $ZCodeExecutable -PathType Leaf)) {
    Write-Warning "ZCode executable was not found. You can finish setup, but requests will fail until this value is corrected."
}

$ExistingArguments = @($Config.zcode.command | Select-Object -Skip 1) -join " "
if (-not $ExistingArguments) {
    $ExistingArguments = "--model {model} --output-format json"
}
$ArgumentTemplate = Read-WithDefault "ZCode argument template" $ExistingArguments

$InputModeDefault = if ($Config.zcode.prompt_via_stdin) { "stdin" } else { "argument" }
while ($true) {
    $InputMode = (Read-WithDefault "Prompt input mode: stdin or argument" $InputModeDefault).ToLowerInvariant()
    if ($InputMode -in @("stdin", "argument")) {
        break
    }
    Write-Host "Please enter stdin or argument."
}
if ($InputMode -eq "argument" -and $ArgumentTemplate -notmatch "\{prompt\}") {
    Write-Host "Argument mode requires the {prompt} placeholder."
    $ArgumentTemplate = Read-WithDefault "ZCode argument template including {prompt}" "$ArgumentTemplate --prompt {prompt}"
}

$Command = @($ZCodeExecutable)
if ($ArgumentTemplate) {
    $Command += @($ArgumentTemplate -split "\s+" | Where-Object { $_ })
}
$Config.zcode.command = $Command
$Config.zcode.prompt_via_stdin = ($InputMode -eq "stdin")

$ExistingModels = @($Config.zcode.models) -join ","
$ModelsText = Read-WithDefault "Available model names, comma-separated" $ExistingModels
$Models = @($ModelsText -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ })
if ($Models.Count -eq 0) {
    throw "At least one model is required."
}
$Config.zcode.models = $Models

$DefaultModel = Read-WithDefault "Default model" ([string]$Config.zcode.default_model)
if ($DefaultModel -notin $Models) {
    Write-Host "The default model was added to the model list."
    $Models = @($DefaultModel) + @($Models)
    $Config.zcode.models = $Models
}
$Config.zcode.default_model = $DefaultModel

Write-Host ""
Write-Host "[4/5] Configure the ZCode working directory."
$ExistingWorkingDirectory = [string]$Config.zcode.working_directory
if (-not $ExistingWorkingDirectory -or $ExistingWorkingDirectory -eq "C:\work") {
    $ExistingWorkingDirectory = (Get-Location).Path
}
$WorkingDirectory = Read-WithDefault "Working directory" $ExistingWorkingDirectory
if (-not (Test-Path $WorkingDirectory -PathType Container)) {
    throw "Working directory does not exist: $WorkingDirectory"
}
$Config.zcode.working_directory = $WorkingDirectory

$ConfigJson = $Config | ConvertTo-Json -Depth 20
$Utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($ConfigPath, $ConfigJson, $Utf8WithoutBom)

Write-Host ""
Write-Host "[5/5] Setup completed."
Write-Host "  WeLink UID:      $UserAccount"
Write-Host "  Control group:   $GroupId"
Write-Host "  ZCode executable: $ZCodeExecutable"
Write-Host "  Default model:   $DefaultModel"
Write-Host "  Working directory: $WorkingDirectory"
Write-Host "  Config file:     $ConfigPath"
Write-Host ""

if (Read-YesNo "Start the bridge now?" $true) {
    & python (Join-Path $ProjectDir "bridge.py") --config $ConfigPath
    exit $LASTEXITCODE
}
Write-Host "Run .\start.ps1 when you are ready."
