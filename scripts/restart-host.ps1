$ErrorActionPreference = 'Stop'
Start-Sleep -Seconds 2
& (Join-Path $PSScriptRoot 'manage-host-task.ps1') -Action Restart
