param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectPath,
    [double]$DepthScale = 0,
    [string]$RunId = ([DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ'))
)

$ErrorActionPreference = 'Stop'
$toolRoot = $PSScriptRoot
$repoRoot = (Resolve-Path (Join-Path $toolRoot '..\..')).Path
$captureExe = Join-Path $toolRoot 'build\CaptureY16.exe'
$turntableExe = Join-Path $toolRoot 'build\TurntableController.exe'
$calibration = Join-Path $toolRoot 'calibration\scanners\revopoint-mini-y16\Q.bin'
$depthScaleConfig = Join-Path $toolRoot 'calibration\scanners\revopoint-mini-y16\depth_scale.json'
if (-not $PSBoundParameters.ContainsKey('DepthScale')) {
    $DepthScale = [double](Get-Content -LiteralPath $depthScaleConfig -Raw | ConvertFrom-Json).depth_scale_mm_per_raw_unit
}
if ([double]::IsNaN($DepthScale) -or [double]::IsInfinity($DepthScale) -or $DepthScale -le 0) {
    throw 'DepthScale must be a positive finite value in millimeters per uint16 unit.'
}
$liveRaw = Join-Path $repoRoot '.projects-state\.revopoint-live.raw'
$workspaceRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot 'projects')).TrimEnd([char[]]@('\', '/'))
$ProjectPath = (Resolve-Path -LiteralPath $ProjectPath).Path
$ProjectPath = [IO.Path]::GetFullPath($ProjectPath).TrimEnd([char[]]@('\', '/'))
$projectParent = [IO.Path]::GetDirectoryName($ProjectPath)
if (-not [string]::Equals($projectParent.TrimEnd([char[]]@('\', '/')), $workspaceRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Scan target must be one existing direct CADYX project folder under $workspaceRoot."
}
$projectRecord = Get-Content -LiteralPath (Join-Path $ProjectPath 'project.json') -Raw | ConvertFrom-Json
$projectId = [guid]::Empty
if ($projectRecord.format -ne 'cadyx-project' -or
    -not [guid]::TryParse([string]$projectRecord.id, [ref]$projectId)) {
    throw "Scan target is not a valid CADYX project folder: $ProjectPath"
}
foreach ($path in @($captureExe, $turntableExe, $calibration, $depthScaleConfig)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required scanner input is missing: $path" }
}
$busy = Get-Process -ErrorAction SilentlyContinue | Where-Object {
    $_.ProcessName -match '^(CaptureY16|CaptureRgb|TurntableController|RevoScan|RevoAssistant)$'
}
if ($busy) { throw "Scanner/table software is already using the hardware: $($busy.ProcessName -join ', ')" }

$runId = $RunId
if ($runId -notmatch '^[0-9]{8}T[0-9]{6}Z$') { throw 'RunId must use the UTC form yyyyMMddTHHmmssZ.' }
$seriesDirectory = Join-Path $ProjectPath "revopoint_$runId"
if (Test-Path -LiteralPath $seriesDirectory -PathType Leaf) {
    throw "Scan series path is not a directory: $seriesDirectory"
}
if (-not (Test-Path -LiteralPath $seriesDirectory -PathType Container)) {
    New-Item -ItemType Directory -Path $seriesDirectory | Out-Null
}
$manifestPath = Join-Path $seriesDirectory "capture_manifest.json"
$seriesEntries = @(Get-ChildItem -LiteralPath $seriesDirectory -Force)
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf) -and $seriesEntries.Count -gt 0) {
    throw "Existing scan series folder has files but no resumable manifest; refusing to overwrite: $seriesDirectory"
}
$tiltPasses = @(
    [pscustomobject]@{ degrees = 0; key = 'p000' },
    [pscustomobject]@{ degrees = 5; key = 'p005' },
    [pscustomobject]@{ degrees = -5; key = 'm005' },
    [pscustomobject]@{ degrees = 10; key = 'p010' },
    [pscustomobject]@{ degrees = -10; key = 'm010' }
)
if (Test-Path -LiteralPath $manifestPath) {
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($manifest.format -ne 'revopoint-mini-multi-tilt-capture-v1' -or
        [double]$manifest.depth_scale_mm_per_raw_unit -ne $DepthScale) {
        throw "Existing manifest is incompatible with this scan run: $manifestPath"
    }
    $manifest.status = 'running'
    $manifest.error = $null
} else {
    $manifest = [ordered]@{
        format = 'revopoint-mini-multi-tilt-capture-v1'
        status = 'running'
        started_utc = [DateTime]::UtcNow.ToString('o')
        camera = 'DepthCam Y16 640x400'
        calibration_file = (Resolve-Path $calibration).Path
        projection_method = 'Q_ray_scaled_to_depth'
        depth_scale_mm_per_raw_unit = $DepthScale
        depth_scale_status = 'reported_by_Revo_Scan; direct_UVC_mapping_provisional'
        commanded_rotation_step_degrees = 30
        passes = @()
        final_rotation_degrees = $null
        final_tilt_degrees = $null
        error = $null
    }
}

function Save-Manifest {
    $manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
}

function Invoke-Controller {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $output = & $turntableExe @Arguments 2>&1 | Out-String
    $exitCode = $LASTEXITCODE
    $output.TrimEnd() | Write-Host
    if ($exitCode -ne 0) { throw "Turntable operation failed ($exitCode): $($Arguments -join ' ')" }
    return $output
}

function Read-TableState {
    $output = Invoke-Controller -Arguments @('query')
    $rotation = [regex]::Match($output, '\+QT,CHANGEANGLE; => \+DATA=(-?\d+(?:\.\d+)?);')
    $tilt = [regex]::Match($output, '\+QR,TILTVALUE; => \+DATA=(-?\d+(?:\.\d+)?);')
    if (-not $rotation.Success -or -not $tilt.Success) { throw 'Turntable query did not return both axes.' }
    return [pscustomobject]@{
        rotation = [double]::Parse($rotation.Groups[1].Value, [Globalization.CultureInfo]::InvariantCulture)
        tilt = [double]::Parse($tilt.Groups[1].Value, [Globalization.CultureInfo]::InvariantCulture)
    }
}

function Get-HomeDelta([double]$Angle) {
    $normalized = $Angle % 360.0
    if ($normalized -gt 180.0) { $normalized -= 360.0 }
    if ($normalized -lt -180.0) { $normalized += 360.0 }
    return $normalized
}

function Confirm-Home([string]$Phase, [double]$ExpectedTilt = 0, [double]$TiltTolerance = 0.5) {
    $state = Read-TableState
    if ([math]::Abs((Get-HomeDelta $state.rotation)) -gt 1.0 -or [math]::Abs($state.tilt - $ExpectedTilt) -gt $TiltTolerance) {
        throw "$Phase did not finish at the home position (rotation=$($state.rotation), tilt=$($state.tilt))."
    }
}

function Get-ValidatedCaptureRows([string]$PoseCsv, [double]$ExpectedTilt) {
    $rows = @(Import-Csv -LiteralPath $PoseCsv | Where-Object { $_.event -eq 'capture_completed' })
    if ($rows.Count -ne 12) { throw "Pose log $PoseCsv contains $($rows.Count) complete captures instead of 12." }
    $baseAngle = [double]$rows[0].angle_degrees
    for ($index = 0; $index -lt $rows.Count; $index++) {
        $row = $rows[$index]
        if ([math]::Abs([double]$row.tilt_degrees - $ExpectedTilt) -gt 1.0) {
            throw "Pose log contains tilt $($row.tilt_degrees), expected $ExpectedTilt degrees."
        }
        $angleError = Get-HomeDelta ([double]$row.angle_degrees - ($baseAngle + 30.0 * $index))
        if ([math]::Abs($angleError) -gt 1.0) {
            throw "Pose $index is $([double]$row.angle_degrees) degrees; expected a 30-degree step from $baseAngle."
        }
        foreach ($extension in @('.raw', '.ply', '.json')) {
            $capturePath = $row.command + $extension
            if (-not (Test-Path -LiteralPath $capturePath -PathType Leaf)) { throw "Capture output is missing: $capturePath" }
        }
    }
    return $rows
}

$completed = $false
Save-Manifest
try {
    $initial = Read-TableState
    if ([math]::Abs((Get-HomeDelta $initial.rotation)) -gt 1.0 -or [math]::Abs($initial.tilt) -gt 0.5) {
        Write-Host 'Returning both axes to home before the zero-tilt pass.'
        Invoke-Controller -Arguments @('zero') | Out-Null
        Confirm-Home 'Initial homing'
    }

    foreach ($pass in $tiltPasses) {
        $tilt = [double]$pass.degrees
        $key = [string]$pass.key
        $prefix = Join-Path $seriesDirectory "revopoint_${runId}_tilt_${key}"
        $poseCsv = "${prefix}_poses.csv"
        if (Test-Path -LiteralPath $poseCsv) {
            $captureRows = @(Get-ValidatedCaptureRows -PoseCsv $poseCsv -ExpectedTilt $tilt)
            $recorded = @($manifest.passes | Where-Object { [double]$_.tilt_degrees -eq $tilt }).Count -gt 0
            if (-not $recorded) {
                $manifest.passes += [ordered]@{
                    tilt_degrees = $tilt
                    pose_log = [IO.Path]::GetFileName($poseCsv)
                    captures = @($captureRows | ForEach-Object {
                        [ordered]@{
                            table_angle_degrees = [double]$_.angle_degrees
                            tilt_degrees = [double]$_.tilt_degrees
                            prefix = [IO.Path]::GetFileName([string]$_.command)
                        }
                    })
                }
                Save-Manifest
            }
            Write-Host "Reusing verified pass at tilt $tilt degrees ($($captureRows.Count) poses)."
            continue
        }

        Write-Host "=== Tilt $tilt degrees: 12 stationary depth captures over a full turn ==="
        $tiltOutput = Invoke-Controller -Arguments @('tilt', $tilt.ToString('0', [Globalization.CultureInfo]::InvariantCulture))
        $tiltMatch = [regex]::Match($tiltOutput, 'tilt after:\s*(-?\d+(?:\.\d+)?)\s*deg')
        if (-not $tiltMatch.Success -or [math]::Abs([double]$tiltMatch.Groups[1].Value - $tilt) -gt 1.0) {
            throw "Turntable did not confirm tilt $tilt degrees."
        }

        Invoke-Controller -Arguments @(
            'capture-sweep', '12', '30',
            $tilt.ToString('0', [Globalization.CultureInfo]::InvariantCulture),
            $poseCsv, $captureExe, $prefix, $calibration,
            $DepthScale.ToString('0.############', [Globalization.CultureInfo]::InvariantCulture), $liveRaw
        ) | Out-Null
        Confirm-Home "Tilt $tilt capture pass" $tilt 1.0
        $captureRows = @(Get-ValidatedCaptureRows -PoseCsv $poseCsv -ExpectedTilt $tilt)
        $manifest.passes += [ordered]@{
            tilt_degrees = $tilt
            pose_log = [IO.Path]::GetFileName($poseCsv)
            captures = @($captureRows | ForEach-Object {
                [ordered]@{
                    table_angle_degrees = [double]$_.angle_degrees
                    tilt_degrees = [double]$_.tilt_degrees
                    prefix = [IO.Path]::GetFileName([string]$_.command)
                }
            })
        }
        Save-Manifest
    }

    Invoke-Controller -Arguments @('zero') | Out-Null
    $final = Read-TableState
    if ([math]::Abs((Get-HomeDelta $final.rotation)) -gt 1.0 -or [math]::Abs($final.tilt) -gt 0.5) {
        throw "Final home position is not verified (rotation=$($final.rotation), tilt=$($final.tilt))."
    }
    $manifest.final_rotation_degrees = $final.rotation
    $manifest.final_tilt_degrees = $final.tilt
    $manifest.status = 'captured_unregistered'
    $completed = $true
    Write-Host "Captured 60 depth views. Registration is not yet performed. Manifest: $manifestPath"
}
catch {
    $manifest.error = $_.Exception.Message
    throw
}
finally {
    if (-not $completed) {
        $manifest.status = 'failed_partial_captures_preserved'
        try { Invoke-Controller -Arguments @('stop') | Out-Null } catch { Write-Warning $_.Exception.Message }
        try {
            Invoke-Controller -Arguments @('zero') | Out-Null
            $state = Read-TableState
            $manifest.final_rotation_degrees = $state.rotation
            $manifest.final_tilt_degrees = $state.tilt
        } catch { Write-Warning "Could not verify table home after the interrupted scan: $($_.Exception.Message)" }
    }
    $manifest.finished_utc = [DateTime]::UtcNow.ToString('o')
    Save-Manifest
}
