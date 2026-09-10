$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectDir
python -m py_compile .\bridge.py
python .\bridge.py --config .\config.json --once --dry-run
