param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectPath,

    [double]$DepthScale = 0,

    [string]$CalibrationFile = '',

    [string]$TurntableMarkerMap = '',

    [string]$PythonExecutable = '',

    [string]$RunId = '',

    [ValidateRange(18, 90)]
    [double]$TurntableRotationPeriodSeconds = 18,

    [string]$ScannerDeviceId = '',

    [string]$TurntableAddress = '',

    [string]$TurntableName = 'REVO_DUAL_AXIS_TABLE',

    [string]$TurntableAdvertisementUuid = '0000FFE1-0000-1000-8000-00805F9B34FB',

    [string]$TurntableServiceUuid = '0000FFE0-0000-1000-8000-00805F9B34FB',

    [string]$TurntableCharacteristicUuid = '0000FFE1-0000-1000-8000-00805F9B34FB',

    [switch]$ManifestOnly
)

$ErrorActionPreference = 'Stop'
$toolRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($CalibrationFile)) {
    $CalibrationFile = Join-Path $toolRoot 'calibration\scanners\revopoint-mini-y16\Q.bin'
}
if ([string]::IsNullOrWhiteSpace($TurntableMarkerMap)) {
    $TurntableMarkerMap = Join-Path $toolRoot 'calibration\tables\revopoint-dual-axis-table\framesmall.mkf'
}
if (-not $ManifestOnly -and [string]::IsNullOrWhiteSpace($PythonExecutable)) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) { throw 'Python is required to align captured frames to the table marker map.' }
    $PythonExecutable = $pythonCommand.Source
}
$repoRoot = (Resolve-Path (Join-Path $toolRoot '..\..')).Path
$workspaceRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot 'projects')).TrimEnd([char[]]@('\', '/'))
$ProjectPath = (Resolve-Path -LiteralPath $ProjectPath).Path
$ProjectPath = [IO.Path]::GetFullPath($ProjectPath).TrimEnd([char[]]@('\', '/'))
$projectParent = [IO.Path]::GetDirectoryName($ProjectPath)
if (-not [string]::Equals($projectParent.TrimEnd([char[]]@('\', '/')), $workspaceRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Scan target must be one existing direct CADYX project folder under $workspaceRoot."
}
$projectRecord = Get-Content -LiteralPath (Join-Path $ProjectPath 'project.json') -Raw | ConvertFrom-Json
$projectId = [guid]::Empty
if ($projectRecord.format -ne 'cadyx-project' -or -not [guid]::TryParse([string]$projectRecord.id, [ref]$projectId)) {
    throw "Scan target is not a valid CADYX project folder: $ProjectPath"
}
$captureExe = Join-Path $toolRoot 'build\CaptureY16.exe'
$turntableExe = Join-Path $toolRoot 'build\TurntableController.exe'
$alignmentScript = Join-Path $toolRoot 'align_y16_to_table.py'
$depthScaleConfig = Join-Path $toolRoot 'calibration\scanners\revopoint-mini-y16\depth_scale.json'
if (-not $PSBoundParameters.ContainsKey('DepthScale')) {
    $DepthScale = [double](Get-Content -LiteralPath $depthScaleConfig -Raw | ConvertFrom-Json).depth_scale_mm_per_raw_unit
}
if ([double]::IsNaN($DepthScale) -or [double]::IsInfinity($DepthScale) -or $DepthScale -le 0) {
    throw 'DepthScale must be a positive finite value in millimeters per uint16 unit.'
}

foreach ($path in @($captureExe, $turntableExe, $CalibrationFile, $depthScaleConfig)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required input was not found: $path"
    }
}
if ([string]::IsNullOrWhiteSpace($RunId)) {
    $runId = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
    $runPrefix = "revopoint_$runId"
} elseif ($RunId -match '^revopoint_\d{8}T\d{6}Z$') {
    $runPrefix = $RunId
    $runId = $RunId.Substring('revopoint_'.Length)
} else {
    throw 'RunId must use the CADYX revopoint_YYYYMMDDTHHMMSSZ identifier format.'
}
$projectCalibration = Join-Path $ProjectPath "${runPrefix}_Q.bin"
$projectDepthScale = Join-Path $ProjectPath "${runPrefix}_depth_scale.json"
$projectMarkerMap = Join-Path $ProjectPath "${runPrefix}_table_marker_map.mkf"
if ($ManifestOnly) {
    foreach ($path in @($projectCalibration, $projectDepthScale)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Manifest-only repair requires the existing scan input copy: $path"
        }
    }
} else {
    foreach ($path in @($TurntableMarkerMap, $alignmentScript, $PythonExecutable)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Required table-alignment input was not found: $path"
        }
    }
    if (Test-Path -LiteralPath $projectCalibration) { throw "Calibration copy already exists: $projectCalibration" }
    if (Test-Path -LiteralPath $projectDepthScale) { throw "Depth-scale copy already exists: $projectDepthScale" }
    if (Test-Path -LiteralPath $projectMarkerMap) { throw "Table-map copy already exists: $projectMarkerMap" }
    [IO.File]::Copy($CalibrationFile, $projectCalibration, $false)
    [IO.File]::Copy($TurntableMarkerMap, $projectMarkerMap, $false)
    $selectedDepthScale = [ordered]@{
        depth_scale_mm_per_raw_unit = $DepthScale
        status = 'selected_device_profile'
    }
    [IO.File]::WriteAllText(
        $projectDepthScale,
        ($selectedDepthScale | ConvertTo-Json -Depth 3),
        [Text.UTF8Encoding]::new($false)
    )
}

function Invoke-Turntable {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $output = & $turntableExe @Arguments 2>&1 | Out-String
    $exitCode = $LASTEXITCODE
    $output.TrimEnd() | Write-Host
    if ($exitCode -ne 0) {
        throw "Turntable command failed ($exitCode): $($Arguments -join ' ')"
    }
    return $output
}

function Read-ReportedAngle {
    param([Parameter(Mandatory = $true)][string]$Output, [Parameter(Mandatory = $true)][string]$Label)
    $match = [regex]::Match($Output, [regex]::Escape($Label) + '\s*(-?\d+(?:\.\d+)?)\s*deg')
    if (-not $match.Success) {
        throw "Turntable did not report '$Label'; captured frames remain saved."
    }
    return [double]::Parse($match.Groups[1].Value, [Globalization.CultureInfo]::InvariantCulture)
}

$poseLog = Join-Path $ProjectPath "${runPrefix}_poses.csv"
$prefix = Join-Path $ProjectPath $runPrefix
$liveRaw = Join-Path $workspaceRoot '.revopoint-live.raw'
if ($ManifestOnly) {
    if (-not (Test-Path -LiteralPath $poseLog -PathType Leaf)) {
        throw "Manifest-only repair requires the existing pose log: $poseLog"
    }
    $manifestPath = Join-Path $ProjectPath "${runPrefix}_scan_360.json"
    if (Test-Path -LiteralPath $manifestPath) {
        throw "Manifest already exists; refusing to overwrite: $manifestPath"
    }
    $rows = @(Import-Csv -LiteralPath $poseLog)
    $captures = @($rows | Where-Object { $_.event -eq 'capture_completed' } | Sort-Object { [int]$_.step_index })
    if ($captures.Count -ne 12) {
        throw "Manifest-only repair requires 12 completed stationary captures; found $($captures.Count)."
    }
    $baseline = $rows | Where-Object { $_.event -eq 'baseline' } | Select-Object -First 1
    $finalPose = $rows | Where-Object { $_.event -eq 'target_reached' -and [int]$_.step_index -eq 12 } | Select-Object -Last 1
    if (-not $baseline -or -not $finalPose) {
        throw 'Pose log has no starting angle or final full-turn pose; no manifest was written.'
    }
    $startAngle = [double]::Parse([string]$baseline.angle_degrees, [Globalization.CultureInfo]::InvariantCulture)
    $finalAngle = [double]::Parse([string]$finalPose.angle_degrees, [Globalization.CultureInfo]::InvariantCulture)
    $closureDelta = [math]::IEEERemainder($finalAngle - $startAngle, 360.0)
    if ([math]::Abs($closureDelta) -gt 1.0) {
        throw "Pose log does not return to the starting angle (closure error $closureDelta degrees)."
    }
    foreach ($row in $rows) {
        if ([string]::IsNullOrWhiteSpace([string]$row.tilt_degrees)) { continue }
        $tilt = [double]::Parse([string]$row.tilt_degrees, [Globalization.CultureInfo]::InvariantCulture)
        if ([math]::Abs($tilt) -gt 0.5) {
            throw "Pose log contains nonzero tilt ($tilt degrees); no manifest was written."
        }
    }
    $frames = @()
    $alignmentPath = Join-Path $ProjectPath "${runPrefix}_table_alignment.json"
    try { $alignmentResult = Get-Content -LiteralPath $alignmentPath -Raw | ConvertFrom-Json }
    catch { $alignmentResult = $null }
    foreach ($capture in $captures) {
        $prefixName = [IO.Path]::GetFileName([string]$capture.command)
        foreach ($extension in @('.raw', '.ply', '.json')) {
            if (-not (Test-Path -LiteralPath (Join-Path $ProjectPath ($prefixName + $extension)) -PathType Leaf)) {
                throw "Capture $($capture.step_index) is missing $($prefixName + $extension); no manifest was written."
            }
        }
        $alignedName = "${prefixName}_table_aligned.ply"
        $aligned = Test-Path -LiteralPath (Join-Path $ProjectPath $alignedName) -PathType Leaf
        $frames += [pscustomobject]@{
            index = [int]$capture.step_index
            commanded_angle_degrees = [int]$capture.step_index * 30
            reported_angle_degrees = [double]::Parse([string]$capture.angle_degrees, [Globalization.CultureInfo]::InvariantCulture)
            tilt_degrees = [double]::Parse([string]$capture.tilt_degrees, [Globalization.CultureInfo]::InvariantCulture)
            raw_file = "$prefixName.raw"
            ply_file = "$prefixName.ply"
            coordinate_frame = if ($aligned) { 'table_marker_map_center_xy' } else { 'source_camera' }
            table_aligned_ply_file = if ($aligned) { $alignedName } else { $null }
            metadata_file = "$prefixName.json"
        }
    }
    $manifest = [ordered]@{
        format = 'revopoint-y16-turntable-capture-v2'
        frame_count = $frames.Count
        step_degrees = 30
        table_tilt_degrees = 0
        starting_position_preserved = $true
        starting_table_angle_degrees = $startAngle
        final_table_angle_degrees = $finalAngle
        full_turn_closure_error_degrees = $closureDelta
        projection_method = 'Q_ray_scaled_to_depth'
        depth_scale_mm_per_raw_unit = $DepthScale
        depth_scale_status = 'provisional_for_direct_UVC'
        coordinate_frame = 'per_frame; see frames[].coordinate_frame'
        table_marker_map_file = if (Test-Path -LiteralPath $projectMarkerMap -PathType Leaf) {
            [IO.Path]::GetFileName($projectMarkerMap)
        } else { $null }
        table_alignment_file = if ($alignmentResult) { [IO.Path]::GetFileName($alignmentPath) } else { $null }
        table_alignment_status = if ($alignmentResult) { $alignmentResult.status } else { 'not_performed' }
        calibration_file = [IO.Path]::GetFileName($projectCalibration)
        depth_scale_config = [IO.Path]::GetFileName($projectDepthScale)
        poses_file = [IO.Path]::GetFileName($poseLog)
        frame_registration = if ($alignmentResult) {
            'table markers when four or more reliable correspondences are visible; unresolved frames stay in source coordinates'
        } else { 'not_performed' }
        frames = $frames
    }
    $manifestJson = $manifest | ConvertTo-Json -Depth 6
    [IO.File]::WriteAllText($manifestPath, $manifestJson, [Text.UTF8Encoding]::new($false))
    Write-Host "Rebuilt 360 scan manifest from the verified pose log; the table and scanner were not started."
    return
}
$complete = $false
try {
    Invoke-Turntable -Arguments @(
        'speed', $TurntableRotationPeriodSeconds.ToString('0.###', [Globalization.CultureInfo]::InvariantCulture),
        $TurntableAddress, $TurntableName, $TurntableAdvertisementUuid,
        $TurntableServiceUuid, $TurntableCharacteristicUuid
    ) | Out-Null
    Invoke-Turntable -Arguments @(
        'capture-sweep', '12', '30', '0', $poseLog, $captureExe, $prefix,
        $projectCalibration, $DepthScale.ToString('0.############', [Globalization.CultureInfo]::InvariantCulture), $liveRaw,
        $ScannerDeviceId, $TurntableAddress, $TurntableName, $TurntableAdvertisementUuid,
        $TurntableServiceUuid, $TurntableCharacteristicUuid
    ) | Out-Null

    & $PythonExecutable $alignmentScript --project $ProjectPath --run-id $runPrefix `
        --marker-map $projectMarkerMap --calibration-file $projectCalibration --depth-scale $DepthScale
    if ($LASTEXITCODE -ne 0) {
        throw 'Table-map alignment failed; the original RAW and PLY capture frames were preserved.'
    }

    $alignmentPath = Join-Path $ProjectPath "${runPrefix}_table_alignment.json"
    $alignmentResult = Get-Content -LiteralPath $alignmentPath -Raw | ConvertFrom-Json
    $frames = @()
    for ($index = 0; $index -lt 12; $index++) {
        $prefixName = '{0}_pose_{1:D3}' -f $runPrefix, $index
        if (-not (Test-Path -LiteralPath (Join-Path $ProjectPath "$prefixName.raw") -PathType Leaf)) {
            $prefixName = '{0}_pose_pose_{1:D3}' -f $runPrefix, $index
        }
        $alignedName = '{0}_pose_{1:D3}_table_aligned.ply' -f $runPrefix, $index
        $alignedPath = Join-Path $ProjectPath $alignedName
        $isAligned = Test-Path -LiteralPath $alignedPath -PathType Leaf
        $frames += [pscustomobject]@{
            index = $index
            raw_file = "$prefixName.raw"
            ply_file = "$prefixName.ply"
            coordinate_frame = if ($isAligned) { 'table_marker_map_center_xy' } else { 'source_camera' }
            table_aligned_ply_file = if ($isAligned) { $alignedName } else { $null }
            metadata_file = "$prefixName.json"
        }
    }
    $manifest = [ordered]@{
        format = 'revopoint-y16-turntable-capture-v2'
        frame_count = $frames.Count
        step_degrees = 30
        turntable_rotation_period_seconds = $TurntableRotationPeriodSeconds
        table_tilt_degrees = 0
        starting_position_preserved = $true
        projection_method = 'Q_ray_scaled_to_depth'
        depth_scale_mm_per_raw_unit = $DepthScale
        depth_scale_status = 'provisional_for_direct_UVC'
        coordinate_frame = 'per_frame; see frames[].coordinate_frame'
        table_marker_map_file = [IO.Path]::GetFileName($projectMarkerMap)
        table_alignment_file = "${runPrefix}_table_alignment.json"
        table_alignment_status = $alignmentResult.status
        calibration_file = [IO.Path]::GetFileName($projectCalibration)
        depth_scale_config = [IO.Path]::GetFileName($projectDepthScale)
        poses_file = [IO.Path]::GetFileName($poseLog)
        frame_registration = 'table markers when four or more reliable correspondences are visible; unresolved frames stay in source coordinates'
        frames = $frames
    }
    $manifestPath = Join-Path $ProjectPath "${runPrefix}_scan_360.json"
    $manifestJson = $manifest | ConvertTo-Json -Depth 6
    [IO.File]::WriteAllText($manifestPath, $manifestJson, [Text.UTF8Encoding]::new($false))
    $complete = $true
    Write-Host "Captured 12 Y16 frames in CADYX project $ProjectPath; original table position preserved."
    Write-Host ("Table-marker alignment: {0} of 12 frames; physical scale remains provisional." -f $alignmentResult.aligned_frame_count)
}
finally {
    if (-not $complete) {
        try { Invoke-Turntable -Arguments @('stop') | Out-Null } catch { Write-Warning $_.Exception.Message }
        Write-Warning "Partial scan files, if any, were preserved in CADYX project $ProjectPath; table home was not changed."
    }
}
