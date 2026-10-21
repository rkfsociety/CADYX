# Local Revopoint camera calibration

[Русский](#русский) · [English](#english)

## Русский

Настройки разложены по профилям устройств: `scanners/<profile_id>/` содержит калибровку модели сканера, `tables/<profile_id>/` — карту и настройки модели стола. Физические настройки конкретных экземпляров хранятся отдельно в `%LOCALAPPDATA%\CADYX\scanner-calibration\<sha256-device-id>` и `%LOCALAPPDATA%\CADYX\turntable-settings\<sha256-device-id>`. При обнаружении ровно одного исходного Revopoint MINI CADYX использует профиль `scanners/revopoint-mini-y16/`. Для другого устройства получите его собственный `Q.bin` и масштаб; не используйте калибровку другого устройства.

Отслеживаемый `Q.bin` был скопирован из `%LOCALAPPDATA%\RevoScan5\cameraparam` на ПК с подключённым сканером; правообладатель и разрешение на распространение не установлены. Пользователь решил включить эти два файла в Git; удалённый `origin` публичный. Это решение не подтверждает права на распространение. В [пользовательском соглашении Revopoint](https://www.revopoint3d.com/pages/user-agreement) права на ПО и относящиеся к нему данные не переданы явно; разрешение распространять извлечённую калибровку не установлено.

Контакт сопровождающего для правообладателя по вопросам этих файлов: [romankuzminvital+cadyx@gmail.com](mailto:romankuzminvital+cadyx@gmail.com).

`scanners/revopoint-mini-y16/Q.bin` — матрица перепроецирования float32 little-endian размером 64 байта, которую `CaptureY16.exe` использует для расчёта луча камеры. Сначала значение uint16 преобразуется в кандидатную глубину по `depthScale` этого устройства, затем луч масштабируется так, чтобы глубина по камере Z совпадала с этим значением. Прямое соответствие глубины UVC всё ещё нужно проверить по известному физическому размеру.

`depth_scale.json` хранит `depth_scale_mm_per_raw_unit` (положительное число) и статус. Для встроенного MINI указан коэффициент `0.005`, считанный из журнала SDK Revo Scan; статус для прямого UVC остаётся предварительным. Если создаёте профиль другого устройства, держите его локально, пока право на распространение не подтверждено. Соответствие UVC и абсолютный масштаб требуют проверки.

Для команд требуется только `Q.bin` и `depth_scale.json`; прежние вспомогательные бинарные файлы не использовались этими командами. Каждый запуск должен использовать оба значения одного и того же устройства. Не выводите масштаб из матрицы Q и не меняйте его ради улучшения оценок перекрытия.

Локальный экспорт `tables/revopoint-dual-axis-table/framesmall.mkf`, намеренно исключённый из Git, является референсом маркеров стола для live-preview. Карта физического стола может переопределить его файлом `framesmall.mkf` в своём `%LOCALAPPDATA%\CADYX\turntable-settings\<sha256-device-id>`. Единицы координат и полная семантика записей карты остаются неизвестны; см. `tools/revopoint/parse_mkf.py` и руководство Revopoint. Настройка периода вращения каждого физического стола сохраняется рядом в `settings.json`.

Обычный workspace `projects/` по-прежнему игнорируется Git и не входит в набор исходников для переноса между ПК. На целевой машине создавайте результаты захвата в новой папке workspace.

## English

Settings are grouped by device profile: `scanners/<profile_id>/` contains scanner-model calibration, and `tables/<profile_id>/` contains the table marker map and defaults. Physical device settings live separately under `%LOCALAPPDATA%\CADYX\scanner-calibration\<sha256-device-id>` and `%LOCALAPPDATA%\CADYX\turntable-settings\<sha256-device-id>`. CADYX selects `scanners/revopoint-mini-y16/` automatically when exactly one original MINI is detected. Never use another scanner's calibration.

The tracked `Q.bin` was copied from `%LOCALAPPDATA%\RevoScan5\cameraparam` on the connected scanner PC. Redistribution rights remain unconfirmed. The user directed these two files to be included in Git, and the remote `origin` is public; that decision does not establish redistribution permission. The [Revopoint User Agreement](https://www.revopoint3d.com/pages/user-agreement) does not expressly grant rights to redistribute extracted device calibration data.

Maintainer contact for the rights holder's questions about these files: [romankuzminvital+cadyx@gmail.com](mailto:romankuzminvital+cadyx@gmail.com).

Local capture requires `scanners/revopoint-mini-y16/Q.bin`, a 64-byte little-endian float32 reprojection matrix used by `CaptureY16.exe` to obtain a camera ray. The uint16 sample is converted to candidate depth using this device's `depthScale`, then the ray is scaled so camera Z equals that depth. Direct UVC mapping still needs validation against a known physical dimension.

The bundled `depth_scale.json` records `0.005`, read from the installed Revo Scan SDK log, with a provisional status for direct UVC. Keep other device-specific calibration files local unless redistribution rights are confirmed. UVC correspondence and absolute scale remain provisional.

The commands require only `Q.bin` and `depth_scale.json`; the previously tracked auxiliary binary files were not used by these commands. Use both values from the same device. Do not infer the scale from Q or change it to improve overlap scores.

The local `tables/revopoint-dual-axis-table/framesmall.mkf` export, deliberately excluded from Git, is the live-preview marker reference for that table profile. An individual physical table can override it with `framesmall.mkf` in its `%LOCALAPPDATA%\CADYX\turntable-settings\<sha256-device-id>` folder. Map units and record semantics remain unknown; see `tools/revopoint/parse_mkf.py` and the Revopoint tools guide. Its per-table rotation period is stored in the same physical-device folder as `settings.json`.

The normal `projects/` workspace remains ignored and is not part of the cross-PC source bundle. Capture outputs should be regenerated into a new workspace folder on the target PC.
