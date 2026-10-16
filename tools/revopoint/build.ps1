$ErrorActionPreference = 'Stop'

$toolRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$buildRoot = Join-Path $toolRoot 'build'
$programFilesX86 = [Environment]::GetFolderPath('ProgramFilesX86')
$vswhere = Join-Path $programFilesX86 'Microsoft Visual Studio\Installer\vswhere.exe'
$framework = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
$csc = Join-Path $framework 'csc.exe'
$runtimeWinRt = Join-Path $framework 'System.Runtime.WindowsRuntime.dll'
$runtimeInterop = Join-Path $framework 'System.Runtime.InteropServices.WindowsRuntime.dll'
$unionMetadata = Join-Path $programFilesX86 'Windows Kits\10\UnionMetadata'

foreach ($path in @($vswhere, $csc, $runtimeWinRt, $runtimeInterop, $unionMetadata)) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Required Windows build component was not found: $path"
    }
}

$vsInstall = & $vswhere -latest -products '*' -property installationPath
if (-not $vsInstall) {
    throw 'Visual Studio C++ Build Tools were not found.'
}
$devShell = Join-Path $vsInstall 'Common7\Tools\Launch-VsDevShell.ps1'
if (-not (Test-Path -LiteralPath $devShell)) {
    throw "Visual Studio developer shell was not found: $devShell"
}

$windowsWinmd = Get-ChildItem -LiteralPath $unionMetadata -Directory |
    Where-Object { $_.Name -match '^\d+\.\d+\.\d+\.\d+$' } |
    Sort-Object { [version]$_.Name } -Descending |
    ForEach-Object { Join-Path $_.FullName 'Windows.winmd' } |
    Where-Object { Test-Path -LiteralPath $_ } |
    Select-Object -First 1
if (-not $windowsWinmd) {
    throw "Windows.winmd was not found under $unionMetadata"
}

New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null
& $devShell -Arch amd64 -HostArch amd64 -SkipAutomaticLocation
if (-not (Get-Command cl.exe -ErrorAction SilentlyContinue)) {
    throw 'cl.exe was not available after loading the Visual Studio developer environment.'
}

$captureSource = Join-Path $toolRoot 'capture_y16.cpp'
$captureObject = Join-Path $buildRoot 'capture_y16.obj'
$captureExe = Join-Path $buildRoot 'CaptureY16.exe'
& cl.exe /nologo /EHsc /std:c++17 /utf-8 "/Fo$captureObject" "/Fe$captureExe" $captureSource `
    /link mf.lib mfreadwrite.lib mfplat.lib mfuuid.lib ole32.lib
if ($LASTEXITCODE -ne 0) {
    throw "CaptureY16 compilation failed with exit code $LASTEXITCODE"
}

$rgbSource = Join-Path $toolRoot 'capture_rgb.cpp'
$rgbObject = Join-Path $buildRoot 'capture_rgb.obj'
$rgbExe = Join-Path $buildRoot 'CaptureRgb.exe'
& cl.exe /nologo /EHsc /std:c++17 /utf-8 "/Fo$rgbObject" "/Fe$rgbExe" $rgbSource `
    /link mf.lib mfreadwrite.lib mfplat.lib mfuuid.lib ole32.lib
if ($LASTEXITCODE -ne 0) {
    throw "CaptureRgb compilation failed with exit code $LASTEXITCODE"
}

$turntableSource = Join-Path $toolRoot 'turntable.cs'
$turntableExe = Join-Path $buildRoot 'TurntableController.exe'
& $csc /nologo /platform:x64 "/out:$turntableExe" "/reference:$windowsWinmd" `
    '/reference:System.Runtime.dll' "/reference:$runtimeWinRt" "/reference:$runtimeInterop" $turntableSource
if ($LASTEXITCODE -ne 0) {
    throw "TurntableController compilation failed with exit code $LASTEXITCODE"
}

Write-Output "Built: $captureExe"
Write-Output "Built: $rgbExe"
Write-Output "Built: $turntableExe"
