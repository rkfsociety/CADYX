param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_-]+$')]
    [string]$OutputFolderName,

    [Parameter(Mandatory = $true)]
    [ValidateScript({ $_ -gt 0 })]
    [double]$DisparityScale,

    [string]$CalibrationFile = (Join-Path $env:LOCALAPPDATA 'RevoScan5\cameraparam\Q.bin')
)

$ErrorActionPreference = 'Stop'
$toolRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $toolRoot '..\..')
$workspaceRoot = Join-Path $repoRoot 'projects'
$outputDirectory = Join-Path $workspaceRoot $OutputFolderName
$captureExe = Join-Path $toolRoot 'build\CaptureY16.exe'
$turntableExe = Join-Path $toolRoot 'build\TurntableController.exe'

foreach ($path in @($captureExe, $turntableExe, $CalibrationFile)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required input was not found: $path"
    }
}
if (Test-Path -LiteralPath $outputDirectory) {
    throw "Output directory already exists; choose a new name: $outputDirectory"
}

New-Item -ItemType Directory -Path $outputDirectory | Out-Null
Copy-Item -LiteralPath $CalibrationFile -Destination (Join-Path $outputDirectory 'Q.bin')

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

function Invoke-Capture {
    param([Parameter(Mandatory = $true)][string]$Prefix)
    & $captureExe --out-prefix $Prefix --q-file (Join-Path $outputDirectory 'Q.bin') `
        --disparity-scale $DisparityScale
    if ($LASTEXITCODE -ne 0) {
        throw "Depth frame capture failed ($LASTEXITCODE) at prefix $Prefix"
    }
}

$frames = @()
$complete = $false
try {
    $zeroOutput = Invoke-Turntable -Arguments @('zero')
    $zeroAngle = Read-ReportedAngle -Output $zeroOutput -Label 'angle after zero:'
    if ([math]::Abs($zeroAngle) -gt 0.5) {
        throw "Turntable did not reach zero (reported $zeroAngle degrees)."
    }

    for ($index = 0; $index -lt 12; $index++) {
        $expectedAngle = $index * 30
        if ($index -gt 0) {
            $rotationOutput = Invoke-Turntable -Arguments @('rotate', '30')
            $actualAngle = Read-ReportedAngle -Output $rotationOutput -Label 'angle after:'
        } else {
            $actualAngle = $zeroAngle
        }
        $prefixName = 'frame_{0:D3}' -f $expectedAngle
        $prefix = Join-Path $outputDirectory $prefixName
        Write-Host "Capturing frame $($index + 1)/12 at table angle $actualAngle degrees."
        Invoke-Capture -Prefix $prefix
        $frames += [pscustomobject]@{
            index = $index
            commanded_angle_degrees = $expectedAngle
            reported_angle_degrees = $actualAngle
            raw_file = "$prefixName.raw"
            ply_file = "$prefixName.ply"
            metadata_file = "$prefixName.json"
        }
    }

    $homeOutput = Invoke-Turntable -Arguments @('zero')
    $finalAngle = Read-ReportedAngle -Output $homeOutput -Label 'angle after zero:'
    if ([math]::Abs($finalAngle) -gt 0.5) {
        throw "Scan frames were captured, but the table did not return to zero (reported $finalAngle degrees)."
    }

    $manifest = [ordered]@{
        format = 'revopoint-y16-turntable-capture-v1'
        frame_count = $frames.Count
        step_degrees = 30
        disparity_scale = $DisparityScale
        disparity_scale_status = 'provisional_unverified'
        calibration_file = 'Q.bin'
        frame_registration = 'not_performed'
        final_table_angle_degrees = $finalAngle
        frames = $frames
    }
    $manifestPath = Join-Path $outputDirectory 'scan_360.json'
    $manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $manifestPath -Encoding utf8NoBOM
    $complete = $true
    Write-Host "Captured 12 Y16 frames under $outputDirectory"
    Write-Host 'The frames are not merged or registered; the scan manifest records the table angles.'
}
finally {
    if (-not $complete) {
        try { Invoke-Turntable -Arguments @('stop') | Out-Null } catch { Write-Warning $_.Exception.Message }
        try { Invoke-Turntable -Arguments @('zero') | Out-Null } catch { Write-Warning $_.Exception.Message }
        Write-Warning "Partial scan files, if any, were preserved in $outputDirectory"
    }
}
