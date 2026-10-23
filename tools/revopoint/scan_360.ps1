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

    [ValidateRange(1, 30)]
    [int]$StepDegrees = 30,

    [switch]$SkipTableMarkerAlignment,

    [string]$ScannerDeviceId = '',

    [string]$TurntableAddress = '',

    [string]$TurntableName = 'REVO_DUAL_AXIS_TABLE',

    [string]$TurntableAdvertisementUuid = '0000FFE1-0000-1000-8000-00805F9B34FB',

    [string]$TurntableServiceUuid = '0000FFE0-0000-1000-8000-00805F9B34FB',

    [string]$TurntableCharacteristicUuid = '0000FFE1-0000-1000-8000-00805F9B34FB',

    [switch]$ManifestOnly
)

$ErrorActionPreference = 'Stop'
if (360 % $StepDegrees -ne 0) { throw 'StepDegrees must divide a full 360-degree revolution.' }
$frameCount = [int](360 / $StepDegrees)
$toolRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($CalibrationFile)) {
    $CalibrationFile = Join-Path $toolRoot 'calibration\scanners\revopoint-mini-y16\Q.bin'
}
if ([string]::IsNullOrWhiteSpace($TurntableMarkerMap)) {
    $TurntableMarkerMap = Join-Path $toolRoot 'calibration\tables\revopoint-dual-axis-table\framesmall.mkf'
}
if (-not $ManifestOnly -and [string]::IsNullOrWhiteSpace($PythonExecutable)) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) { throw 'Python is required to clean captured frames and optionally align them to the table marker map.' }
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
$framePreprocessor = Join-Path $toolRoot 'preprocess_scan_frame.py'
$alignmentScript = Join-Path $toolRoot 'align_y16_to_table.py'
$depthScaleConfig = Join-Path $toolRoot 'calibration\scanners\revopoint-mini-y16\depth_scale.json'
if (-not $PSBoundParameters.ContainsKey('DepthScale')) {
    $DepthScale = [double](Get-Content -LiteralPath $depthScaleConfig -Raw | ConvertFrom-Json).depth_scale_mm_per_raw_unit
}
if ([double]::IsNaN($DepthScale) -or [double]::IsInfinity($DepthScale) -or $DepthScale -le 0) {
    throw 'DepthScale must be a positive finite value in millimeters per uint16 unit.'
}

foreach ($path in @($captureExe, $turntableExe, $framePreprocessor, $CalibrationFile, $depthScaleConfig)) {
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
$runDirectory = Join-Path $ProjectPath $runPrefix
if (-not $ManifestOnly) {
    if (Test-Path -LiteralPath $runDirectory) {
        $expectedLog = ".cadyx-scan360-$runId.log"
        $existingEntries = @(Get-ChildItem -LiteralPath $runDirectory -Force)
        if ($existingEntries.Count -gt 1 -or
            ($existingEntries.Count -eq 1 -and $existingEntries[0].Name -ne $expectedLog)) {
            throw "Scan series folder already contains data; refusing to overwrite: $runDirectory"
        }
    }
} elseif (-not (Test-Path -LiteralPath $runDirectory -PathType Container)) {
    # Keep manifest repair compatible with scans captured before per-series folders existed.
    $runDirectory = $ProjectPath
}
$projectCalibration = Join-Path $runDirectory "${runPrefix}_Q.bin"
$projectDepthScale = Join-Path $runDirectory "${runPrefix}_depth_scale.json"
$projectMarkerMap = Join-Path $runDirectory "${runPrefix}_table_marker_map.mkf"
if ($ManifestOnly) {
    foreach ($path in @($projectCalibration, $projectDepthScale)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Manifest-only repair requires the existing scan input copy: $path"
        }
    }
} else {
    $alignmentInputs = if ($SkipTableMarkerAlignment) { @() } else { @($TurntableMarkerMap, $alignmentScript, $PythonExecutable) }
    foreach ($path in $alignmentInputs) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Required table-alignment input was not found: $path"
        }
    }
    if (Test-Path -LiteralPath $projectCalibration) { throw "Calibration copy already exists: $projectCalibration" }
    if (Test-Path -LiteralPath $projectDepthScale) { throw "Depth-scale copy already exists: $projectDepthScale" }
    if (-not $SkipTableMarkerAlignment -and (Test-Path -LiteralPath $projectMarkerMap)) { throw "Table-map copy already exists: $projectMarkerMap" }
    if (-not (Test-Path -LiteralPath $runDirectory -PathType Container)) {
        New-Item -ItemType Directory -Path $runDirectory | Out-Null
    }
    [IO.File]::Copy($CalibrationFile, $projectCalibration, $false)
    if (-not $SkipTableMarkerAlignment) { [IO.File]::Copy($TurntableMarkerMap, $projectMarkerMap, $false) }
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

$poseLog = Join-Path $runDirectory "${runPrefix}_poses.csv"
$prefix = Join-Path $runDirectory $runPrefix
$liveRaw = Join-Path $workspaceRoot '.revopoint-live.raw'
if ($ManifestOnly) {
    if (-not (Test-Path -LiteralPath $poseLog -PathType Leaf)) {
        throw "Manifest-only repair requires the existing pose log: $poseLog"
    }
    $manifestPath = Join-Path $runDirectory "${runPrefix}_scan_360.json"
    if (Test-Path -LiteralPath $manifestPath) {
        throw "Manifest already exists; refusing to overwrite: $manifestPath"
    }
    $rows = @(Import-Csv -LiteralPath $poseLog)
    $captures = @($rows | Where-Object { $_.event -eq 'capture_completed' } | Sort-Object { [int]$_.step_index })
    if ($captures.Count -ne $frameCount) {
        throw "Manifest-only repair requires $frameCount completed stationary captures; found $($captures.Count)."
    }
    $baseline = $rows | Where-Object { $_.event -eq 'baseline' } | Select-Object -First 1
    $finalPose = $rows | Where-Object { $_.event -eq 'target_reached' -and [int]$_.step_index -eq $frameCount } | Select-Object -Last 1
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
    $alignmentPath = Join-Path $runDirectory "${runPrefix}_table_alignment.json"
    try { $alignmentResult = Get-Content -LiteralPath $alignmentPath -Raw | ConvertFrom-Json }
    catch { $alignmentResult = $null }
    foreach ($capture in $captures) {
        $prefixName = [IO.Path]::GetFileName([string]$capture.command)
        foreach ($extension in @('.raw', '.ply', '.json')) {
            if (-not (Test-Path -LiteralPath (Join-Path $runDirectory ($prefixName + $extension)) -PathType Leaf)) {
                throw "Capture $($capture.step_index) is missing $($prefixName + $extension); no manifest was written."
            }
        }
        $alignedName = "${prefixName}_table_aligned.ply"
        $aligned = Test-Path -LiteralPath (Join-Path $runDirectory $alignedName) -PathType Leaf
        $frames += [pscustomobject]@{
            index = [int]$capture.step_index
            commanded_angle_degrees = [int]$capture.step_index * $StepDegrees
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
        step_degrees = $StepDegrees
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
    $preprocessingReportPath = Join-Path $runDirectory "${runPrefix}_preprocessing.json"
    $workerControlDirectory = Join-Path $repoRoot '.runtime\scan-reconstruction'
    New-Item -ItemType Directory -Force -Path $workerControlDirectory | Out-Null
    $workerStopPath = Join-Path $workerControlDirectory "${runPrefix}_preprocessing.stop"
    $workerStdout = Join-Path $workerControlDirectory "${runPrefix}_preprocessing.stdout.log"
    $workerStderr = Join-Path $workerControlDirectory "${runPrefix}_preprocessing.stderr.log"
    foreach ($path in @($preprocessingReportPath, $workerStopPath, $workerStdout, $workerStderr)) {
        if (Test-Path -LiteralPath $path) { throw "Per-frame processing output already exists; refusing to overwrite: $path" }
    }
    $workerArguments = @(
        $framePreprocessor, '--watch-pose-log', $poseLog,
        '--run-directory', $runDirectory, '--run-id', $runPrefix,
        '--expected-frame-count', [string]$frameCount,
        '--stop-signal', $workerStopPath, '--report-file', $preprocessingReportPath,
        '--max-workers', '2'
    ) | ForEach-Object { '"' + ([string]$_).Replace('"', '\"') + '"' }
    $preprocessingWorker = Start-Process -FilePath $PythonExecutable -ArgumentList ($workerArguments -join ' ') `
        -WorkingDirectory $repoRoot -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $workerStdout -RedirectStandardError $workerStderr
    try {
        Invoke-Turntable -Arguments @(
            'capture-sweep', [string]$frameCount, [string]$StepDegrees, '0', $poseLog, $captureExe, $prefix,
            $projectCalibration, $DepthScale.ToString('0.############', [Globalization.CultureInfo]::InvariantCulture), $liveRaw,
            $ScannerDeviceId, $TurntableAddress, $TurntableName, $TurntableAdvertisementUuid,
            $TurntableServiceUuid, $TurntableCharacteristicUuid
        ) | Out-Null
    }
    finally {
        [IO.File]::WriteAllText($workerStopPath, 'stop', [Text.UTF8Encoding]::new($false))
        $preprocessingWorker.WaitForExit()
        Remove-Item -LiteralPath $workerStopPath -Force -ErrorAction SilentlyContinue
    }
    if ($preprocessingWorker.ExitCode -ne 0) {
        $workerError = if (Test-Path -LiteralPath $workerStderr) { Get-Content -LiteralPath $workerStderr -Raw } else { '' }
        if (Test-Path -LiteralPath $preprocessingReportPath) {
            try {
                $failedFrames = (Get-Content -LiteralPath $preprocessingReportPath -Raw | ConvertFrom-Json).failed_frames
                $failureDetails = @($failedFrames | ForEach-Object { "frame $($_.frame_index): $($_.error)" }) -join '; '
                if (-not [string]::IsNullOrWhiteSpace($failureDetails)) { $workerError = $failureDetails }
            } catch { $workerError = "Could not read per-frame cleanup details: $($_.Exception.Message)" }
        }
        throw "Per-frame cleanup failed; original captured RAW/PLY files are preserved. $workerError"
    }
    $preprocessingResult = Get-Content -LiteralPath $preprocessingReportPath -Raw | ConvertFrom-Json
    if ($preprocessingResult.state -ne 'completed' -or $preprocessingResult.cleaned_frame_count -ne $frameCount) {
        throw "Per-frame cleanup completed for $($preprocessingResult.cleaned_frame_count) of $frameCount frames; no stitched result was produced."
    }
    if (-not $SkipTableMarkerAlignment) {
        & $PythonExecutable $alignmentScript --project $runDirectory --run-id $runPrefix `
            --marker-map $projectMarkerMap --calibration-file $projectCalibration --depth-scale $DepthScale `
            --frame-count $frameCount
        if ($LASTEXITCODE -ne 0) {
            throw 'Table-map alignment failed; the original RAW and PLY capture frames were preserved.'
        }
        $alignmentPath = Join-Path $runDirectory "${runPrefix}_table_alignment.json"
        $alignmentResult = Get-Content -LiteralPath $alignmentPath -Raw | ConvertFrom-Json
    }
    $frames = @()
    for ($index = 0; $index -lt $frameCount; $index++) {
        $prefixName = '{0}_pose_{1:D3}' -f $runPrefix, $index
        if (-not (Test-Path -LiteralPath (Join-Path $runDirectory "$prefixName.raw") -PathType Leaf)) {
            $prefixName = '{0}_pose_pose_{1:D3}' -f $runPrefix, $index
        }
        $alignedName = '{0}_pose_{1:D3}_table_aligned.ply' -f $runPrefix, $index
        $alignedPath = Join-Path $runDirectory $alignedName
        $isAligned = Test-Path -LiteralPath $alignedPath -PathType Leaf
        $frames += [pscustomobject]@{
            index = $index
            raw_file = "$prefixName.raw"
            ply_file = "$prefixName.ply"
            preprocessed_ply_file = "${prefixName}_cleaned.ply"
            preprocessing_report_file = "${prefixName}_cleaned.json"
            coordinate_frame = if ($isAligned) { 'table_marker_map_center_xy' } else { 'source_camera' }
            table_aligned_ply_file = if ($isAligned) { $alignedName } else { $null }
            metadata_file = "$prefixName.json"
        }
    }
    $manifest = [ordered]@{
        format = 'revopoint-y16-turntable-capture-v2'
        frame_count = $frames.Count
        step_degrees = $StepDegrees
        turntable_rotation_period_seconds = $TurntableRotationPeriodSeconds
        table_tilt_degrees = 0
        starting_position_preserved = $true
        projection_method = 'Q_ray_scaled_to_depth'
        depth_scale_mm_per_raw_unit = $DepthScale
        depth_scale_status = 'provisional_for_direct_UVC'
        coordinate_frame = 'per_frame; see frames[].coordinate_frame'
        table_marker_map_file = if ($SkipTableMarkerAlignment) { $null } else { [IO.Path]::GetFileName($projectMarkerMap) }
        table_alignment_file = if ($alignmentResult) { "${runPrefix}_table_alignment.json" } else { $null }
        table_alignment_status = if ($alignmentResult) { $alignmentResult.status } else { 'not_performed' }
        calibration_file = [IO.Path]::GetFileName($projectCalibration)
        depth_scale_config = [IO.Path]::GetFileName($projectDepthScale)
        poses_file = [IO.Path]::GetFileName($poseLog)
        frame_registration = if ($SkipTableMarkerAlignment) { 'not_performed; source camera frames preserved for geometry-only registration' } else { 'table markers when four or more reliable correspondences are visible; unresolved frames stay in source coordinates' }
        preprocessing_report = [IO.Path]::GetFileName($preprocessingReportPath)
        preprocessing_status = $preprocessingResult.state
        frames = $frames
    }
    $manifestPath = Join-Path $runDirectory "${runPrefix}_scan_360.json"
    $manifestJson = $manifest | ConvertTo-Json -Depth 6
    [IO.File]::WriteAllText($manifestPath, $manifestJson, [Text.UTF8Encoding]::new($false))
    $complete = $true
    Write-Host "Captured and preprocessed $frameCount Y16 frames at $StepDegrees-degree steps in CADYX project $ProjectPath; original table position and source PLYs preserved."
    if ($alignmentResult) { Write-Host ("Table-marker alignment: {0} of {1} frames; physical scale remains provisional." -f $alignmentResult.aligned_frame_count, $frameCount) }
}
finally {
    if (-not $complete) {
        try { Invoke-Turntable -Arguments @('stop') | Out-Null } catch { Write-Warning $_.Exception.Message }
        Write-Warning "Partial scan files, if any, were preserved in CADYX project $ProjectPath; table home was not changed."
    }
}
