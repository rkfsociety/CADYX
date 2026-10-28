# Revopoint tools for Windows

[Русский обзор](#русский-обзор) · [English reference](#english-reference)

[Полный русский перевод](README.ru.md)

## Русский обзор

### Экспериментальные инструменты Revopoint

Инструменты предназначены для прямого UVC-захвата и управления столом в Windows без запуска Revo Scan. Для подключённого Revopoint MINI используется отслеживаемая калибровка `Q.bin` и `depth_scale.json`; для другого устройства установите его локальную калибровку по [инструкции](calibration/README.md). Источники RAW, PLY, метаданные и позы сохраняются в пользовательском workspace. Захват выполняйте при неподвижном столе, поворачивая его между кадрами; обычный режим — шаг 30° и наклон 0°. Установленный пользователем предел наклона ±10°. Физический масштаб и регистрация требуют проверки. Нельзя выдавать облако или удачное совмещение за метрически подтверждённую модель.

Это обзор, не полный перевод. Полные схемы, примеры и подробности контракта приведены на английском ниже.

## English reference

These small command-line tools exercise the scanner and dual-axis turntable through Windows APIs. They do not use the paid Revopoint SDK or require Revo Scan to run during capture. The original connected Revopoint MINI uses the tracked `tools/revopoint/calibration/scanners/revopoint-mini-y16/Q.bin` and `depth_scale.json`; another scanner needs its own local profile. Table marker maps and defaults live under `tools/revopoint/calibration/tables/<profile_id>/`, with per-device overrides in each table's local settings folder. Follow the [calibration guide](calibration/README.md).

## CADYX persistent point-cloud reference

A completed **point-only** PLY can be uploaded into a CADYX project through the
Agent API/MCP staged scan-file upload and then imported with the leased
`import_project_point_cloud` command (`file` is the staged PLY filename,
`name` is optional). The saved scene/server PNG renders its vertices as points,
with no surface reconstruction or invented physical units. The command accepts
3–300,000 finite XYZ vertices with no faces. For section-based reverse
engineering, use `import_project_scan`/`import_scan` to build a **triangle mesh**
instead; scan alignment and section tools do not operate on the point-only
reference. See [scan reverse engineering](../../.codex/docs/scan-reverse-engineering.md)
and the [shared viewport contract](../../.codex/docs/viewport-api.md).

This is a CADYX project operation, not a turntable-control command or an
automatic step performed by every scanner capture.

## Build

Run from the repository root in PowerShell:

```powershell
.\tools\revopoint\build.ps1
```

The build uses the installed Visual Studio C++ Build Tools, Windows SDK, and .NET Framework C# compiler. It writes executables under `tools/revopoint/build/`, which is ignored by Git.

## Capture one depth frame

Close Revo Scan before capturing so it does not hold the depth camera. The Q matrix must come from the same scanner calibration. Revo Scan 5 stores it at:

```powershell
$scannerProfile = Join-Path $PWD 'tools\revopoint\calibration\scanners\revopoint-mini-y16'
$qFile = Join-Path $scannerProfile 'Q.bin' # tracked default for the connected original MINI
$outPrefix = Join-Path $PWD 'projects\revopoint_probe\frame'
& .\tools\revopoint\build\CaptureY16.exe `
    --out-prefix $outPrefix `
    --q-file $qFile `
    --depth-scale 0.005
```

The command writes `frame.raw`, `frame.ply`, and `frame.json` side by side. It refuses to overwrite any of them. The raw file contains one 640×400 little-endian Y16 frame. Reprojection treats each uint16 sample as depth (`raw × depth_scale` mm), uses the first three rows of this device's local `Q.bin` to obtain the camera ray, then scales that ray so its Z equals the measured depth. The point-cloud PLY uses candidate millimeter units; direct UVC byte-for-byte equivalence with the SDK frame is still marked provisional. The bundled MINI scale is `tools/revopoint/calibration/scanners/revopoint-mini-y16/depth_scale.json`; physical scanner overrides live in their own local scanner folders.

To reproject a saved raw frame after a processing change without reconnecting to the camera, use `reproject_y16.py`. Its output prefix must be in an existing folder (normally the same CADYX project) and existing outputs are never overwritten:

```powershell
$calibration = Join-Path $PWD 'tools\revopoint\calibration\scanners\revopoint-mini-y16'
$projectPath = Join-Path $PWD 'projects\<existing CADYX project folder>'
$scale = (Get-Content (Join-Path $calibration 'depth_scale.json') -Raw | ConvertFrom-Json).depth_scale_mm_per_raw_unit
python .\tools\revopoint\reproject_y16.py `
    --raw-file (Join-Path $projectPath 'frame.raw') `
    --q-file (Join-Path $calibration 'Q.bin') `
    --output-prefix (Join-Path $projectPath 'frame_reprojected') `
    --depth-scale $scale
```

For a continuous capture-rate measurement, add `--frames 120`. The camera stays open for the sequence; the utility saves the packed frames to `frame_burst.raw`, records Media Foundation timestamps, elapsed stream time and UTC arrival time in `frame_timestamps.csv`, and reports both timestamp-derived and wall-clock rates. The PLY remains a preview of the first frame. An earlier 120-frame run produced about 4.53 fps by media timestamps; the newer paired full-turn run below measured both streams for over 40 seconds and supersedes it for that tested configuration.

The Scanning tab's **Предпросмотр** button starts a continuous `CaptureY16 --live-only` stream and renders calibrated 3D points directly in the existing `#model-preview` canvas; no iframe or second preview window is used for the model viewport. For each poll, the main viewer selects the newest queued Y16 frame, discards older queued frames, reprojects it through `Q.bin`, and replaces the displayed point cloud in one redraw; the viewer's orbit is preserved. Every retained in-range depth return is displayed while a frame contains at most 8,000 points; denser frames use a deterministic sample within that ceiling. Before marker registration, the camera center and fit scale are fixed from the first valid frame; successful registration switches them to the table-map origin and scale. Each later frame with at least four reliable marker matches recomputes the camera-to-map transform, keeping points in the table coordinate frame as it turns. If a frame has no reliable match, the canvas keeps the last map-aligned image instead of showing points in an unaligned frame. Frames are not interpolated, blended, or accumulated; between sensor frames the canvas retains the last rendered image. Idle queue polling adapts to the measured frame interval. Live preview matches detected markers to the selected table's map, uses its near-centroid marker as the XY origin, and keeps all map markers and their calculated circular turntable paths visible while highlighting markers detected in the current frame. The selected camera view, zoom and pan remain fixed as later frames replace the live cloud and are restored after a page refresh. The standalone diagnostic page requests the newest frame from `.revopoint-live.raw.frames` without consuming it; the workspace preview consumes the selected newest frame and removes older queued frames. An active 360 capture can still expose its latest `.revopoint-live.raw` snapshot to the diagnostic endpoint. The live preview writes one temporary queue file per frame; the queue is removed when stopped, and no per-scan RAW/PLY/metadata/burst is written. The live preview and 360 capture are mutually exclusive because both use the same depth camera. **Облако точек** loads a selected saved PLY into that same canvas. The **Сканирование** tab follows **Производство**, expands the existing `#model-preview` canvas across the workspace, and keeps 360 capture, live preview and device settings in its viewport toolbar; it does not embed a second preview iframe. Switching workspace tabs only hides the live scan layer; the camera stream keeps running and its display resumes when Scanning is opened again. Only the **Предпросмотр** button starts or stops the stream. A frame older than one second is marked as stopped by the standalone diagnostic view. The depth scale is sourced from the installed Revo Scan SDK log, but direct UVC correspondence and known-dimension accuracy remain unverified. The `/scanner` route remains available for diagnostics.

```powershell
& .\tools\revopoint\build\CaptureY16.exe --out-prefix (Join-Path $projectPath 'capture') --q-file (Join-Path $calibration 'Q.bin') --depth-scale $scale --frames 1000 --fps 15 --live-raw (Join-Path $PWD 'projects\.revopoint-live.raw')
```

For a live-only positioning preview that does not save per-scan RAW/PLY/metadata or a burst, run:

```powershell
$liveFramesPath = Join-Path $PWD 'projects\.revopoint-live.raw.frames'
$stopPath = Join-Path $PWD 'projects\.revopoint-live-preview.stop'
& .\tools\revopoint\build\CaptureY16.exe --live-only --live-frames-dir $liveFramesPath --stop-file $stopPath --fps 15
```

The scanner page reads the latest frame from the fixed-size `$livePath` cache. To stop the preview from another PowerShell window, create the stop signal; the capture closes on the next frame:

```powershell
New-Item -ItemType File -Path (Join-Path $PWD 'projects\.revopoint-live-preview.stop')
```

## Live capture with turntable movement

The BLE controller can rotate while the UVC reader is active. A verified 90-frame run overlapped a 30° move: the move completed in 2.27 s, the capture acquired 90 frames at 4.54 fps by Media Foundation timestamps, and the table was homed to 0° afterward. The open browser panel reported about 3 fps during the run, showing additional polling/rendering overhead. Registration, scale validation, and full-scan fusion remain outstanding.

For a timestamped angle trace with an in-session turn command, use:

```powershell
& .\tools\revopoint\build\TurntableController.exe monitor 24 (Join-Path $PWD 'projects\angles.csv') 3 30
```

The arguments are monitor duration, CSV path, delay before the relative turn, and turn angle. The command keeps a single BLE connection, records UTC rotation and tilt samples, and sends the turn itself on that connection. In a 120-frame live capture the controller recorded 88 angle samples and observed 0°→30°. The table was homed to 0° after capture. Tilt control was tested across the supported range: target +30° reported +29.2°, −30° reported −30.0°, and the zero command restored both axes to 0°. `+QR,TILTVALUE;` reads the current tilt; `+QR,TILTANGLE;` reports the supported range instead.

The frame UTC timestamps and angle samples can be joined by linear interpolation. Verified synchronized run: 120 captured frames all received an interpolated table pose spanning 0°–30°; derived per-frame rows are saved as `capture_frame_poses.csv`. The monitor CSV also records tilt from `+QR,TILTVALUE;`. This is suitable for per-frame rigid pose estimates when the turntable axes/pivot are calibrated. The direct UVC depth mapping and mechanical axes remain unvalidated, so the pose table alone does not establish an accurate fused full model.

## Stationary marker poses

For marker/camera studies, this script captures each angle while the table is stationary. DepthCam and RGBCam processes exit before the table moves to the next 30° pose:

```powershell
$projectPath = Join-Path $PWD 'projects\Исходные кадры сканирования'
.\tools\revopoint\capture_marker_sweep.ps1 -ProjectPath $projectPath -OutputFolderName marker_poses -FramesPerPose 1 -Fps 15
```

The script captures one or more stationary frames from each camera at 0°, 30°, …, 330°, then makes a rotation-only 30° return to zero after the final camera process has exited. It never commands table tilt. Each pose and capture time is stored in `poses.csv` and `capture_manifest.json`; raw frames, point-cloud previews, and RGB images stay in one run subfolder inside the existing CADYX project. This marker-pose script closes both cameras before each of its own table moves. Separately, the CADYX control API blocks table movement and speed changes only during an active CADYX 360 capture; live-only preview does not block manual table control. Partial captures are preserved, and an error does not trigger an automatic table move.

The `analyze_marker_sweep.py` tool below is for historical moving-camera sweeps that already contain continuous timestamp streams and a `turntable_sweep.csv`. It does not process the stationary pose format; those captures should be associated by their recorded pose angles.

For a historical continuous sweep, the old analysis command was:

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

The initial CADYX reconstruction had 24,641 vertices and 49,979 triangles. Subsequent trials at higher triangle count and with density/outlier filtering still produce a thin plate-like result with isolated fragments; this is a diagnostic model, not a verified complete scan. The previous `raw/256` disparity interpretation, mechanical axis/pivot and coverage of occluded surfaces need validation before that fusion can be accepted.

The mesh and source cloud have similar PCA eigenvalues for their thinnest dimension (14.1 vs. 15.6 coordinate-units²). This indicates the thin, plate-like shape is already present in the fused input, rather than being created only by Poisson reconstruction. It still does not prove the object itself is fully covered: the underside and occluded surfaces are not validated.

## Two-frame fusion with a known table pose

When the table angle is measured and an axis/pivot estimate is available, fuse two saved point-only PLY files with an arbitrary 3D axis:

```powershell
$run = Join-Path $PWD 'projects\revopoint_single_frame_20261019'
python .\tools\revopoint\fuse_known_pose_pair.py `
    (Join-Path $run 'depthcam_0deg_001.ply') `
    (Join-Path $run 'depthcam_15deg_001.ply') `
    (Join-Path $run 'fused_pair_0_15deg.ply') `
    --table-angle-deg 15 `
    --axis -0.001874 -0.154783 0.987947 `
    --pivot 34.941 -19.133 117.392
```

The tool rotates the second frame by the negative measured table angle, reports sampled bidirectional overlap at 1/2/5 Q units, voxel-averages the merged points, and writes a JSON provenance file beside the PLY. It refuses to replace either source or an existing output. Supply an independently measured angle and a justified axis/pivot; this run reuses the prior marker-registered axis/pivot and is therefore a provisional registration. Its physical scale remains unverified and the result covers only the surfaces visible in these two views.

List the camera's advertised native modes with:

```powershell
& .\tools\revopoint\build\CaptureY16.exe --list-modes
```

The connected camera advertises Y16 at 640×400, 1280×800, and 640×800, each with nominal 5, 10, and 15 fps modes. Earlier explicit 640×400 selections at nominal 5, 10, and 15 fps each delivered about 4.6 fps by Media Foundation timestamps in 40-frame runs. Later measurements were inconsistent; see below. The direct UVC path has not demonstrated a stable 10 fps, and these checks do not measure Revo Scan's complete capture pipeline.

The same Windows enumeration exposes an `RGBCam` interface with 1280×800 MJPG and NV12 modes at nominal 10, 15, 20, and 30 fps. `CaptureRgb.exe` can request either advertised format directly. On 2026-10-16, both formats selected alone at nominal 10 fps returned no sample within a 12-second watchdog window. Starting the DepthCam reader first allowed a simultaneous MJPG run: 30 RGB and 30 Y16 frames were acquired while the turntable telemetry connection was active. RGB Media Foundation timestamps span 15.65 seconds (~1.9 fps), with 16.44 seconds between first and last UTC arrival (~1.8 fps); Y16 timestamps span 15.54 seconds (~1.9 fps), while the capture process wall time was 21.1 seconds (~1.4 fps). Requesting nominal 10 or 30 fps did not raise the measured rate to 10 fps. Pair frames by UTC arrival timestamps; RGB and depth do not share a common Media Foundation clock. JPEG samples can contain trailing bytes after EOI, so the capture utility trims the payload at the JPEG end marker and preserves malformed samples under an `.invalid-mjpg.bin` name before reporting an error. Run logs and source frames are in `projects/revopoint_marker_rotation_sync_20261016/`.

The local `calibration/tables/revopoint-dual-axis-table/framesmall.mkf` map contains 34 3D marker records with unit normals; `parse_mkf.py` parses the observed 16-byte header plus 64-byte records and can export CSV/PLY. The live preview estimates the table center from the map marker nearest its projected centroid, places that marker at the viewport origin, and aligns the map plane to XY. It displays all map markers and calculates a full circular path around the estimated turn axis for each marker, including markers not detected in the current frame; only map-confirmed detections are highlighted at their exact map positions. Unmatched bright-region candidates and mapped surface returns at or below 2.5 map units are omitted from the live point layer; captured and stitched scan coordinates are unchanged. Live registration requires at least four matched markers; unresolved or ambiguous maps are rejected. The file is user-provided, remains excluded from Git, and its units and complete field semantics are unknown. The preview removes points below the mapped table plane because that violates the user's physical setup constraint. These steps establish the preview datum, not certified physical dimensions or accuracy.

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

Installed Revo Scan logs for this scanner report a 640×400, 15 fps depth stream (`format:3`) and `depthScale=0.005`. The local `Q.bin` hash matches the active `%LOCALAPPDATA%\RevoScan5\cameraparam\Q.bin`; `Pl.bin` also matches byte-for-byte. The 40-byte `Pl.bin` is 640×400 with `fx=fy=891.2453`, `cx=52.7665`, `cy=199.49365`; `Prgb.bin` is 640×400 with `fx=fy=899.3882`, `cx=331.0519`, `cy=202.1866`. Reprojection now scales the uint16 sample by the logged depth scale, uses Q to obtain a ray, then scales the ray to the measured Z depth. A 0°/14.4° table pair gave a 13.1° fitted cloud rotation and 100%/97.9% directional overlap within 5 mm using this projection; the old `raw/256` disparity path gave only 2.7–3.1 Q units of robust depth spread and a 0.7° fitted rotation. This is strong internal evidence for the corrected method, not independent proof of UVC/SDK buffer identity or absolute scale; verify against a known dimension before treating the scale as certified.

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

The raw-to-disparity scale `256` is provisional and has not been validated against a known object dimension, so treat the PLY scale as unverified. The continuous sweep records per-frame table poses and exploratory 2D bright-component tracks, but it does not align/fuse the 3D frames or create a watertight object mesh. Live-preview marker matching does not change that saved-capture limitation or certify the map's units. The original MINI model is user-confirmed; its generic USB camera names alone do not identify an IMU or provide pose telemetry.

Keep each capture prefix unique. The original Y16 frame is preserved beside the derived PLY so later calibration work can use the same input.

## Query and move the dual-axis turntable

```powershell
& .\tools\revopoint\build\TurntableController.exe query
& .\tools\revopoint\build\TurntableController.exe zero
& .\tools\revopoint\build\TurntableController.exe speed 18
& .\tools\revopoint\build\TurntableController.exe rotate 5
& .\tools\revopoint\build\TurntableController.exe stop
```

The Scanning viewport also has a square turntable panel in its upper-right corner. It reads both axis positions, sets the revolution period, moves the rotation axis by 5°, 15° or 30°, sets tilt within ±10°, homes both axes, stops both axes, and repeats verified 360° turns until **Стоп** is pressed. Continuous rotation requires zero tilt. The live-preview camera may remain active while moving the table; the CADYX control API locks table commands only while its 360° scan job is capturing, and will not start a scan while continuous rotation is active. During continuous rotation, the panel refreshes its encoder angle about once every 1–2 seconds; bounded moves update the position when the command completes. While idle, the refresh icon explicitly queries the table; passive status polling uses the latest cached position and does not open a competing BLE connection. The firmware accepts bounded turns, so the continuous mode reissues the next 360° command after telemetry confirms the previous turn.

The same scanner API is available through the local MCP without browser control: `cadyx_get_scanner_devices`, `cadyx_get_turntable_status`, `cadyx_get_turntable_marker_map`, and `cadyx_control_turntable`; live preview has `cadyx_get_scanner_live_preview_status`, `cadyx_start_scanner_live_preview`, and `cadyx_stop_scanner_live_preview`. The `monitor` turntable command samples angle and tilt for 5–60 seconds without sending a movement command. Pass a known `device_id` and `profile_id` to avoid Bluetooth rediscovery. Turntable commands retain the API's ±30° rotation, ±10° tilt and 18–90 second revolution-period limits. The marker-map tool returns coordinates and normals with a SHA-256; its coordinate units are unverified. Current live match count and residual remain visible in the scanning viewport.

Live preview replaces the coordinate grid with a display-only turntable: a circular 200 mm diameter, 10 mm thick top plate and a simplified circular lower support guided by the supplied photos. Support dimensions are illustrative, not measured. The lower support stays fixed; the top plate, map markers and marker-aligned cloud rotate and tilt together in the viewport only. The top plate has 5° ticks and 30° labels; labels sit outside its outline and move with the plate. Zero is centered below the top view and appears in the lower-left sector in isometric view. The front camera looks along the tilt hinge axis; the right camera looks along the perpendicular horizontal axis. While the camera is dragged, the display uses a faster painter-sorted pass with its silhouette retained; depth-tested rendering returns when the drag ends. The preview highlights only live detections that matched the map, at the exact map marker positions; unmatched bright-region candidates are omitted from the mapped preview layer. Marker registration and point coordinates for stitching remain unchanged. The display follows tilt telemetry through ±30°; the actuator API remains limited to ±10° tilt.

`query` reads rotation angle and tilt value. The API/UI reports the equivalent signed orientation in `[-180°, 180°)`, so encoder values such as `−355°` appear as `+5°`; relative movement still follows the requested direction. When an exact BLE device address is supplied, discovery ends as soon as that device advertises the required service instead of waiting through the full scan window. `speed` sets the requested period in seconds per revolution from 18 to 90; smaller values rotate faster. `rotate` sends one relative move and is limited to 30° per command; use a negative angle to reverse it. `tilt` sets the second axis only within the user-defined safe operating range of −10° to +10° and verifies the current value. Although the table protocol accepts wider angles, do not exceed this limit on this setup: the scanned part fell off the table near 29°. The CLI enforces the ±10° limit; `zero` remains available to return both axes to their boot-time homes. Only tilt a part that is present and secured. `stop` sends stop commands to both axes. The tool scans for the BLE FFE1 service, connects without pairing, confirms the GATT name `REVO_DUAL_AXIS_TABLE`, and uses the FFE0/FFE1 service. The observed ASCII commands match the independently documented Revopoint Dual-axis Turntable protocol: [protocol research](https://github.com/opensourcemanufacturing/Revopoint-Dual-Turntable).

Revo Scan, Revo Assistant, or another Bluetooth client can hold the table connection. The controller does not retry a movement command after a lost response.

Before geometric registration, each frame's Y16 image is segmented into connected table-marker regions. Their pixel indices map directly to the paired PLY vertex order, so only marker vertices are removed. The capture-time worker then runs `isolation_filter` and `remove_overlaps` on that same PLY and atomically replaces it with the cleaned result; it does not create a second cleaned PLY. The RAW frame remains unchanged, and the per-frame JSON report records the original/output hashes, detected marker regions and removed vertices. The scan worker uses a 0.59-unit neighborhood radius, just above the maximum separation allowed by the standard overlap tolerances, to stay within the surface-comparison budget. Cleanup does not use table angles or marker-map coordinates to register geometry.

## Capture a full turn at configurable intervals

The Agent API accepts `step_degrees` from 1° to 30° when it divides 360° (default 30°), so 15° captures 24 frames. Set `align_to_markers=false` to preserve camera-frame PLY files for geometry-only registration. The PowerShell entry point exposes the same options as `-StepDegrees` and `-SkipTableMarkerAlignment`.

Secure the object to the turntable, keep the scanner fixed, close Revo Scan, and run. Frame cleanup requires the Python interpreter passed with `-PythonExecutable` (the Agent API supplies the one running CADYX); filtering uses project source and the standard library, without Open3D:

```powershell
.\tools\revopoint\scan_360.ps1 -ProjectPath 'projects\<existing CADYX project folder>' -DepthScale 0.005
```

The script operates inside the supplied existing CADYX project; it does not create another project. Every 360° series is stored under its own `revopoint_<UTC timestamp>` folder inside that project. The Files view lists these folders and allows browsing their RAW, RGB when captured, metadata and PLY files; a PLY can be previewed inside the series folder. The Scanning tree has a red trash action for each series; after confirmation it removes the series folder and its files, or the matching root files for a legacy series. Deletion requires a free project and is rejected while a model feature references the series. The Scanning tree lists only selectable PLY scans and previews the selected cloud in the 3D viewport; clearing the selection restores the prior viewport mode or the latest capture preview. The Files view retains the full series contents. Existing captures stored directly in the project root remain readable. The **Устройства** button at the right side of the header discovers connected USB scanners and Bluetooth turntables. Users select detected instances matched by the version-controlled [`devices.json`](devices.json) catalog. Each scanner has its own local calibration folder under `%LOCALAPPDATA%\CADYX\scanner-calibration\<sha256-device-id>`; each turntable has a separate `%LOCALAPPDATA%\CADYX\turntable-settings\<sha256-device-id>` folder for its rotation period and optional marker-map override. Model defaults are grouped under `calibration/scanners/<profile_id>/` and `calibration/tables/<profile_id>/`. The browser saves selected instance IDs and sends their exact USB/Bluetooth IDs to the capture controller. **Сканировать 360°** captures a full-turn series at the configured step (30° by default; 15° produces 24 frames) and verifies that the full turn returns to the starting angle. The table must already be level; capture never sends a tilt or home command. Immediately after each source PLY is written, a single background worker processes completed frames sequentially in frame-index order, performing one ordered pass of marker removal, `isolation_filter` and `remove_overlaps` while the table moves and the next frame is captured. The worker atomically replaces that same PLY when cleanup succeeds; RAW remains unchanged, and only JSON reports are stored beside it. The capture waits only for work still queued and does not continue to alignment or stitching until every frame passes both filters; filter failure preserves RAW and any PLY frames not yet processed. After the sweep, optional marker-map alignment reads the cleaned source PLYs and writes separate `*_table_aligned.ply` files. Frames with at least four reliable marker correspondences get a separate aligned file in the map-centered XY frame. If a large object hides the markers or the match is uncertain, that cleaned frame stays in camera coordinates and is marked deferred. Its center can be attached later after identifying axes and planes in the point cloud or mesh. The map is copied into the series folder with the capture so the alignment can be reproduced. Geometric stitching starts only when cleaned frames are ready.

Selecting a PLY scan in the Scanning tree opens it in the existing `#model-preview` canvas; no iframe or second preview window is created. The point cloud replaces the displayed geometry in the shared viewer and can be orbited and zoomed there. When the newest captured frame has a marker-aligned copy, the viewer loads it directly in the table-map frame. An unaligned source frame is shown in its own fitted table plane; it is not presented as centered on the table map. The live Y16 preview matches marker centers against the same table map, places its selected center marker at `(0, 0, 0)`, aligns the table surface to XY and removes points below Z=0. Point-map matching uses a uniform similarity transform, preserving circular geometry without shear. Map units and direct-UVC physical scale remain unverified.

If all twelve frames exist but manifest creation failed, rebuild only that metadata from the saved pose log with `-ManifestOnly -RunId revopoint_<UTC run id>`. This mode verifies all capture files, level tilt and return-to-start telemetry, then writes the manifest without starting the scanner or table.

## Continuous RGB/Y16 capture and colorized fusion

The separate continuous-turn workflow keeps the live DepthCam Y16 reader and RGBCam MJPG reader active while the table receives one `+CT,TURNANGLE=360;` command. It archives every changed Y16 frame and every RGB image under one `revopoint_<UTC timestamp>` project folder. Run it from the scan-processing environment while the CADYX host is available:

```powershell
$runId = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
$project = 'projects\<existing CADYX project folder>'
& .\.runtime\scan-reconstruction\venv\Scripts\python.exe `
    tools\revopoint\capture_continuous_turn.py $project --run-id "revopoint_$runId"
```

Frame registration and fusion use every archived Y16 frame. The selected 30° samples are loop-closure anchors only. Geometric loop closures with ICP fitness below 0.6 are rejected; the report keeps all candidate scores and post-optimization edge residuals so weak matches are visible. The resulting point-only geometric cloud and JSON registration report remain in the series folder. When `LC_RT.bin` and `Prgb.bin` exist under `%LOCALAPPDATA%\RevoScan5\cameraparam`, the capture copies them into that folder and also writes a separate colorized PLY. Color mapping uses unique depth/RGB pairs within 15 ms, rejects RGB-occluded depth samples with a per-image depth buffer, and transfers sampled colors to nearby vertices of the already fused cloud; vertices without a nearby color sample are neutral gray. The geometric PLY and all source frames remain unchanged.

The `LC_RT.bin` transform interpretation is empirical: the local 48-byte file is decoded as a 3×3 rotation followed by a translation, then checked against paired RGB images and visible table-marker regions at multiple poses. The vendor format is undocumented here, no RGB lens-distortion correction is applied, and direct-UVC depth scale remains provisional. Keep both color and geometric results marked provisional until a known-size target and full surface coverage are independently checked. The manifest and color-mapping sidecar record pair counts, coverage, calibration hashes and limits.

## Register a turntable scan by geometry in the first-frame coordinate system

This optional offline tool reuses during-capture cleaned source PLYs only when their output hashes match the per-frame reports. For legacy series without those artifacts, it applies CADYX `isolation_filter` and `remove_overlaps` in memory before registration and leaves those legacy source files unchanged. Only after per-frame cleanup are the clouds downsampled for registration. Registration uses geometric correspondences and does not use the turntable encoder, table axis or a guessed pivot for transforms. FPFH/RANSAC seeds each adjacent pair. It then aligns each new frame to the accumulated cloud with robust point-to-plane ICP, optimizes adjacent and first-frame loop constraints in an Open3D pose graph, and adds a strong endpoint-closure constraint only when geometric ICP passes its fitness and residual checks. After stitching it keeps the largest connected point-cloud component. The JSON report records adjacent overlap, endpoint closure and local correction metrics; the result remains provisional until inspected. The output is a point-only PLY; no mesh is built. Open3D documents global feature registration and multiway pose-graph registration in its [global registration guide](https://www.open3d.org/docs/latest/tutorial/pipelines/global_registration.html) and [multiway registration guide](https://open3d.org/docs/latest/tutorial/pipelines/multiway_registration.html).

Use an optional virtual environment so the app runtime stays separate from scan processing:

```powershell
python -m venv .runtime\scan-reconstruction\venv
& .\.runtime\scan-reconstruction\venv\Scripts\python.exe -m pip install -r tools\revopoint\requirements-point-registration.txt
$runId = 'revopoint_<YYYYMMDDTHHMMSSZ>'
$project = 'projects\<existing CADYX project folder>'
$output = ".runtime\scan-reconstruction\${runId}_first_frame_geometric_candidate.ply"
& .\.runtime\scan-reconstruction\venv\Scripts\python.exe `
    tools\revopoint\register_point_clouds_first_frame.py $project $runId $output
```

Review the JSON sidecar before publishing the candidate. Check each adjacent direct-overlap fraction, last-to-first overlap, dominant-component fraction, bounds, and the residual local correction. Keep the status provisional if these disagree or are weak. Physical scale remains unverified until checked against a known length. The staging script writes outside the CADYX project and refuses to overwrite outputs; after review, publish the PLY into the same project with `cadyx_upload_project_scan` through the Agent API. Preserve all captured source frames.

## Multi-tilt capture and registration status

The multi-tilt script captures five full turns in this order: 0°, +5°, −5°, +10°, −10°. Each turn is sampled every 30° at stationary poses, producing 60 Y16 frames, provisional PLY views, metadata, and per-pass pose logs in the run's own folder inside the selected project. Secure the object before using tilt; tilt is constrained to ±10°. The script verifies tilt/rotation telemetry, preserves completed captures on failure, resumes a run by its UTC run ID, and homes both axes at completion. It refuses to create a second project or overwrite existing run files.

```powershell
$runId = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
$projectPath = Join-Path $PWD 'projects\Исходные кадры сканирования'
.\tools\revopoint\scan_tilt_series.ps1 -ProjectPath $projectPath -RunId $runId
```

Registration is a separate step and must not assume the table axis equals camera Z. The tilt axis is not calibrated in camera coordinates. The previous `fuse_multi_tilt_scan.py` experiment used an XY pivot and that Z-axis assumption for every pass; it produced a flat, invalid combined cloud and has been removed. An ICP check of the first adjacent pair converged to 4.8° for a commanded 30° move, showing a local planar match rather than a trustworthy pose estimate. Do not import that candidate as scan geometry. Its PLY/JSON artifacts remain in the project as failed diagnostic output; the bad CADYX body was removed while the original comparison body and all captured source frames were retained.

The 2026-10-16 campaign `20261016T135207Z` saved all 60 poses and returned the table to zero. The old candidate's reference-directed alignment covered about 45% of the full reference points within 5 provisional Q-coordinate units. This only indicates partial coverage: the reference was assembled from views of multiple sides, and the captured views need not reproduce all of it. A usable workflow still needs 3D pose registration with independently validated overlap and closure checks before any merged cloud is displayed as a model.

## Live-preview performance metrics

While a preview is running, `GET /api/v1/scanner/live-preview` includes a rolling `metrics` object when the capture process has emitted one. `camera_fps` uses Media Foundation sample timestamps; `capture_fps` counts frames after publication. `read_avg_ms`/`read_max_ms`, `copy_avg_ms`/`copy_max_ms`, and `publish_avg_ms`/`publish_max_ms` separate the synchronous stages, while `process_cpu_percent` reports CaptureY16's process CPU over the same window. `raw_link_fallbacks` counts frames that needed a full snapshot copy instead of a hard-link alias. `null_samples` and `last_no_frame_flags` identify missing Media Foundation samples. `frame_mtime_ns` is a filesystem publication timestamp, not a camera timestamp.

`POST /api/v1/scanner/live-preview` accepts an optional `fps` value of 5, 10 or 15; the default is 10. Use the stage metrics to compare actual wall-clock delivery before changing that requested mode.

The browser sends one aggregated `client_metrics` window per second to `POST /api/v1/scanner/live-preview/client-metrics`. The status endpoint exposes the latest window and `client_metrics_age_ms`: `request_*` covers frame fetch and body read, `process_*` covers Y16 filtering, marker detection, projection and table registration, `render_*` covers the synchronous viewport load, and `animation_*` covers opacity updates between cached clouds. Y16 filtering, marker-region detection and projection run in a dedicated Web Worker; `process_*` remains wall time for that worker result, but those calculations no longer block camera input or viewport drawing on the main thread. `animation_frames` and `display_fps` report the number and measured cadence of those display updates; `frame_cache_frames` and `frame_cache_limit` expose current occupancy and the adaptive cap. `points_avg` is the average number of newly processed points. These timings exclude GPU compositing and monitor presentation.

The browser keeps a bounded cache of 25–35 processed point frames, sized from measured frame interval and jitter. It crossfades adjacent point clouds at a target 30 FPS, bounded to 25–35 FPS, on a transparent point-only canvas layer; the table map and viewport background stay on the base canvas. This keeps each interpolation tick from rebuilding the grid. The bottom-right badge shows the measured display rate, native sensor rate (`Сенсор N/с`), and cache occupancy (`Кэш depth/limit`). The cache smooths visual changes but does not create additional depth measurements or register the rotating table.

On 2026-10-27, 63 rolling windows at requested 10 fps after removing durable frame-move flushes and before hard-linking the diagnostic snapshot recorded median `camera_fps` 5.33 (Media Foundation timestamps), `capture_fps` 4.50 (wall-clock published frames), `read_avg_ms` 217.22, `copy_avg_ms` 0.07, `publish_avg_ms` 5.01, and `process_cpu_percent` 1.17; no null samples occurred. Keep Media Foundation and wall-clock rates distinct; this is the pre-alias baseline.

After adding the snapshot hard-link alias, a new 151-window run recorded median `camera_fps` 5.32, published `capture_fps` 4.50, `read_avg_ms` 215.56, `copy_avg_ms` 0.09, `publish_avg_ms` 6.41, and process CPU 1.18%; `raw_link_fallbacks` stayed at zero. The queue now writes one frame payload and the diagnostic name aliases it, but this change did not raise the delivered frame rate. The remaining rate is limited before/around the blocking Media Foundation read, not by CPU use.

An API-controlled comparison during the same continuous table rotation measured 24 windows per requested mode (5, 10 and 15 fps). All three produced median `camera_fps` 5.32–5.33 and wall-clock published `capture_fps` 4.50; median `read_avg_ms` stayed 218–220 ms, CPU stayed near 1.2%, with no null samples or hard-link fallbacks. The 15 fps request did not raise delivery, and 5 fps did not reduce read time, so the default remains 10 fps.
