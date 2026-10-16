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

For a continuous capture-rate measurement, add `--frames 120`. The camera stays open for the sequence; the utility saves the packed frames to `frame_burst.raw`, records Media Foundation timestamps, elapsed stream time and UTC arrival time in `frame_timestamps.csv`, and reports both timestamp-derived and wall-clock rates. The PLY remains a preview of the first frame. On the connected device, a 120-frame run produced about 4.53 fps by media timestamps; this is a measured result for the selected 640×400 Y16 UVC mode, not the vendor's complete scan pipeline.

To show the live depth image in CADYX, start a capture with `--live-raw` pointing at the CADYX workspace root's `.revopoint-live.raw`, then open `/scanner` in the CADYX web app. The capture updates this fixed-size file atomically after each received frame; the authenticated page polls the corresponding read-only API route and draws a depth grayscale preview. Example when the workspace root is the repository's `projects` folder:

```powershell
& .\tools\revopoint\build\CaptureY16.exe --out-prefix (Join-Path $PWD 'projects\live_preview\capture') --q-file (Join-Path $env:LOCALAPPDATA 'RevoScan5\cameraparam\Q.bin') --disparity-scale 256 --frames 1000 --fps 15 --live-raw (Join-Path $PWD 'projects\.revopoint-live.raw')
```

## Live capture with turntable movement

The BLE controller can rotate while the UVC reader is active. A verified 90-frame run overlapped a 30° move: the move completed in 2.27 s, the capture acquired 90 frames at 4.54 fps by Media Foundation timestamps, and the table was homed to 0° afterward. The open browser panel reported about 3 fps during the run, showing additional polling/rendering overhead. Registration, scale validation, and full-scan fusion remain outstanding.

For a timestamped angle trace with an in-session turn command, use:

```powershell
& .\tools\revopoint\build\TurntableController.exe monitor 24 (Join-Path $PWD 'projects\angles.csv') 3 30
```

The arguments are monitor duration, CSV path, delay before the relative turn, and turn angle. The command keeps a single BLE connection, records UTC angle samples, and sends the turn itself on that connection. In a 120-frame live capture the controller recorded 88 angle samples, observed the table move from `-360°` (equivalent to 0°) through `-345.5°` to `-330°` (30°), and then remain there. The table was homed to 0° after capture.

The frame UTC timestamps and angle samples can be joined by linear interpolation. Verified synchronized run: 120 captured frames all received an interpolated table pose spanning 0°–30°; derived per-frame rows are saved as `capture_frame_poses.csv`. This is suitable for per-frame rigid rotation estimates when the turntable axis and pivot are calibrated. The Q-matrix disparity scale and turntable axis/pivot remain unvalidated, so the pose table alone does not establish an accurate fused full model.

## Experimental 360° fusion result

The twelve 30° raw captures were reprojected and tested with a candidate Z-axis rotation and overlap-fitted pivot. A candidate transform of −30° per commanded +30° step around approximately `[28.8, -5.4]` in current Q coordinates gave 81–94% adjacent-pair nearest-neighbor overlap within 5 coordinate units. The pivot and sign are empirical estimates, not mechanical calibration. A voxel-deduplicated PLY and its JSON provenance are saved as `approx_fused_candidate.ply` and `approx_fused_candidate.json` beside the source frames.

CADYX reconstructed the candidate to 24,641 vertices and 49,979 triangles. Visual inspection shows an overly broad, nearly planar Poisson sheet with a noisy dense center; this mesh is a diagnostic artifact and is not a verified complete model. The raw disparity scale, mechanical axis/pivot, object support/removal and reconstruction quality need further work before the fusion can be accepted.

List the camera's advertised native modes with:

```powershell
& .\tools\revopoint\build\CaptureY16.exe --list-modes
```

The connected camera advertises Y16 at 640×400, 1280×800, and 640×800, each with nominal 5, 10, and 15 fps modes. Explicit 640×400 selections at nominal 5, 10, and 15 fps each delivered about 4.6 fps by Media Foundation timestamps in 40-frame runs. Other resolutions remain unmeasured. The direct UVC path therefore does not meet the 10 fps target in the tested mode; this does not measure Revo Scan's complete proprietary capture pipeline.

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
