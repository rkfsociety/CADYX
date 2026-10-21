# Local Revopoint camera calibration

[Русский](#русский) · [English](#english)

## Русский

Для подключённого исходного Revopoint MINI репозиторий содержит `Q.bin` и `depth_scale.json`. При обнаружении ровно одного сканера этого профиля CADYX автоматически выбирает встроенную калибровку. Для другого устройства получите его собственный `Q.bin` и масштаб из установки Revo Scan либо из источника, на распространение которого у вас есть права; дополнительные калибровочные файлы остаются локальными и игнорируются Git. Не используйте калибровку другого устройства.

Отслеживаемый `Q.bin` был скопирован из `%LOCALAPPDATA%\RevoScan5\cameraparam` на ПК с подключённым сканером; правообладатель и разрешение на распространение не установлены. Пользователь решил включить эти два файла в Git; удалённый `origin` публичный. Это решение не подтверждает права на распространение. В [пользовательском соглашении Revopoint](https://www.revopoint3d.com/pages/user-agreement) права на ПО и относящиеся к нему данные не переданы явно; разрешение распространять извлечённую калибровку не установлено.

`Q.bin` — матрица перепроецирования float32 little-endian размером 64 байта, которую `CaptureY16.exe` использует для расчёта луча камеры. Сначала значение uint16 преобразуется в кандидатную глубину по `depthScale` этого устройства, затем луч масштабируется так, чтобы глубина по камере Z совпадала с этим значением. Прямое соответствие глубины UVC всё ещё нужно проверить по известному физическому размеру.

`depth_scale.json` хранит `depth_scale_mm_per_raw_unit` (положительное число) и статус. Для встроенного MINI указан коэффициент `0.005`, считанный из журнала SDK Revo Scan; статус для прямого UVC остаётся предварительным. Если создаёте профиль другого устройства, держите его локально, пока право на распространение не подтверждено. Соответствие UVC и абсолютный масштаб требуют проверки.

Для команд требуется только `Q.bin` и `depth_scale.json`; прежние вспомогательные бинарные файлы не использовались этими командами. Каждый запуск должен использовать оба значения одного и того же устройства. Не выводите масштаб из матрицы Q и не меняйте его ради улучшения оценок перекрытия.

Локальный экспорт `framesmall.mkf`, намеренно исключённый из Git, является отдельным предоставленным пользователем референсом маркеров стола. Единицы его координат и полная семантика записей неизвестны; см. `tools/revopoint/parse_mkf.py` и руководство Revopoint.

Обычный workspace `projects/` по-прежнему игнорируется Git и не входит в набор исходников для переноса между ПК. На целевой машине создавайте результаты захвата в новой папке workspace.

## English

The repository tracks `Q.bin` and `depth_scale.json` for the connected original Revopoint MINI. CADYX selects this bundled calibration automatically when exactly one scanner of that profile is detected. For another device, obtain its own calibration from Revo Scan or a source whose redistribution rights you hold; auxiliary device files remain local and ignored by Git. Never use another device's calibration.

The tracked `Q.bin` was copied from `%LOCALAPPDATA%\RevoScan5\cameraparam` on the connected scanner PC. Redistribution rights remain unconfirmed. The user directed these two files to be included in Git, and the remote `origin` is public; that decision does not establish redistribution permission. The [Revopoint User Agreement](https://www.revopoint3d.com/pages/user-agreement) does not expressly grant rights to redistribute extracted device calibration data.

Local capture requires `Q.bin`, a 64-byte little-endian float32 reprojection matrix used by `CaptureY16.exe` to obtain a camera ray. The uint16 sample is converted to candidate depth using this device's `depthScale`, then the ray is scaled so camera Z equals that depth. Direct UVC mapping still needs validation against a known physical dimension.

The bundled `depth_scale.json` records `0.005`, read from the installed Revo Scan SDK log, with a provisional status for direct UVC. Keep other device-specific calibration files local unless redistribution rights are confirmed. UVC correspondence and absolute scale remain provisional.

The commands require only `Q.bin` and `depth_scale.json`; the previously tracked auxiliary binary files were not used by these commands. Use both values from the same device. Do not infer the scale from Q or change it to improve overlap scores.

The local `framesmall.mkf` export, deliberately excluded from Git, is a separate user-provided table-marker reference. Its coordinate units and complete record semantics remain unknown; see `tools/revopoint/parse_mkf.py` and the Revopoint tools guide.

The normal `projects/` workspace remains ignored and is not part of the cross-PC source bundle. Capture outputs should be regenerated into a new workspace folder on the target PC.
