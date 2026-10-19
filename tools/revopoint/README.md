# Revopoint tools for Windows

These small command-line tools exercise the scanner and dual-axis turntable through Windows APIs. They do not use the paid Revopoint SDK or require Revo Scan to run. The device calibration files used by the tools are bundled in `tools/revopoint/calibration/`; they were copied from `%LOCALAPPDATA%\RevoScan5\cameraparam` on the connected scanner PC so the same hardware setup can be used from another checkout. This calibration bundle is specific to that scanner; it is not a universal MINI calibration.

## Build

Run from the repository root in PowerShell:

```powershell
.\tools\revopoint\build.ps1
```

The build uses the installed Visual Studio C++ Build Tools, Windows SDK, and .NET Framework C# compiler. It writes executables under `tools/revopoint/build/`, which is ignored by Git.

## Capture one depth frame

Close Revo Scan before capturing so it does not hold the depth camera. The Q matrix must come from the same scanner calibration. Revo Scan 5 stores it at:

```powershell
$qFile = Join-Path $PWD 'tools\revopoint\calibration\Q.bin'
$outPrefix = Join-Path $PWD 'projects\revopoint_probe\frame'
& .\tools\revopoint\build\CaptureY16.exe `
    --out-prefix $outPrefix `
    --q-file $qFile `
    --disparity-scale 256
```

The command writes `frame.raw`, `frame.ply`, and `frame.json` side by side. It refuses to overwrite any of them. The raw file contains one 640×400 little-endian Y16 frame. The PLY is a single-frame point cloud reprojected through `Q.bin`.

For a continuous capture-rate measurement, add `--frames 120`. The camera stays open for the sequence; the utility saves the packed frames to `frame_burst.raw`, records Media Foundation timestamps, elapsed stream time and UTC arrival time in `frame_timestamps.csv`, and reports both timestamp-derived and wall-clock rates. The PLY remains a preview of the first frame. An earlier 120-frame run produced about 4.53 fps by media timestamps; the newer paired full-turn run below measured both streams for over 40 seconds and supersedes it for that tested configuration.

To preview a live scan in the current CADYX project, open the project and use the scan button in the 3D viewport toolbar. The embedded preview polls the read-only frame endpoint every 80 ms and offers depth grayscale or 3D points reprojected through Q.bin; drag the point view to orbit it. It shows one current frame and does not accumulate or fuse frames. A saved ASCII point-only PLY can also be shown in the same project panel by adding `scan_folder` and `scan_file` to the CADYX URL together with `live_scan=1`; this displays the stored points directly without Poisson reconstruction. A frame older than one second is marked as stopped. The disparity scale (256) and physical units remain unverified, so this is a geometry preview rather than a calibrated scan. The /scanner route remains available for diagnostics. Start capture with --live-raw pointing at the CADYX workspace root's .revopoint-live.raw; the capture updates this fixed-size file atomically after each received frame. Example when the workspace root is the repository's projects folder:

```powershell
& .\tools\revopoint\build\CaptureY16.exe --out-prefix (Join-Path $PWD 'projects\live_preview\capture') --q-file (Join-Path $PWD 'tools\revopoint\calibration\Q.bin') --disparity-scale 256 --frames 1000 --fps 15 --live-raw (Join-Path $PWD 'projects\.revopoint-live.raw')
```

## Live capture with turntable movement

The BLE controller can rotate while the UVC reader is active. A verified 90-frame run overlapped a 30° move: the move completed in 2.27 s, the capture acquired 90 frames at 4.54 fps by Media Foundation timestamps, and the table was homed to 0° afterward. The open browser panel reported about 3 fps during the run, showing additional polling/rendering overhead. Registration, scale validation, and full-scan fusion remain outstanding.

For a timestamped angle trace with an in-session turn command, use:

```powershell
& .\tools\revopoint\build\TurntableController.exe monitor 24 (Join-Path $PWD 'projects\angles.csv') 3 30
```

The arguments are monitor duration, CSV path, delay before the relative turn, and turn angle. The command keeps a single BLE connection, records UTC rotation and tilt samples, and sends the turn itself on that connection. In a 120-frame live capture the controller recorded 88 angle samples and observed 0°→30°. The table was homed to 0° after capture. Tilt control was tested across the supported range: target +30° reported +29.2°, −30° reported −30.0°, and the zero command restored both axes to 0°. `+QR,TILTVALUE;` reads the current tilt; `+QR,TILTANGLE;` reports the supported range instead.

The frame UTC timestamps and angle samples can be joined by linear interpolation. Verified synchronized run: 120 captured frames all received an interpolated table pose spanning 0°–30°; derived per-frame rows are saved as `capture_frame_poses.csv`. The monitor CSV also records tilt from `+QR,TILTVALUE;`. This is suitable for per-frame rigid pose estimates when the turntable axes/pivot are calibrated. The Q-matrix disparity scale and mechanical axes remain unvalidated, so the pose table alone does not establish an accurate fused full model.

## Continuous synchronized sweep

To record both camera streams while the table completes one revolution, run:

```powershell
$projectPath = Join-Path $PWD 'projects\Исходные кадры сканирования'
.\tools\revopoint\capture_marker_sweep.ps1 -ProjectPath $projectPath -OutputFolderName revopoint_marker_sweep_20261019 -Frames 1000 -Fps 15
```

The script creates a run folder inside the selected CADYX project, checks that the table is at home with zero tilt, starts DepthCam, waits for its first valid Y16 sample, starts RGBCam and waits for its first valid MJPG sample, then starts TurntableController.exe sweep 12 30. That controller holds one BLE connection, verifies each 30° step before issuing the next, records angle and tilt telemetry, and aborts on a missed target. A shared stop-file closes the two finite bursts after the final step so their raw frames and timestamps are saved. The Y16 capture also updates projects/.revopoint-live.raw for the in-project scan preview. The script preserves partial data and attempts to stop and home the table if an operation fails.

To join camera frames to interpolated turntable angles and inspect bright-component trajectories, run:

```powershell
py .\tools\revopoint\analyze_marker_sweep.py .\projects\revopoint_marker_sweep_YYYYMMDD_HHMMSS
```

The analyzer runs the bundled `DetectMarkers.exe` for RGB frames that do not already have a candidate CSV, then writes `frame_pose_sync.csv`, `marker_detections.csv`, `marker_tracks.csv`, `marker_trajectories.csv`, and `sweep_analysis.json` beside the captures. It pairs RGB and Y16 by UTC arrival time within 45 ms by default and interpolates the measured table angle between telemetry samples. The additional trajectory pass filters tabletop components by area, greedily associates nearby detections, and fits each path to a first-order sinusoid of table angle. Its default `marker_trajectories.csv` marks paths with at least 10 observations, 45° angle span, 8 px image extent, and ≤25 px fit RMS as rotating candidates. Thresholded components and fitted tracks remain exploratory; trajectory extraction alone does not confirm reflector identities. The analyzer refuses to overwrite prior analysis files; use `--suffix _2` for a separate analysis pass.

To compare full-turn trajectories with the exported 34-point map without assuming an absolute scan orientation, run:

```powershell
py .\tools\revopoint\register_marker_map.py .\projects\revopoint_marker_sweep_YYYYMMDD_HHMMSS --map .\framesmall_points.csv
```

The standard-library tool projects the map and sinusoidal trajectory coefficients into 2D, proposes long-span anchor matches with orientation/scale/translation-free RANSAC, refines the point-set mapping, then scores remaining smooth tracks without changing that fit. It reports leave-one-out errors for anchors and marks `fit_anchor` in each output row. It writes `marker_map_registration.csv` and `.json` beside the capture and refuses to overwrite them; pass `--suffix _2` to preserve the earlier result when rerunning. All rows are explicitly marked `candidate`: detections use greedy tracks, the map's physical units are unknown, and this internal cross-validation does not independently confirm marker identities or fuse/scale an object scan.

Measured empty-table sweep on 2026-10-16: requested 10 fps; 570 Y16 frames and 567 RGB frames were acquired over about 43.4 seconds. Media Foundation rates were 13.12 fps for Y16 and 13.12 fps for RGB. The table moved 359.9° over 32.0 seconds; 420 frames from each stream fell inside that motion window, and all 420 RGB frames paired to a Y16 frame within 45 ms. Across the capture, 567 RGB/Y16 pairs met that tolerance; the absolute timestamp offset had a 22.4 ms 95th percentile. The two cameras were timestamped from UTC arrival clocks, not a shared hardware clock. Table telemetry provided 73 angle/tilt samples at a median 410 ms interval, so per-frame poses are interpolated estimates. The table finished at −0.2° with zero tilt. A separate 60-frame burst also measured about 13 fps but ended before rotation; it is not counted as motion-overlapped evidence.

A repeat sweep with the scanner raised, captured later on 2026-10-16, acquired 572 Y16 and 568 RGB frames over 43.56 seconds. Arrival rates were 13.108 fps and 13.105 fps; during the 32.12-second motion window, 422 Y16 and 421 RGB frames arrived, with all 421 moving RGB frames paired to Y16 within 45 ms. The measured angle span was 359.9° and the table returned to −0.3° with zero tilt. At threshold 150, the tabletop ROI contained a mean of 15.9 RGB candidates per moving frame and the trajectory pass retained 29 smooth rotating candidates. A per-frame point-pattern trial produced only 4–7 one-to-one matches (median 6 of 9–13 detections), and the fitted rotations did not track table telemetry (correlation −0.074). The aggregate trajectory-registration prototype found 9 anchor correspondences from 10 long-span tracks, then scored one additional shorter-span track at the 35 px threshold. Anchor fit RMS was 7.985 px and anchor leave-one-out RMS was 14.074 px; the extra match had no leave-one-out validation. These are candidate correspondences, not independent identity confirmation. The frame does not contain the tabletop edges, so only a partial set of reflectors is visible at once.

This run demonstrates over 10 fps from both direct UVC streams during a full table revolution on this setup. It does not validate the depth-to-metric scale, exact 2D-to-MKF marker correspondence, camera/table axis calibration, or a complete object mesh. Earlier short mode tests on the same PC reported substantially lower rates; verify performance for each capture environment.

For the earlier 2026-10-16 sweep, the default angle-trajectory pass found 32 smooth rotating candidates; allowing a 10-frame association gap found 31, and requiring at least 200 px component area found 30. These counts are close to the 34 MKF points but do not prove reflector identity. The raised-position repeat now has candidate map matches as described above; full reflector recovery and independent marker verification remain open.

The scanner's field of view covers only part of the turntable at once. In the same empty-table run, the first 640×400 Y16 frame contained 1,259 non-zero pixels; the existing threshold-1 connected-region finder reported five regions with its default size limits. A single frame therefore cannot provide the full 34-point reflector map. Build the map by accumulating detections across the full rotation and associating each observation with the measured table angle; exact correspondence to the MKF points still needs to be solved.

## Experimental 360° fusion result

The twelve 30° raw captures were reprojected and tested with a candidate Z-axis rotation and overlap-fitted pivot. A candidate transform of −30° per commanded +30° step around approximately `[28.8, -5.4]` in current Q coordinates gave 81–94% adjacent-pair nearest-neighbor overlap within 5 coordinate units. The pivot and sign are empirical estimates, not mechanical calibration. A voxel-deduplicated PLY and its JSON provenance are saved as `approx_fused_candidate.ply` and `approx_fused_candidate.json` beside the source frames.

For a fresh `scan_360.ps1` folder, `fuse_turntable_scan.py` provides a repeatable diagnostic fusion without external Python packages:

```powershell
python .\tools\revopoint\fuse_turntable_scan.py .\projects\revopoint_model_scan
```

It reads each frame's measured table angle, estimates an XY pivot under a camera-Z-axis assumption, scores even-index adjacent pairs, reports odd-index pairs as held-out overlap, rotates all source clouds back to the first pose, and voxel-deduplicates them. It writes an experimental PLY and JSON next to the input data and refuses to overwrite existing outputs. Defaults use every 64th point for pivot fitting, a 20-unit search radius around the median cloud center, and a 4-unit coarse step followed by local refinement; widen the search radius if the expected pivot lies farther from the cloud center. Use `--match-stride` and the pivot-search options to change sampling/search density. The reported overlap is a diagnostic, not an accuracy guarantee: the transform is not independently calibrated, Q coordinates have no verified physical scale, and incomplete occluded surfaces remain absent.

On the 2026-10-16 current-setup capture, the denser defaults estimated pivot `[32.1, -22.7, 0]` and achieved 82.2% training and 86.9% held-out point overlap within 5 Q units. The prior sparse settings scored 71.1% held-out. The earlier manually fitted candidate scored about 90% with a different sampling procedure. These candidates remain diagnostic; the multi-tilt experiment added points but only slightly increased coverage against the user's earlier scan, so the transform, scale and completeness still need validation.

The initial CADYX reconstruction had 24,641 vertices and 49,979 triangles. Subsequent trials at higher triangle count and with density/outlier filtering still produce a thin plate-like result with isolated fragments; this is a diagnostic model, not a verified complete scan. The raw disparity scale, mechanical axis/pivot and coverage of occluded surfaces need validation before the fusion can be accepted.

The mesh and source cloud have similar PCA eigenvalues for their thinnest dimension (14.1 vs. 15.6 coordinate-units²). This indicates the thin, plate-like shape is already present in the fused input, rather than being created only by Poisson reconstruction. It still does not prove the object itself is fully covered: the underside and occluded surfaces are not validated.

List the camera's advertised native modes with:

```powershell
& .\tools\revopoint\build\CaptureY16.exe --list-modes
```

The connected camera advertises Y16 at 640×400, 1280×800, and 640×800, each with nominal 5, 10, and 15 fps modes. Earlier explicit 640×400 selections at nominal 5, 10, and 15 fps each delivered about 4.6 fps by Media Foundation timestamps in 40-frame runs. Later measurements were inconsistent; see below. The direct UVC path has not demonstrated a stable 10 fps, and these checks do not measure Revo Scan's complete capture pipeline.

The same Windows enumeration exposes an `RGBCam` interface with 1280×800 MJPG and NV12 modes at nominal 10, 15, 20, and 30 fps. `CaptureRgb.exe` can request either advertised format directly. On 2026-10-16, both formats selected alone at nominal 10 fps returned no sample within a 12-second watchdog window. Starting the DepthCam reader first allowed a simultaneous MJPG run: 30 RGB and 30 Y16 frames were acquired while the turntable telemetry connection was active. RGB Media Foundation timestamps span 15.65 seconds (~1.9 fps), with 16.44 seconds between first and last UTC arrival (~1.8 fps); Y16 timestamps span 15.54 seconds (~1.9 fps), while the capture process wall time was 21.1 seconds (~1.4 fps). Requesting nominal 10 or 30 fps did not raise the measured rate to 10 fps. Pair frames by UTC arrival timestamps; RGB and depth do not share a common Media Foundation clock. JPEG samples can contain trailing bytes after EOI, so the capture utility trims the payload at the JPEG end marker and preserves malformed samples under an `.invalid-mjpg.bin` name before reporting an error. Run logs and source frames are in `projects/revopoint_marker_rotation_sync_20261016/`.

The local `DetectMarkers.exe` and `detect_y16_markers.py` utilities are initial bright-component candidate finders, not a verified reproduction of Revopoint's detector. They report weighted/grayscale or connected-region centers and shape measurements for inspection. The user-provided `framesmall.mkf` export contains 34 records with 3D coordinates and unit normals; `parse_mkf.py` parses the observed 16-byte header plus 64-byte records and exports CSV/PLY. Its binary layout is empirically checked against the declared record count, finite coordinates and normals, and planar consistency, but field meanings and coordinate units are undocumented. The export is suitable as a reference for camera-to-table-map calibration, but current 2D detections have not yet been matched to all 34 records or accuracy-validated.

When the turntable moves between two RGB captures, use `classify_marker_motion.py` with the two detector CSVs and measured table angles. Candidates that remain at the same pixel center are flagged as stable in the image; candidates that have no nearby after-pose center are flagged as moved or not redetected. The output is only a background-rejection aid: it does not identify the same physical reflector in both images, and a reflector near the rotation axis can remain stable or ambiguous. On the stored -360°→-330° empty-table sequence, full-frame threshold-150 detections yielded 33 candidates before and 32 after; the classifier found 5 stable-in-image, 26 moved/not-redetected, and 2 ambiguous candidates. Four stable candidates are above the visible turntable edge; one falls on the tabletop and could be near the rotation axis or be a false candidate. Cropping to `y >= 250 px` leaves 29 candidates before and 28 after the turn. This confirms the RGB sequence contains useful table motion, but does not validate an exact 34-point map. Example:

```powershell
py .\tools\revopoint\classify_marker_motion.py `
  --before .\projects\capture\before_candidates.csv --before-angle 0 `
  --after .\projects\capture\after_candidates.csv --after-angle 30 `
  --min-y-px 250 `
  --out-csv .\projects\capture\motion_candidates.csv
```

### Revo Scan DLL analysis and isolated SDK probe

The installed Revo Scan 5.5.5 package was inspected without launching the application. The relevant vendor DLLs have valid Revopoint Authenticode signatures. The installation folder has no headers, import libraries, or PDB files. Revopoint's public `3DViewer` repository includes `3DCamera` headers, import library, and SDK DLL (3.2.23.0); its `CameraType` enum explicitly includes `CAMERA_MINI_NORMAL` and `CAMERA_MINI_NO_RGB`. The SDK DLL exports all 114 named functions also present in installed Revo Scan `3DCamera.dll` (3.2.140.0); the installed copy adds four names: `setSdkConfigData`, `setSdkConfigPath`, `getMarkersInfo`, and `upgradeCameraPro`. An isolated helper loaded the installed DLL and enumerated the connected MINI using the public 3.2.23 import library. `ICamera::connect` returned error 8 (`ERROR_NOT_SUPPORT`), so this version pair did not permit runtime calibration queries and is not a validated integration ABI. The public SDK does not include headers or an import library for Revo Scan's separate `zxPointCloudProcessing64.dll`.

`3DCamera.dll` exports the camera/frame API: enumerate/connect/disconnect, create/start/stop streams, get single or paired frames, read frame data/format/dimensions/timestamp/extra-info, query intrinsics/extrinsics/distortion, and request marker information. The public SDK headers document callback and pull-based frame acquisition, and require releasing frames after use. Its generic `ExtraInfo` structure contains an optional `gyroPosAndPos` extension with quaternion fields `q0..q3`, gyroscope fields `gx/gy/gz`, acceleration, magnetic-field values, and a motion score. This proves the SDK frame format can carry such metadata; it does not prove that the original MINI supplies it. The public 3DViewer sample converts each incoming depth frame to a point cloud using camera calibration and publishes that frame as a live 3D update; it does not implement multi-frame registration/fusion in that path.

The public SDK defines Z16 depth as `uint16` and documents `Z_mm = raw_value × depthScale`. Installed Revo Scan logs repeatedly report `depthScale=0.005`. The SDK `Intrinsics` structure is exactly 40 bytes (two 16-bit dimensions and nine floats), matching the local `Pl.bin` and `Prgb.bin` sizes and decoded layouts. `Pl.bin` gives 640×400, `fx=fy=891.2453`, `cx=52.7665`, `cy=199.49365`; `Prgb.bin` gives 640×400, `fx=fy=899.3882`, `cx=331.0519`, `cy=202.1866`. This provides a strong depth-scale candidate and verified camera intrinsics without assuming Q-matrix disparity. Whether the raw values from the independent UVC reader are byte-for-byte the SDK's Z16 values remains unconfirmed; validate against a known dimension or a supported SDK connection before claiming metric accuracy.

The marker pipeline is split across `markerDetectionByGradient.dll` and `markerDetectionByJH.dll` (OpenCV-backed `IMarkerExtraction` factories), `markerMatchByEpipolar.dll` (camera-parameter setup and point undistortion), `markerAlignByTriangle.dll` (`IMarkerAlign` factory), and `markerFusionByTriangle.dll` (`IMarkerFusion` factory importing the aligner). The fusion DLL contains strings for `createBaFusion`, keyframe selection/optimization, frame-marker alignment, global optimization, point filtering, and intermediate PLY dumps. These are architecture clues, not a recovered algorithm or a guarantee that its marker mode applies to the turntable reflectors.

`zxPointCloudProcessing64.dll` exports C++ classes and methods named `AlignPointCloud`, `AlignByMark`, `RobustRegisterDepthSeqs`, `GlobalRegisterDepthSeqs`, `RobustGlobalRegisterDepthSeqs`, `GlobalFuseDepthSeqs`, `ICPRegistration`, and `IntegratePointCloudByFusion`. Export signatures decoded with Microsoft's MSVC name undecorator show `RobustRegisterDepthSeqs::Init(Vector3 const&, double, double, bool, double, bool, int, int, int, double, bool)`, `AddDepthFrame(IGridPointCloud*, DepthParm const*)`, `StartSeqs()`, `GetPreview()`, `GetRegFrameInfo(int, Matrix4x4*, int*)`, and `Finish()`. Its setters include initial-transform estimation, robust ICP, tracking-acceptable-overlap ratio, fragment optimization, and group optimization. `GlobalFuseDepthSeqs::Init(int, int, double, double, double, MergeType, int, bool, double)` takes frames as `AddDepthFrame(IPointCloud*, vector<Vector3> const*, Matrix4x4 const*, int)`, then exposes `FuseSeqs()` and `GetFusedCloud(bool)`. This confirms an incremental registration/preview path and a distinct global fusion path. It does not reveal safe parameter units/defaults or the layout/ownership contract for the private `GPP` point-cloud classes and `DepthParm`; direct calls from CADYX remain unsafe without that contract. `rpmarker.dll` exposes `SetImage`, `SetParam`, and `Detect` on its C++ detector class.

The original Revopoint MINI is confirmed by the user; Revopoint lists its position sensors as `No` in the [MINI specification](https://global.revopoint3d.com/en-hk/products/official-refurbished-mini). Do not infer an onboard gyro from the generic SDK extension fields; the current direct UVC capture reads only RGB/Y16. For CADYX turntable capture, use the table encoder and calibrated visual/3D registration. MINI calibration-step JSON contains per-pose exposure and left/right image-bound settings. The `config/calibration/0A8/calibration.json` circle fields belong to a coded calibration-board schema and are not evidence of the turntable reflector diameter.

`CameraControls.exe` reads standard DirectShow exposure and image-control ranges/current values for the enumerated scanner cameras. It is intentionally query-only because the driver did not confirm a requested manual RGB exposure mode in an earlier experiment.

For timestamp-only measurements of other native modes (without applying the 640×400 calibration to them), use `--measure-only` with explicit dimensions:

```powershell
& .\tools\revopoint\build\CaptureY16.exe --out-prefix (Join-Path $PWD 'projects\revopoint_rate\1280x800') --frames 40 --fps 10 --width 1280 --height 800 --measure-only
```

Additional direct-UVC measurements on 2026-10-16 varied substantially by run. At 640×400, nominal 5/10/15 fps selections yielded 1.72–2.01 fps by Media Foundation timestamps and 1.00–1.51 fps by wall clock in 10–20-frame checks; frame timestamps arrived in pairs separated by roughly one second. A later 43-second synchronized sweep measured about 13.1 fps from both cameras, including the full 32-second rotation. Successful frames had zero Source Reader flags; a no-frame read returned `0x100` (`MF_SOURCE_READERF_STREAMTICK`). The timestamp CSV includes each delivered frame's Source Reader flags, and the console reports flags for reads without a frame. At 1280×800, nominal 15 and 10 fps each delivered 0.69 fps by Media Foundation timestamps over 40 frames; 640×800 nominal 10 did not deliver 40 frames within about 150 seconds and was stopped. Treat that mode as unverified, not as a measured zero rate. Raw bursts and timestamp CSVs are retained under `projects/revopoint_mode_rate_20261016/`.

The local Revo Scan 5 log for the same camera identifier gives a historical comparison. The paired stream reported 12.3–13.0 fps in four consecutive readings from 09:09:01 to 09:09:32 on 2026-10-14, then fell to 5.7–6.7 fps for roughly three minutes; one pair-frame failure (error 7) occurred at 09:11:20. Later it reported 8.0 fps for six consecutive readings around 09:41–09:42, with no pair failure in that minute. RGB/depth counters sometimes diverged (depth near 12 fps while RGB and paired output were near 6), so the paired rate is the meaningful figure. Those readings do not establish sustained 10 fps. A later independent direct-UVC sweep did establish over 10 fps during a controlled turn, as recorded above. The proprietary pipeline was faster in parts of its historical log; the direct-reader performance variation remains unexplained.

The raw-to-disparity scale `256` is provisional and has not been validated against a known object dimension, so treat the PLY scale as unverified. The continuous sweep records per-frame table poses and exploratory 2D bright-component tracks, but it does not align/fuse the 3D frames or create a watertight object mesh. The 34 detections have not been matched to the `framesmall.mkf` 3D map or accuracy-validated. The original MINI model is user-confirmed; its generic USB camera names alone do not identify an IMU or provide pose telemetry.

Keep each capture prefix unique. The original Y16 frame is preserved beside the derived PLY so later calibration work can use the same input.

## Query and move the dual-axis turntable

```powershell
& .\tools\revopoint\build\TurntableController.exe query
& .\tools\revopoint\build\TurntableController.exe zero
& .\tools\revopoint\build\TurntableController.exe speed 18
& .\tools\revopoint\build\TurntableController.exe rotate 5
& .\tools\revopoint\build\TurntableController.exe stop
```

`query` reads rotation angle and tilt value. `speed` sets the requested period in seconds per revolution from 18 to 90; smaller values rotate faster. `rotate` sends one relative move and is limited to 30° per command; use a negative angle to reverse it. `tilt` sets the second axis to a target from −30° to +30° and verifies the current value. `zero` returns both axes to their boot-time homes. `stop` sends stop commands to both axes. The tool scans for the BLE FFE1 service, connects without pairing, confirms the GATT name `REVO_DUAL_AXIS_TABLE`, and uses the FFE0/FFE1 service. The observed ASCII commands match the independently documented Revopoint Dual-axis Turntable protocol: [protocol research](https://github.com/opensourcemanufacturing/Revopoint-Dual-Turntable).

Revo Scan, Revo Assistant, or another Bluetooth client can hold the table connection. The controller does not retry a movement command after a lost response.

## Capture a full turn at 30° intervals

Secure the object to the turntable, keep the scanner fixed, close Revo Scan, and run:

```powershell
.\tools\revopoint\scan_360.ps1 -OutputFolderName revopoint_scan_20261016 -DisparityScale 256
```

The script returns the table to its home zero, captures at 0°, 30°, …, 330°, and homes the table again. It saves every raw frame, per-frame provisional PLY, calibration copy, and angle manifest in a new direct workspace folder. It does **not** register the twelve views or merge them into one mesh. The table center/axis and disparity scale still need validation before the views can form a trustworthy complete model.

## Multi-tilt capture and registration status

The multi-tilt script captures five full turns in this order: 0°, +5°, −5°, +10°, −10°. Each turn is sampled every 30° at stationary poses, producing 60 Y16 frames, provisional PLY views, metadata, and per-pass pose logs directly in the selected project. Secure the object before using tilt; tilt is constrained to ±10°. The script verifies tilt/rotation telemetry, preserves completed captures on failure, resumes a run by its UTC run ID, and homes both axes at completion. It refuses to create a second project or overwrite existing run files.

```powershell
$runId = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
$projectPath = Join-Path $PWD 'projects\Исходные кадры сканирования'
.\tools\revopoint\scan_tilt_series.ps1 -ProjectPath $projectPath -RunId $runId
```

Registration is a separate step and must not assume the table axis equals camera Z. The tilt axis is not calibrated in camera coordinates. The previous `fuse_multi_tilt_scan.py` experiment used an XY pivot and that Z-axis assumption for every pass; it produced a flat, invalid combined cloud and has been removed. An ICP check of the first adjacent pair converged to 4.8° for a commanded 30° move, showing a local planar match rather than a trustworthy pose estimate. Do not import that candidate as scan geometry. Its PLY/JSON artifacts remain in the project as failed diagnostic output; the bad CADYX body was removed while the original comparison body and all captured source frames were retained.

The 2026-10-16 campaign `20261016T135207Z` saved all 60 poses and returned the table to zero. The old candidate's reference-directed alignment covered about 45% of the full reference points within 5 provisional Q-coordinate units. This only indicates partial coverage: the reference was assembled from views of multiple sides, and the captured views need not reproduce all of it. A usable workflow still needs 3D pose registration with independently validated overlap and closure checks before any merged cloud is displayed as a model.
