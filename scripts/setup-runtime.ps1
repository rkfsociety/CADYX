#requires -Version 7.0
param([switch]$WithNode)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $projectRoot '.runtime'
$versions = Get-Content (Join-Path $PSScriptRoot 'runtime-versions.json') -Raw | ConvertFrom-Json
New-Item -ItemType Directory -Force (Join-Path $runtimeRoot 'downloads') | Out-Null

function Get-VerifiedArchive($Url, $Path, $Hash) {
    if (!(Test-Path -LiteralPath $Path) -or (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ne $Hash) {
        Invoke-WebRequest -Uri $Url -OutFile $Path
    }
    if ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ne $Hash) {
        throw "SHA256 mismatch: $Path"
    }
}

$pythonArchive = Join-Path $runtimeRoot 'downloads/python.zip'
Get-VerifiedArchive $versions.python.url $pythonArchive $versions.python.sha256
$pythonRoot = Join-Path $runtimeRoot 'python'
if (!(Test-Path (Join-Path $pythonRoot 'python.exe'))) {
    Expand-Archive -LiteralPath $pythonArchive -DestinationPath $pythonRoot
}
$pythonTag = ($versions.python.version.Split('.')[0..1] -join '')
# Use an absolute project import path; rerun setup after moving the checkout.
@("python$pythonTag.zip", '.', $projectRoot) | Set-Content (Join-Path $pythonRoot "python$pythonTag._pth") -Encoding utf8NoBOM
$pythonExe = Join-Path $pythonRoot 'python.exe'
$actualPython = & $pythonExe -c 'import platform; print(platform.python_version())'
if ($LASTEXITCODE -ne 0 -or $actualPython -ne $versions.python.version) { throw 'Python runtime version mismatch.' }

$sdkArchive = Join-Path $runtimeRoot 'downloads/occt.conda'
Get-VerifiedArchive $versions.occt.url $sdkArchive $versions.occt.sha256
$sdkRoot = Join-Path $runtimeRoot 'occt-sdk'
if (!(Test-Path (Join-Path $sdkRoot 'Library/include/opencascade/Standard_Version.hxx'))) {
    & $pythonExe (Join-Path $PSScriptRoot 'extract-sdk.py') $sdkArchive $sdkRoot
    if ($LASTEXITCODE -ne 0) { throw 'OCCT SDK extraction failed.' }
}
$open3dArchive = Join-Path $runtimeRoot "downloads/$($versions.open3d.archive)"
Get-VerifiedArchive $versions.open3d.url $open3dArchive $versions.open3d.sha256
$open3dRoot = Join-Path $runtimeRoot $versions.open3d.directory
if (!(Test-Path (Join-Path $open3dRoot 'CMake/Open3DConfig.cmake'))) {
    Expand-Archive -LiteralPath $open3dArchive -DestinationPath $runtimeRoot -Force
}
if (!(Test-Path (Join-Path $open3dRoot 'CMake/Open3DConfig.cmake'))) {
    throw 'Open3D SDK extraction failed.'
}
& cmake -S (Join-Path $projectRoot 'native') -B (Join-Path $runtimeRoot 'build') `
    "-DOCCT_ROOT=$sdkRoot" "-DOPEN3D_ROOT=$open3dRoot"
if ($LASTEXITCODE -ne 0) { throw 'Native kernel configuration failed.' }
& cmake --build (Join-Path $runtimeRoot 'build') --config Release --parallel 2
if ($LASTEXITCODE -ne 0) { throw 'Native kernel compilation failed.' }
@{
    python_version = $versions.python.version
    occt_version = $versions.occt.version
    open3d_version = $versions.open3d.version
    dll_directories = @('occt-sdk/Library/bin', "$($versions.open3d.directory)/bin")
    bridge = 'build/Release/cadyx_kernel.dll'
    scan_bridge = 'build/Release/cadyx_scan.dll'
} | ConvertTo-Json | Set-Content (Join-Path $runtimeRoot 'manifest.json') -Encoding utf8NoBOM
& $pythonExe -c 'import sys; from cadyx.native_kernel import NativeKernel; from cadyx.native_scan import NativeScan; from cadyx.kernel import OCCTKernelAdapter; assert NativeKernel().version == sys.argv[1], "OCCT version mismatch"; assert NativeScan().version == sys.argv[2], "Open3D version mismatch"; print("OCCT", NativeKernel().version, "Open3D", NativeScan().version); print(OCCTKernelAdapter().create_box(width=2,height=3,depth=5))' $versions.occt.version $versions.open3d.version
if ($LASTEXITCODE -ne 0) { throw 'Native kernel runtime check failed.' }

if ($WithNode) {
    $nodeName = "node-v$($versions.node.version)-win-x64.zip"
    $baseUrl = "https://nodejs.org/dist/v$($versions.node.version)"
    $checksums = (Invoke-WebRequest "$baseUrl/SHASUMS256.txt").Content
    $hash = ($checksums -split "`n" | Where-Object { $_.Trim().EndsWith("  $nodeName") }) -split '\s+' | Select-Object -First 1
    if (!$hash) { throw 'Node archive checksum not found.' }
    $nodeArchive = Join-Path $runtimeRoot "downloads/$nodeName"
    Get-VerifiedArchive "$baseUrl/$nodeName" $nodeArchive $hash
    $nodeRoot = Join-Path $runtimeRoot "node-v$($versions.node.version)-win-x64"
    if (!(Test-Path (Join-Path $nodeRoot 'node.exe'))) {
        Expand-Archive -LiteralPath $nodeArchive -DestinationPath $runtimeRoot
    }
    & (Join-Path $nodeRoot 'node.exe') --version
    if ($LASTEXITCODE -ne 0) { throw 'Node runtime check failed.' }
}
