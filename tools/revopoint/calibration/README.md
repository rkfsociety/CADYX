# Revopoint camera calibration bundle

This device-specific bundle was copied as-is from `%LOCALAPPDATA%\RevoScan5\cameraparam` on the connected scanner PC. It lets the repository's UVC capture and offline marker-calibration work continue from another checkout without finding that PC's AppData folder or launching Revo Scan.

`Q.bin` is the 64-byte little-endian float32 reprojection matrix used by `CaptureY16.exe`. Its coordinate scale has not been validated against a known physical dimension. `Pl.bin` and `Prgb.bin` each contain 640×400 dimensions followed by a 3×3 float32 matrix. The remaining files are preserved calibration inputs for ongoing camera/marker research; their binary schemas are not fully established. Files are committed byte-for-byte and should not be edited or replaced with calibration from another scanner.

The `framesmall.mkf` export in the repository root is the separate user-provided table-marker reference. Its coordinate units and complete record semantics remain unknown; see `tools/revopoint/parse_mkf.py` and the Revopoint tools guide.

The normal `projects/` workspace remains ignored and is not part of the cross-PC source bundle. Capture outputs should be regenerated into a new workspace folder on the target PC.
