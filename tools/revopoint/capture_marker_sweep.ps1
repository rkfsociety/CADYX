param(
    [ValidatePattern('^[A-Za-z0-9_-]+$')]
    [string]$OutputFolderName = ('revopoint_marker_sweep_' + (Get-Date -Format 'yyyyMMdd_HHmmss')),

    [ValidateRange(12, 1000)]
    [int]$Frames = 1000,

    [ValidateSet(10, 15)]
    [int]$Fps = 10
)

$ErrorActionPreference = 'Stop'
$toolRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $toolRoot '..\..')).Path
$workspaceRoot = Join-Path $repoRoot 'projects'
$outputDirectory = Join-Path $workspaceRoot $OutputFolderName
$captureY16 = Join-Path $toolRoot 'build\CaptureY16.exe'
$captureRgb = Join-Path $toolRoot 'build\CaptureRgb.exe'
$turntable = Join-Path $toolRoot 'build\TurntableController.exe'
$calibration = Join-Path $toolRoot 'calibration\Q.bin'
$liveFrame = Join-Path $workspaceRoot '.revopoint-live.raw'
$stopSignal = Join-Path $outputDirectory 'capture.stop'

foreach ($path in @($captureY16, $captureRgb, $turntable, $calibration)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required input was not found: $path. Run tools/revopoint/build.ps1 first."
    }
}
if (Test-Path -LiteralPath $outputDirectory) {
    throw "Output directory already exists; choose a new name: $outputDirectory"
}
$busy = Get-Process -ErrorAction SilentlyContinue | Where-Object {
    $_.ProcessName -match '^(CaptureY16|CaptureRgb|TurntableController|RevoScan|RevoAssistant)$'
}
if ($busy) {
    throw "Scanner or turntable process is already running: $($busy.ProcessName -join ', ')"
}

New-Item -ItemType Directory -Path $outputDirectory | Out-Null
Copy-Item -LiteralPath $calibration -Destination (Join-Path $outputDirectory 'Q.bin')

function Invoke-Turntable {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $lines = & $turntable @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    $text = ($lines | Out-String).TrimEnd()
    $text | Write-Host
    if ($exitCode -ne 0) {
        throw "Turntable command failed ($exitCode): $($Arguments -join ' ')"
    }
    return $text
}

function Read-TurntableState {
    $text = Invoke-Turntable -Arguments @('query')
    $angleMatch = [regex]::Match($text, '\+QT,CHANGEANGLE; => \+DATA=(-?\d+(?:\.\d+)?);')
    $tiltMatch = [regex]::Match($text, '\+QR,TILTVALUE; => \+DATA=(-?\d+(?:\.\d+)?);')
    if (-not $angleMatch.Success -or -not $tiltMatch.Success) {
        throw 'Turntable query did not return both rotation and tilt angles.'
    }
    return [pscustomobject]@{
        angle = [double]::Parse($angleMatch.Groups[1].Value, [Globalization.CultureInfo]::InvariantCulture)
        tilt = [double]::Parse($tiltMatch.Groups[1].Value, [Globalization.CultureInfo]::InvariantCulture)
        text = $text
    }
}

function Get-NeutralAngle([double]$Angle) {
    $normalized = $Angle % 360.0
    if ($normalized -gt 180.0) { $normalized -= 360.0 }
    if ($normalized -lt -180.0) { $normalized += 360.0 }
    return $normalized
}

function Start-Capture {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$Name
    )
    return Start-Process -PassThru -NoNewWindow -FilePath $FilePath -ArgumentList $Arguments `
        -WorkingDirectory $repoRoot `
        -RedirectStandardOutput (Join-Path $outputDirectory "$Name.stdout.txt") `
        -RedirectStandardError (Join-Path $outputDirectory "$Name.stderr.txt")
}

function Wait-Ready {
    param([System.Diagnostics.Process]$Process, [string]$SignalPath, [string]$Name)
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while ([DateTime]::UtcNow -lt $deadline) {
        $Process.Refresh()
        if (Test-Path -LiteralPath $SignalPath -PathType Leaf) { return }
        if ($Process.HasExited) {
            throw "$Name exited before producing a frame (exit $($Process.ExitCode)); inspect its stdout/stderr logs."
        }
        Start-Sleep -Milliseconds 100
    }
    throw "$Name did not produce its first frame within 30 seconds."
}

function Wait-ProcessExit {
    param([System.Diagnostics.Process]$Process, [string]$Name, [int]$TimeoutSeconds = 240)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        $Process.Refresh()
        if ($Process.HasExited) {
            $Process.WaitForExit()
            if ($Process.ExitCode -ne 0) { throw "$Name failed with exit code $($Process.ExitCode)." }
            return
        }
        Start-Sleep -Milliseconds 250
    }
    throw "$Name exceeded the $TimeoutSeconds second capture limit."
}

$depth = $null
$rgb = $null
$sweep = $null
$sweepStarted = $false
$captureSucceeded = $false
$failureMessage = $null
$baseline = $null
$finalState = $null
$depthExitCode = $null
$rgbExitCode = $null
$sweepExitCode = $null
$startedUtc = [DateTime]::UtcNow

try {
    $baseline = Read-TurntableState
    if ([math]::Abs((Get-NeutralAngle $baseline.angle)) -gt 1.0 -or [math]::Abs($baseline.tilt) -gt 0.5) {
        throw "Empty-table sweep requires a home rotation position and zero tilt; found angle $($baseline.angle), tilt $($baseline.tilt)."
    }

    $depthPrefix = Join-Path $outputDirectory 'depth'
    $depthReady = Join-Path $outputDirectory 'depth.ready'
    $depth = Start-Capture -FilePath $captureY16 -Name 'depth' -Arguments @(
        '--out-prefix', $depthPrefix, '--q-file', (Join-Path $outputDirectory 'Q.bin'),
        '--disparity-scale', '256', '--frames', "$Frames", '--fps', "$Fps",
        '--live-raw', $liveFrame, '--ready-file', $depthReady, '--stop-file', $stopSignal
    )
    Wait-Ready -Process $depth -SignalPath $depthReady -Name 'DepthCam'
    Write-Host 'DepthCam delivered its first valid Y16 frame.'

    $rgbPrefix = Join-Path $outputDirectory 'rgb'
    $rgbReady = Join-Path $outputDirectory 'rgb.ready'
    $rgb = Start-Capture -FilePath $captureRgb -Name 'rgb' -Arguments @(
        '--out-prefix', $rgbPrefix, '--frames', "$Frames", '--fps', "$Fps",
        '--format', 'mjpg', '--ready-file', $rgbReady, '--stop-file', $stopSignal
    )
    Wait-Ready -Process $rgb -SignalPath $rgbReady -Name 'RGBCam'
    Write-Host 'RGBCam delivered its first valid frame; starting the turntable sweep.'

    $sweep = Start-Capture -FilePath $turntable -Name 'turntable' -Arguments @(
        'sweep', '12', '30', (Join-Path $outputDirectory 'turntable_sweep.csv')
    )
    $sweepStarted = $true
    Wait-ProcessExit -Process $sweep -Name 'Turntable sweep'
    $sweepExitCode = $sweep.ExitCode

    $stopHandle = [System.IO.File]::Open($stopSignal, [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    $stopHandle.Dispose()
    Write-Host 'Full rotation verified; both camera streams are closing their timestamped bursts.'

    Wait-ProcessExit -Process $depth -Name 'DepthCam capture' -TimeoutSeconds 30
    $depthExitCode = $depth.ExitCode
    Wait-ProcessExit -Process $rgb -Name 'RGBCam capture' -TimeoutSeconds 30
    $rgbExitCode = $rgb.ExitCode

    $captureSucceeded = $true
}
catch {
    $failureMessage = $_.Exception.Message
}
finally {
    foreach ($process in @($sweep, $depth, $rgb)) {
        if ($null -ne $process) {
            $process.Refresh()
            if (-not $process.HasExited -and $process.Id -ne $PID) {
                Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
                $process.WaitForExit()
            }
        }
    }

    if ($sweepStarted -and -not $captureSucceeded) {
        try { Invoke-Turntable -Arguments @('stop') | Out-Null } catch { $failureMessage += " Stop failed: $($_.Exception.Message)" }
    }

    try {
        $finalState = Read-TurntableState
        if ([math]::Abs((Get-NeutralAngle $finalState.angle)) -gt 1.0 -or [math]::Abs($finalState.tilt) -gt 0.5) {
            $homeText = Invoke-Turntable -Arguments @('zero')
            if ($homeText -notmatch 'angle after zero:\s*(-?\d+(?:\.\d+)?)\s*deg') {
                throw 'Turntable did not report a verified home angle.'
            }
            $finalState = Read-TurntableState
            if ([math]::Abs((Get-NeutralAngle $finalState.angle)) -gt 1.0 -or [math]::Abs($finalState.tilt) -gt 0.5) {
                throw "Turntable did not return to home; angle $($finalState.angle), tilt $($finalState.tilt)."
            }
        }
    }
    catch {
        if ($failureMessage) { $failureMessage += " Final table state could not be verified: $($_.Exception.Message)" }
        else { $failureMessage = "Final table state could not be verified: $($_.Exception.Message)" }
        $captureSucceeded = $false
    }

    $depthTimestamps = Join-Path $outputDirectory 'depth_timestamps.csv'
    $rgbTimestamps = Join-Path $outputDirectory 'rgb_timestamps.csv'
    $manifest = [ordered]@{
        format = 'revopoint-synchronized-marker-sweep-v1'
        status = if ($captureSucceeded -and -not $failureMessage) { 'completed' } else { 'failed' }
        started_utc = $startedUtc.ToString('o')
        finished_utc = [DateTime]::UtcNow.ToString('o')
        requested_frames_per_camera = $Frames
        requested_fps = $Fps
        disparity_scale = 256
        disparity_scale_status = 'provisional_unverified'
        rotation_steps = 12
        step_degrees = 30
        baseline_angle_degrees = if ($baseline) { $baseline.angle } else { $null }
        baseline_tilt_degrees = if ($baseline) { $baseline.tilt } else { $null }
        final_angle_degrees = if ($finalState) { $finalState.angle } else { $null }
        final_tilt_degrees = if ($finalState) { $finalState.tilt } else { $null }
        depth_frame_rows = if (Test-Path -LiteralPath $depthTimestamps) { [Math]::Max(0, (Get-Content $depthTimestamps).Count - 1) } else { 0 }
        rgb_frame_rows = if (Test-Path -LiteralPath $rgbTimestamps) { [Math]::Max(0, (Get-Content $rgbTimestamps).Count - 1) } else { 0 }
        depth_exit_code = $depthExitCode
        rgb_exit_code = $rgbExitCode
        sweep_exit_code = $sweepExitCode
        error = $failureMessage
        table_baseline_query = if ($baseline) { $baseline.text } else { $null }
        table_final_query = if ($finalState) { $finalState.text } else { $null }
    }
    $manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $outputDirectory 'capture_manifest.json') -Encoding utf8NoBOM
}

if (-not $captureSucceeded -or $failureMessage) {
    throw "Marker sweep failed; partial data were preserved in $outputDirectory. $failureMessage"
}
Write-Host "Completed: $outputDirectory"
