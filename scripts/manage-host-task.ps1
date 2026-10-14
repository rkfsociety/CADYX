[CmdletBinding()]
param(
    [ValidateSet('Install', 'Restart', 'Stop', 'Status', 'Uninstall')]
    [string]$Action = 'Install'
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonw = Join-Path $projectRoot '.runtime\python\pythonw.exe'
$launcher = Join-Path $PSScriptRoot 'run-host-background.py'
$taskName = 'CADYX Local Host'
$port = 8888
$healthUrl = "http://127.0.0.1:$port/"

function Get-CadyxTask {
    Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
}

function Get-CadyxListener {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
}

function Get-CadyxListenerProcess {
    $listener = Get-CadyxListener
    if (!$listener) { return $null }
    Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)" -ErrorAction SilentlyContinue
}

function Test-CadyxProcess($process) {
    if (!$process) { return $false }
    return $process.ExecutablePath -ieq $pythonw
}

function Assert-CadyxPortFree {
    $process = Get-CadyxListenerProcess
    if ($process) {
        throw "Port $port is already used by PID $($process.ProcessId) ($($process.ExecutablePath)); it was left untouched."
    }
}

function Wait-CadyxStopped {
    $deadline = [DateTime]::UtcNow.AddSeconds(25)
    do {
        $task = Get-CadyxTask
        $listener = Get-CadyxListener
        if ((!$task -or $task.State -ne 'Running') -and !$listener) { return }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)

    $process = Get-CadyxListenerProcess
    if ($process) {
        throw "CADYX did not release port $port; listener PID $($process.ProcessId) remains."
    }
    throw "The scheduled task '$taskName' did not stop within 25 seconds."
}

function Stop-CadyxTask {
    $task = Get-CadyxTask
    if ($task -and $task.State -eq 'Running') {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction Stop
    }

    $deadline = [DateTime]::UtcNow.AddSeconds(25)
    do {
        $task = Get-CadyxTask
        if (!$task -or $task.State -ne 'Running') { break }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    if ($task -and $task.State -eq 'Running') {
        throw "The scheduled task '$taskName' did not stop within 25 seconds."
    }

    # If Task Scheduler leaves the listener process behind, stop only this checkout's pythonw.
    $process = Get-CadyxListenerProcess
    if ($process) {
        if (!(Test-CadyxProcess $process)) {
            throw "Port $port remains occupied by PID $($process.ProcessId) ($($process.ExecutablePath)); it was left untouched."
        }
        Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
    }
    Wait-CadyxStopped
}

function Wait-CadyxHealthy {
    $deadline = [DateTime]::UtcNow.AddSeconds(35)
    $lastFailure = 'No listener on port 8888 yet.'
    do {
        $process = Get-CadyxListenerProcess
        if ($process) {
            if (!(Test-CadyxProcess $process)) {
                throw "Port $port was taken by a different process (PID $($process.ProcessId)); it was left untouched."
            }
            try {
                $response = Invoke-WebRequest -Uri $healthUrl -TimeoutSec 3
                if ($response.StatusCode -eq 200) {
                    Write-Host "CADYX is running at $healthUrl (PID $($process.ProcessId))."
                    return
                }
                $lastFailure = "HTTP $($response.StatusCode) from $healthUrl"
            } catch {
                $lastFailure = $_.Exception.Message
            }
        }
        Start-Sleep -Seconds 1
    } while ([DateTime]::UtcNow -lt $deadline)

    $logDirectory = Join-Path $env:LOCALAPPDATA 'CADYX'
    throw "CADYX did not become healthy within 35 seconds. Last check: $lastFailure. See $logDirectory\host.stdout.log and host.stderr.log."
}

switch ($Action) {
    'Status' {
        $task = Get-CadyxTask
        if (!$task) {
            Write-Host "Scheduled task '$taskName' is not installed."
            $process = Get-CadyxListenerProcess
            if ($process) {
                Write-Host "Port $port is served by PID $($process.ProcessId) ($($process.ExecutablePath))."
            }
            return
        }

        $taskInfo = Get-ScheduledTaskInfo -TaskName $taskName
        [pscustomobject]@{
            Task = $taskName
            State = $task.State
            Trigger = 'At user logon'
            RunAs = $task.Principal.UserId
            LogonType = $task.Principal.LogonType
            LastRunTime = $taskInfo.LastRunTime
            LastTaskResult = if ($task.State -eq 'Running') { 'Running' } else { $taskInfo.LastTaskResult }
            Url = $healthUrl
        } | Format-List

        if (Get-CadyxListenerProcess) { Wait-CadyxHealthy }
        else { Write-Host 'No CADYX listener is currently bound to port 8888.' }
        return
    }
    'Stop' {
        if (!(Get-CadyxTask)) { throw "Scheduled task '$taskName' is not installed." }
        Stop-CadyxTask
        Write-Host 'CADYX stopped. The startup task remains installed.'
        return
    }
    'Uninstall' {
        $task = Get-CadyxTask
        if (!$task) {
            Write-Host "Scheduled task '$taskName' is not installed."
            return
        }
        Stop-CadyxTask
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop
        Write-Host 'CADYX startup task removed.'
        return
    }
    'Install' {
        if (!(Test-Path -LiteralPath $pythonw -PathType Leaf)) {
            throw "CADYX Python runtime not found at $pythonw. Run scripts/setup-runtime.ps1 first."
        }
        if (!(Test-Path -LiteralPath $launcher -PathType Leaf)) {
            throw "CADYX background launcher not found at $launcher."
        }

        if (Get-CadyxTask) { Stop-CadyxTask }
        Assert-CadyxPortFree

        $taskArguments = '"{0}"' -f $launcher
        $scheduledAction = New-ScheduledTaskAction -Execute $pythonw -Argument $taskArguments -WorkingDirectory $projectRoot
        $user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
        $principal = New-ScheduledTaskPrincipal `
            -UserId $user `
            -LogonType Interactive `
            -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet `
            -StartWhenAvailable `
            -RestartCount 999 `
            -RestartInterval (New-TimeSpan -Minutes 1) `
            -ExecutionTimeLimit ([TimeSpan]::Zero) `
            -MultipleInstances IgnoreNew `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries

        Register-ScheduledTask `
            -TaskName $taskName `
            -Action $scheduledAction `
            -Trigger $trigger `
            -Principal $principal `
            -Settings $settings `
            -Description 'Runs hidden CADYX on loopback port 8888 at user logon and restarts it after failures.' `
            -Force `
            -ErrorAction Stop | Out-Null
        if (!(Get-CadyxTask)) { throw "Task Scheduler did not register '$taskName'." }
    }
    'Restart' {
        if (!(Get-CadyxTask)) {
            throw "Scheduled task '$taskName' is not installed. Run scripts/manage-host-task.ps1 -Action Install first."
        }
        Stop-CadyxTask
    }
}

Start-ScheduledTask -TaskName $taskName -ErrorAction Stop
Wait-CadyxHealthy
