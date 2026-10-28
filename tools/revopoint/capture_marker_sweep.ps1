param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectPath,

    [ValidatePattern('^[A-Za-z0-9_-]+$')]
    [string]$OutputFolderName = ('revopoint_marker_poses_' + (Get-Date -Format 'yyyyMMdd_HHmmss')),

    [ValidateRange(1, 10)]
    [int]$FramesPerPose = 1,

    [ValidateSet(10, 15)]
    [int]$Fps = 15,

    [double]$DepthScale = 0
)

$ErrorActionPreference = 'Stop'
$toolRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $toolRoot '..\..')).Path
$workspaceRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot 'projects')).TrimEnd([char[]]@('\', '/'))
$ProjectPath = [IO.Path]::GetFullPath((Resolve-Path -LiteralPath $ProjectPath).Path).TrimEnd([char[]]@('\', '/'))
$projectParent = [IO.Path]::GetDirectoryName($ProjectPath)
if (-not [string]::Equals($projectParent.TrimEnd([char[]]@('\', '/')), $workspaceRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Scan target must be one existing direct CADYX project folder under $workspaceRoot."
}
$projectRecord = Get-Content -LiteralPath (Join-Path $ProjectPath 'project.json') -Raw | ConvertFrom-Json
$projectId = [guid]::Empty
if ($projectRecord.format -ne 'cadyx-project' -or -not [guid]::TryParse([string]$projectRecord.id, [ref]$projectId)) {
    throw "Scan target is not a valid CADYX project folder: $ProjectPath"
}

$captureY16 = Join-Path $toolRoot 'build\CaptureY16.exe'
$captureRgb = Join-Path $toolRoot 'build\CaptureRgb.exe'
$turntable = Join-Path $toolRoot 'build\TurntableController.exe'
$calibration = Join-Path $toolRoot 'calibration\scanners\revopoint-mini-y16\Q.bin'
$depthScaleConfig = Join-Path $toolRoot 'calibration\scanners\revopoint-mini-y16\depth_scale.json'
if (-not $PSBoundParameters.ContainsKey('DepthScale')) {
    $DepthScale = [double](Get-Content -LiteralPath $depthScaleConfig -Raw | ConvertFrom-Json).depth_scale_mm_per_raw_unit
}
if ([double]::IsNaN($DepthScale) -or [double]::IsInfinity($DepthScale) -or $DepthScale -le 0) {
    throw 'DepthScale must be a positive finite value in millimeters per uint16 unit.'
}
foreach ($path in @($captureY16, $captureRgb, $turntable, $calibration, $depthScaleConfig)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required scanner input was not found: $path" }
}
$outputDirectory = Join-Path $ProjectPath $OutputFolderName
if (Test-Path -LiteralPath $outputDirectory) { throw "Output folder already exists; choose a new run name: $outputDirectory" }
$activeScannerPattern = '^(CaptureY16|CaptureRgb|RevoScan|RevoScan5|RevoAssistant)$'
$busy = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -match $activeScannerPattern }
if ($busy) { throw "A scanner application is already open: $($busy.ProcessName -join ', ')" }

New-Item -ItemType Directory -Path $outputDirectory | Out-Null
Copy-Item -LiteralPath $calibration -Destination (Join-Path $outputDirectory 'Q.bin')
Copy-Item -LiteralPath $depthScaleConfig -Destination (Join-Path $outputDirectory 'depth_scale.json')
$liveFrame = Join-Path (Join-Path $repoRoot '.projects-state') '.revopoint-live.raw'
$manifestPath = Join-Path $outputDirectory 'capture_manifest.json'
$posesPath = Join-Path $outputDirectory 'poses.csv'
$manifest = [ordered]@{
    format = 'revopoint-stationary-marker-poses-v2'
    status = 'capturing'
    started_utc = [DateTime]::UtcNow.ToString('o')
    camera = 'DepthCam Y16 and RGBCam MJPG'
    rotation_steps = 12
    step_degrees = 30
    frames_per_pose = $FramesPerPose
    requested_fps = $Fps
    projection_method = 'Q_ray_scaled_to_depth'
    depth_scale_mm_per_raw_unit = $DepthScale
    depth_scale_status = 'provisional_for_direct_UVC'
    motion_while_cameras_connected = $false
    captures = @()
    final_angle_degrees = $null
    final_tilt_degrees = $null
    error = $null
}
$manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $manifestPath -Encoding utf8

function Save-Manifest {
    $manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding utf8
    $manifest.captures | Export-Csv -LiteralPath $posesPath -NoTypeInformation -Encoding utf8
}

function Assert-CamerasDisconnected {
    $active = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -match $activeScannerPattern }
    if ($active) { throw "Turntable motion blocked while scanner process is active: $($active.ProcessName -join ', ')" }
}

function Invoke-Turntable {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    Assert-CamerasDisconnected
    $output = & $turntable @Arguments 2>&1 | Out-String
    $exitCode = $LASTEXITCODE
    $output.TrimEnd() | Write-Host
    if ($exitCode -ne 0) { throw "Turntable command failed ($exitCode): $($Arguments -join ' ')" }
    return $output
}

function Read-TableState {
    $output = Invoke-Turntable -Arguments @('query')
    $angleMatch = [regex]::Match($output, '\+QT,CHANGEANGLE; => \+DATA=(-?\d+(?:\.\d+)?);')
    $tiltMatch = [regex]::Match($output, '\+QR,TILTVALUE; => \+DATA=(-?\d+(?:\.\d+)?);')
    if (-not $angleMatch.Success -or -not $tiltMatch.Success) { throw 'Turntable query did not return both axes.' }
    return [pscustomobject]@{
        angle = [double]::Parse($angleMatch.Groups[1].Value, [Globalization.CultureInfo]::InvariantCulture)
        tilt = [double]::Parse($tiltMatch.Groups[1].Value, [Globalization.CultureInfo]::InvariantCulture)
    }
}

function Get-NeutralAngle([double]$Angle) {
    $value = $Angle % 360.0
    if ($value -gt 180.0) { $value -= 360.0 }
    if ($value -lt -180.0) { $value += 360.0 }
    return $value
}

function Invoke-DepthCapture([string]$Prefix) {
    Assert-CamerasDisconnected
    & $captureY16 --out-prefix $Prefix --q-file (Join-Path $outputDirectory 'Q.bin') `
        --depth-scale $DepthScale --frames $FramesPerPose --fps $Fps --live-raw $liveFrame
    if ($LASTEXITCODE -ne 0) { throw "Depth capture failed at $Prefix ($LASTEXITCODE)." }
    Assert-CamerasDisconnected
}

function Invoke-RgbCapture([string]$Prefix) {
    Assert-CamerasDisconnected
    & $captureRgb --out-prefix $Prefix --frames $FramesPerPose --fps $Fps --format mjpg
    if ($LASTEXITCODE -ne 0) { throw "RGB capture failed at $Prefix ($LASTEXITCODE)." }
    Assert-CamerasDisconnected
}

$completed = $false
try {
    $baseline = Read-TableState
    if ([math]::Abs((Get-NeutralAngle $baseline.angle)) -gt 1.0 -or [math]::Abs($baseline.tilt) -gt 0.5) {
        throw "Stationary marker scan requires rotation 0 and tilt 0; found $($baseline.angle), $($baseline.tilt). No axes were moved."
    }

    for ($index = 0; $index -lt 12; $index++) {
        if ($index -gt 0) {
            Invoke-Turntable -Arguments @('rotate', '30') | Out-Null
        }
        $state = Read-TableState
        $expected = $index * 30.0
        $errorAngle = Get-NeutralAngle ($state.angle - $expected)
        if ([math]::Abs($errorAngle) -gt 1.0 -or [math]::Abs($state.tilt) -gt 0.5) {
            throw "Pose $index is outside its rotation target or tilt is nonzero (angle=$($state.angle), tilt=$($state.tilt))."
        }

        $depthPrefixName = 'depth_pose_{0:D2}' -f $index
        $rgbPrefixName = 'rgb_pose_{0:D2}' -f $index
        $depthPrefix = Join-Path $outputDirectory $depthPrefixName
        $rgbPrefix = Join-Path $outputDirectory $rgbPrefixName
        $captureStarted = [DateTime]::UtcNow
        Invoke-DepthCapture $depthPrefix
        Invoke-RgbCapture $rgbPrefix
        $captureFinished = [DateTime]::UtcNow
        Assert-CamerasDisconnected

        $manifest.captures += [ordered]@{
            pose_index = $index
            expected_angle_degrees = $expected
            reported_angle_degrees = $state.angle
            tilt_degrees = $state.tilt
            capture_started_utc = $captureStarted.ToString('o')
            capture_finished_utc = $captureFinished.ToString('o')
            depth_prefix = $depthPrefixName
            rgb_prefix = $rgbPrefixName
            camera_processes_closed_before_next_move = $true
        }
        Save-Manifest
        Write-Host "Completed stationary pose $($index + 1)/12 at $($state.angle)°; both cameras are closed."
    }

    # Final 30-degree move returns the table to zero; both camera processes have exited.
    Invoke-Turntable -Arguments @('rotate', '30') | Out-Null
    $final = Read-TableState
    if ([math]::Abs((Get-NeutralAngle $final.angle)) -gt 1.0 -or [math]::Abs($final.tilt) -gt 0.5) {
        throw "Final rotation-only home check failed (angle=$($final.angle), tilt=$($final.tilt))."
    }
    $manifest.final_angle_degrees = $final.angle
    $manifest.final_tilt_degrees = $final.tilt
    $manifest.status = 'captured_unregistered'
    $completed = $true
}
catch {
    $manifest.error = $_.Exception.Message
    throw
}
finally {
    Assert-CamerasDisconnected
    if (-not $completed) {
        $manifest.status = 'failed_partial_captures_preserved'
        try {
            $state = Read-TableState
            $manifest.final_angle_degrees = $state.angle
            $manifest.final_tilt_degrees = $state.tilt
        } catch { $manifest.error += "; final table state unavailable: $($_.Exception.Message)" }
    }
    $manifest.finished_utc = [DateTime]::UtcNow.ToString('o')
    Save-Manifest
}

Write-Host "Saved 12 stationary pose captures in $outputDirectory. No camera was connected during table rotation."
