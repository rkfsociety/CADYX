# Revopoint tools for Windows

These small command-line tools exercise the scanner and dual-axis turntable through Windows APIs. They do not use the paid Revopoint SDK.

## Build

Run from the repository root in PowerShell:

```powershell
.\tools\revopoint\build.ps1
```

The build uses the installed Visual Studio C++ Build Tools, Windows SDK, and .NET Framework C# compiler. It writes executables under `tools/revopoint/build/`, which is ignored by Git.

## Capture one depth frame

Close Revo Scan before capturing so it does not hold the depth camera. The Q matrix must come from the same scanner calibration. Revo Scan 5 stores it at:

```powershell
$qFile = Join-Path $env:LOCALAPPDATA 'RevoScan5\cameraparam\Q.bin'
$outPrefix = Join-Path $PWD 'projects\revopoint_probe\frame'
& .\tools\revopoint\build\CaptureY16.exe `
    --out-prefix $outPrefix `
    --q-file $qFile `
    --disparity-scale 256
```

The command writes `frame.raw`, `frame.ply`, and `frame.json` side by side. It refuses to overwrite any of them. The raw file contains one 640×400 little-endian Y16 frame. The PLY is a single-frame point cloud reprojected through `Q.bin`.

The raw-to-disparity scale `256` is a provisional value from the first live capture. It has not been validated against a known object dimension, so treat the PLY scale as unverified. The utility captures one camera pose; it does not track motion, align multiple frames, fuse a full scan, or create a watertight mesh. The scanner's exact product model is not established by its generic USB camera names.

Keep each capture prefix unique. The original Y16 frame is preserved beside the derived PLY so later calibration work can use the same input.

## Query and move the dual-axis turntable

```powershell
& .\tools\revopoint\build\TurntableController.exe query
& .\tools\revopoint\build\TurntableController.exe zero
& .\tools\revopoint\build\TurntableController.exe speed 18
& .\tools\revopoint\build\TurntableController.exe rotate 5
& .\tools\revopoint\build\TurntableController.exe stop
```

`query` reads the current rotation angle and reports raw tilt/speed responses. `speed` sets the requested period in seconds per revolution from 18 to 90; smaller values rotate faster. `rotate` sends one relative move and is limited to 30° per command; use a negative angle to reverse it. The controller waits for the requested angle and reports movement time. `zero` returns to the turntable's boot-time home. `stop` sends stop commands to both axes. The tool scans for the BLE FFE1 service, connects without pairing, confirms the GATT name `REVO_DUAL_AXIS_TABLE`, and uses the FFE0/FFE1 service. The observed ASCII commands match the independently documented Revopoint Dual-axis Turntable protocol: [protocol research](https://github.com/opensourcemanufacturing/Revopoint-Dual-Turntable).

Revo Scan, Revo Assistant, or another Bluetooth client can hold the table connection. The controller does not retry a movement command after a lost response.

## Capture a full turn at 30° intervals

Secure the object to the turntable, keep the scanner fixed, close Revo Scan, and run:

```powershell
.\tools\revopoint\scan_360.ps1 -OutputFolderName revopoint_scan_20261016 -DisparityScale 256
```

The script returns the table to its home zero, captures at 0°, 30°, …, 330°, and homes the table again. It saves every raw frame, per-frame provisional PLY, calibration copy, and angle manifest in a new direct workspace folder. It does **not** register the twelve views or merge them into one mesh. The table center/axis and disparity scale still need validation before the views can form a trustworthy complete model.
