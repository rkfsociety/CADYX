$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Start-Sleep -Seconds 4
& (Join-Path $PSScriptRoot 'manage-host-task.ps1') -Action Restart
