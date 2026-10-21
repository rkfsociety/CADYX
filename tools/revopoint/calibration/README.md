# Revopoint camera calibration bundle

[Русский обзор](#русский-обзор) · [English reference](#english-reference)

## Русский обзор

### Калибровочные данные камеры Revopoint

Набор скопирован без изменения из `%LOCALAPPDATA%\RevoScan5\cameraparam` конкретного ПК со сканером. Наличие в приватном Git не подтверждает права на публичное распространение. `Q.bin` — матрица float32 little-endian размером 64 байта; `Pl.bin` и `Prgb.bin` содержат размеры 640×400 и матрицу 3×3. Остальные бинарные схемы установлены не полностью. `depth_scale.json` хранит коэффициент 0.005 из SDK с предварительным статусом для прямого UVC. Не меняйте его ради улучшения совмещения и не подменяйте калибровкой другого устройства. Масштаб требует проверки известным размером. Пользовательский `framesmall.mkf` и содержимое `projects/` не включены в Git.

Это обзор, не полный перевод. Полные схемы, примеры и подробности контракта приведены на английском ниже.

## English reference

This device-specific bundle was copied as-is from `%LOCALAPPDATA%\RevoScan5\cameraparam` on the connected scanner PC. It lets the repository's UVC capture and offline marker-calibration work continue from another checkout without finding that PC's AppData folder or launching Revo Scan.

The files originate from scanner-specific Revo Scan installation data. Their presence in this private repository does **not** establish permission to redistribute them publicly. Keep the bundle out of a public release until its provenance and redistribution rights are reviewed. If those rights cannot be confirmed, replace the tracked bundle with a documented, user-supplied local calibration workflow before publication.

`Q.bin` is the 64-byte little-endian float32 reprojection matrix used by `CaptureY16.exe` to obtain a camera ray. The uint16 sample is first converted to candidate depth with the `depthScale` read from Revo Scan, then the ray is scaled so camera Z equals that depth. This direct UVC depth mapping still needs validation against a known physical dimension. `Pl.bin` and `Prgb.bin` each contain 640×400 dimensions followed by a 3×3 float32 matrix. The remaining files are preserved calibration inputs for ongoing camera/marker research; their binary schemas are not fully established. Files are committed byte-for-byte and should not be edited or replaced with calibration from another scanner.

`depth_scale.json` records the installed Revo Scan SDK's `0.005` depth scale and its provisional status for direct UVC frames. Keep the value together with this scanner's calibration bundle; do not infer it from the Q matrix or change it to improve overlap scores.

The local `framesmall.mkf` export, deliberately excluded from Git, is a separate user-provided table-marker reference. Its coordinate units and complete record semantics remain unknown; see `tools/revopoint/parse_mkf.py` and the Revopoint tools guide.

The normal `projects/` workspace remains ignored and is not part of the cross-PC source bundle. Capture outputs should be regenerated into a new workspace folder on the target PC.
