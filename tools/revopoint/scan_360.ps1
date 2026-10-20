param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectPath,

    [double]$DepthScale = 0,

    [string]$CalibrationFile = ''
)

$ErrorActionPreference = 'Stop'
$toolRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($CalibrationFile)) {
    $CalibrationFile = Join-Path $toolRoot 'calibration\Q.bin'
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
$depthScaleConfig = Join-Path $toolRoot 'calibration\depth_scale.json'
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
$runId = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
$runPrefix = "revopoint_$runId"
$projectCalibration = Join-Path $ProjectPath "${runPrefix}_Q.bin"
$projectDepthScale = Join-Path $ProjectPath "${runPrefix}_depth_scale.json"
if (Test-Path -LiteralPath $projectCalibration) { throw "Calibration copy already exists: $projectCalibration" }
if (Test-Path -LiteralPath $projectDepthScale) { throw "Depth-scale copy already exists: $projectDepthScale" }
Copy-Item -LiteralPath $CalibrationFile -Destination $projectCalibration
Copy-Item -LiteralPath $depthScaleConfig -Destination $projectDepthScale

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
$prefix = Join-Path $ProjectPath "${runPrefix}_pose"
$liveRaw = Join-Path $workspaceRoot '.revopoint-live.raw'
$complete = $false
try {
    Invoke-Turntable -Arguments @(
        'capture-sweep', '12', '30', '0', $poseLog, $captureExe, $prefix,
        $projectCalibration, $DepthScale.ToString('0.############', [Globalization.CultureInfo]::InvariantCulture), $liveRaw
    ) | Out-Null

    $frames = @()
    for ($index = 0; $index -lt 12; $index++) {
        $prefixName = '{0}_pose_{1:D3}' -f $runPrefix, $index
        $frames += [pscustomobject]@{
            index = $index
            raw_file = "$prefixName.raw"
            ply_file = "$prefixName.ply"
            metadata_file = "$prefixName.json"
        }
    }
    $manifest = [ordered]@{
        format = 'revopoint-y16-turntable-capture-v2'
        frame_count = $frames.Count
        step_degrees = 30
        table_tilt_degrees = 0
        starting_position_preserved = $true
        projection_method = 'Q_ray_scaled_to_depth'
        depth_scale_mm_per_raw_unit = $DepthScale
        depth_scale_status = 'provisional_for_direct_UVC'
        calibration_file = [IO.Path]::GetFileName($projectCalibration)
        depth_scale_config = [IO.Path]::GetFileName($projectDepthScale)
        poses_file = [IO.Path]::GetFileName($poseLog)
        frame_registration = 'not_performed'
        frames = $frames
    }
    $manifestPath = Join-Path $ProjectPath "${runPrefix}_scan_360.json"
    $manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $manifestPath -Encoding utf8NoBOM
    $complete = $true
    Write-Host "Captured 12 Y16 frames in CADYX project $ProjectPath; original table position preserved."
    Write-Host 'Live preview follows the latest captured frame. Registration and fusion are not performed during capture.'
}
finally {
    if (-not $complete) {
        try { Invoke-Turntable -Arguments @('stop') | Out-Null } catch { Write-Warning $_.Exception.Message }
        Write-Warning "Partial scan files, if any, were preserved in CADYX project $ProjectPath; table home was not changed."
    }
}
