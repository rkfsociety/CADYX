param(
    [string]$ProjectPath = (Join-Path (Split-Path -Parent $PSScriptRoot) '..\projects\Kobra S1 Back Cover'),
    [ValidateScript({ $_ -gt 0 })]
    [double]$DisparityScale = 256,
    [string]$RunId = ([DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ'))
)

$ErrorActionPreference = 'Stop'
$toolRoot = $PSScriptRoot
$repoRoot = (Resolve-Path (Join-Path $toolRoot '..\..')).Path
$captureExe = Join-Path $toolRoot 'build\CaptureY16.exe'
$turntableExe = Join-Path $toolRoot 'build\TurntableController.exe'
$calibration = Join-Path $toolRoot 'calibration\Q.bin'
$liveRaw = Join-Path $repoRoot 'projects\.revopoint-live.raw'
$ProjectPath = (Resolve-Path -LiteralPath $ProjectPath).Path
$projectRecord = Get-Content -LiteralPath (Join-Path $ProjectPath 'project.json') -Raw | ConvertFrom-Json

if ($projectRecord.name -ne 'Kobra S1 Back Cover') {
    throw "Scan target must be the existing Kobra S1 Back Cover project; found '$($projectRecord.name)'."
}
foreach ($path in @($captureExe, $turntableExe, $calibration)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required scanner input is missing: $path" }
}
$busy = Get-Process -ErrorAction SilentlyContinue | Where-Object {
    $_.ProcessName -match '^(CaptureY16|CaptureRgb|TurntableController|RevoScan|RevoAssistant)$'
}
if ($busy) { throw "Scanner/table software is already using the hardware: $($busy.ProcessName -join ', ')" }

$runId = $RunId
if ($runId -notmatch '^[0-9]{8}T[0-9]{6}Z$') { throw 'RunId must use the UTC form yyyyMMddTHHmmssZ.' }
$manifestPath = Join-Path $ProjectPath "revopoint_scan_$runId.json"
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
        [double]$manifest.disparity_scale -ne $DisparityScale) {
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
        disparity_scale = $DisparityScale
        disparity_scale_status = 'provisional_unverified'
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

function Confirm-Home([string]$Phase, [double]$ExpectedTilt = 0) {
    $state = Read-TableState
    if ([math]::Abs((Get-HomeDelta $state.rotation)) -gt 1.0 -or [math]::Abs($state.tilt - $ExpectedTilt) -gt 0.5) {
        throw "$Phase did not finish at the home position (rotation=$($state.rotation), tilt=$($state.tilt))."
    }
}

function Get-ValidatedCaptureRows([string]$PoseCsv, [double]$ExpectedTilt) {
    $rows = @(Import-Csv -LiteralPath $PoseCsv | Where-Object { $_.event -eq 'capture_completed' })
    if ($rows.Count -ne 12) { throw "Pose log $PoseCsv contains $($rows.Count) complete captures instead of 12." }
    $baseAngle = [double]$rows[0].angle_degrees
    for ($index = 0; $index -lt $rows.Count; $index++) {
        $row = $rows[$index]
        if ([math]::Abs([double]$row.tilt_degrees - $ExpectedTilt) -gt 0.5) {
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
        $prefix = Join-Path $ProjectPath "revopoint_${runId}_tilt_${key}"
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
        if (-not $tiltMatch.Success -or [math]::Abs([double]$tiltMatch.Groups[1].Value - $tilt) -gt 0.5) {
            throw "Turntable did not confirm tilt $tilt degrees."
        }

        Invoke-Controller -Arguments @(
            'capture-sweep', '12', '30',
            $tilt.ToString('0', [Globalization.CultureInfo]::InvariantCulture),
            $poseCsv, $captureExe, $prefix, $calibration,
            $DisparityScale.ToString('0.############', [Globalization.CultureInfo]::InvariantCulture), $liveRaw
        ) | Out-Null
        Confirm-Home "Tilt $tilt capture pass" $tilt
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
