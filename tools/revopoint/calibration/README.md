# Local Revopoint camera calibration

[Русский](#русский) · [English](#english)

## Русский

Калибровка привязана к конкретному сканеру и хранится только локально в этой папке. Файлы `*.bin` и `depth_scale.json` игнорируются Git. Получите `Q.bin` и значение масштаба из установки Revo Scan для своего устройства либо из источника, на распространение которого у вас есть права; скопируйте их сюда под именами, указанными ниже. Не используйте калибровку другого устройства.

Ранее отслеживаемые файлы были скопированы без изменений из `%LOCALAPPDATA%\RevoScan5\cameraparam` на ПК с подключённым сканером; правообладатель и разрешение на распространение не установлены. Поэтому сами файлы удалены из текущего Git-снимка. Их предыдущие версии остаются в уже опубликованной истории Git и требуют отдельного решения о переписывании истории.

В [пользовательском соглашении Revopoint](https://www.revopoint3d.com/pages/user-agreement) сказано, что интеллектуальные права распространяются на ПО и относящиеся к нему данные, а права, прямо не предоставленные соглашением, остаются у компании и требуют дополнительного письменного согласия (разделы 1 и 2.2). В соглашении не найдено явного разрешения распространять извлечённые device-specific файлы калибровки. Поэтому до письменного разъяснения право на публикацию набора не считается подтверждённым.

Для локального захвата необходим `Q.bin` — матрица перепроецирования float32 little-endian размером 64 байта, которую `CaptureY16.exe` использует для расчёта луча камеры. Сначала значение uint16 преобразуется в кандидатную глубину по `depthScale` этого устройства, затем луч масштабируется так, чтобы глубина по камере Z совпадала с этим значением. Прямое соответствие глубины UVC всё ещё нужно проверить по известному физическому размеру.

Создайте локальный `depth_scale.json` со свойствами `depth_scale_mm_per_raw_unit` (положительное число) и `status` (например, `provisional_for_direct_UVC`). Не добавляйте в Git значения, журналы или калибровочные данные, скопированные из SDK/ПО устройства. Соответствие UVC и абсолютный масштаб пока предварительны.

Для команд требуется только `Q.bin` и `depth_scale.json`; прежние вспомогательные бинарные файлы не использовались этими командами. Каждый запуск должен использовать оба значения одного и того же устройства. Не выводите масштаб из матрицы Q и не меняйте его ради улучшения оценок перекрытия.

Локальный экспорт `framesmall.mkf`, намеренно исключённый из Git, является отдельным предоставленным пользователем референсом маркеров стола. Единицы его координат и полная семантика записей неизвестны; см. `tools/revopoint/parse_mkf.py` и руководство Revopoint.

Обычный workspace `projects/` по-прежнему игнорируется Git и не входит в набор исходников для переноса между ПК. На целевой машине создавайте результаты захвата в новой папке workspace.

## English

Calibration is device-specific and belongs only in this local folder. Git ignores `*.bin` and `depth_scale.json`. Obtain `Q.bin` and the depth scale from Revo Scan installed for your own scanner, or from a source whose redistribution rights you hold, then place them here under the names below. Never use another device's calibration.

The formerly tracked files were copied unchanged from `%LOCALAPPDATA%\RevoScan5\cameraparam` on a PC connected to the scanner. The rights holder and permission to redistribute them were not established, so the data files have been removed from the current Git snapshot. Earlier versions remain in the already-published Git history; rewriting that history requires a separate decision.

The [Revopoint User Agreement](https://www.revopoint3d.com/pages/user-agreement) says intellectual-property rights cover the software and related data, and rights not expressly granted remain with the company and require additional written consent (sections 1 and 2.2). It does not expressly authorize redistribution of extracted device-specific calibration files. Redistribution permission is therefore not established absent written clarification.

Local capture requires `Q.bin`, a 64-byte little-endian float32 reprojection matrix used by `CaptureY16.exe` to obtain a camera ray. The uint16 sample is converted to candidate depth using this device's `depthScale`, then the ray is scaled so camera Z equals that depth. Direct UVC mapping still needs validation against a known physical dimension.

Create a local `depth_scale.json` with `depth_scale_mm_per_raw_unit` (a positive number) and `status` (for example, `provisional_for_direct_UVC`). Do not commit values, logs, or calibration data copied from the device SDK/software. UVC correspondence and absolute scale remain provisional.

The commands require only `Q.bin` and `depth_scale.json`; the previously tracked auxiliary binary files were not used by these commands. Use both values from the same device. Do not infer the scale from Q or change it to improve overlap scores.

The local `framesmall.mkf` export, deliberately excluded from Git, is a separate user-provided table-marker reference. Its coordinate units and complete record semantics remain unknown; see `tools/revopoint/parse_mkf.py` and the Revopoint tools guide.

The normal `projects/` workspace remains ignored and is not part of the cross-PC source bundle. Capture outputs should be regenerated into a new workspace folder on the target PC.
