# Revopoint camera calibration bundle

This device-specific bundle was copied as-is from `%LOCALAPPDATA%\RevoScan5\cameraparam` on the connected scanner PC. It lets the repository's UVC capture and offline marker-calibration work continue from another checkout without finding that PC's AppData folder or launching Revo Scan.

`Q.bin` is the 64-byte little-endian float32 reprojection matrix used by `CaptureY16.exe` to obtain a camera ray. The uint16 sample is first converted to candidate depth with the `depthScale` read from Revo Scan, then the ray is scaled so camera Z equals that depth. This direct UVC depth mapping still needs validation against a known physical dimension. `Pl.bin` and `Prgb.bin` each contain 640×400 dimensions followed by a 3×3 float32 matrix. The remaining files are preserved calibration inputs for ongoing camera/marker research; their binary schemas are not fully established. Files are committed byte-for-byte and should not be edited or replaced with calibration from another scanner.

`depth_scale.json` records the installed Revo Scan SDK's `0.005` depth scale and its provisional status for direct UVC frames. Keep the value together with this scanner's calibration bundle; do not infer it from the Q matrix or change it to improve overlap scores.

The `framesmall.mkf` export in the repository root is the separate user-provided table-marker reference. Its coordinate units and complete record semantics remain unknown; see `tools/revopoint/parse_mkf.py` and the Revopoint tools guide.

The normal `projects/` workspace remains ignored and is not part of the cross-PC source bundle. Capture outputs should be regenerated into a new workspace folder on the target PC.
