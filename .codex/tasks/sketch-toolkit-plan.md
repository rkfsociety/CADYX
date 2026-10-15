# План инструментария эскизов CADYX

Источник: предоставленный список. В нём 357 пунктов; повторы сохранены как отдельные пользовательские инструменты и получают отдельные тест-кейсы.

## Цель и правила приёмки

- Реализовать семантику каждого пункта в CADYX, через существующие Core, Command System, модель данных, геометрическое ядро, Agent API и viewport по применимости. Браузер остаётся пассивным наблюдателем согласно `.codex/rules.md`; интерактивные инструменты должны иметь API-контракт и тестируемую модель событий/выбора, прежде чем появятся UI-контролы.
- Для каждого `SK-001`…`SK-357` провести отдельную позитивную и, где применимо, негативную проверку. Близкие команды могут использовать общую реализацию, но каждый пункт списка сохраняет свой отдельный тест и контракт параметров.
- После прохождения всех индивидуальных проверок запускать отдельные групповые сценарии: одна временная CADYX-проектная модель на функциональную группу, все инструменты этой группы последовательно в ней, с раздельными объектами/эскизами и проверкой результата после каждого шага.
- После прохождения всех групповых сценариев выполнить финальный интеграционный прогон всех 357 пунктов строго последовательно в одном временном CADYX-проекте; использовать раздельные эскизы/объекты и недеструктивные фикстуры, сохраняя один project id. Проверить историю, валидность документа, ссылки, профили, viewport-сцену и отсутствие незавершённых операций.
- Не объявлять инструмент готовым, если его имя только зарегистрировано: результат должен менять/анализировать документ согласно контракту или давать проверяемое состояние UI-инструмента. Неподдерживаемые семантики остаются `pending` с конкретной причиной и не проходят ворота следующей стадии.

## Этапы и ворота

1. **Инвентаризация и контракты:** определить для каждого пункта тип результата, параметры, поддержку XY/XZ/YZ/3D, ссылочную семантику, ограничения и представление в документе. Подготовить миграционный и kernel/API/UI план без изменения данных пользователя.
2. **Базовая модель:** развивать геометрические типы/параметризацию, constraints/solver, dimensions/expressions, операции редактирования, выбор/привязки, reference geometry, импорт и 3D по функциональным границам; каждый этап включает отдельные тесты каждого затронутого пункта.
3. **Индивидуальная проверка:** пока не пройдут все тесты `SK-001`…`SK-357`, групповые тесты не начинать. Обновить колонку `Индивидуальный тест` фактическим именем теста/результатом.
4. **Групповые проектные проверки:** для групп G01–G12 создать отдельные временные проекты; прогнать полный набор инструментов группы в одном проекте группы и записать результаты. При сбое исправить первопричину и повторить группу.
5. **Общий проект:** одним последовательным сценарием применить все 357 пунктов в одном новом временном CADYX-проекте; записать единый project id/fixture label и результат для каждого инструмента. При сбое этап не завершён.
6. **Финальный аудит и доставка:** проверить compatibility/migrations, итоговые тесты, полный diff, `.codex` память и документацию; выполнить отдельный task-only commit. Push только по прямому запросу.

## Групповые тесты

- [G01] Построение 2D-геометрии и кривых — инструменты SK-001…SK-063; статус: pending; проектный сценарий: pending.
- [G02] Ограничения и размеры 2D — инструменты SK-064…SK-125; статус: pending; проектный сценарий: pending.
- [G03] Редактирование, топология и преобразования — инструменты SK-126…SK-172; статус: pending; проектный сценарий: pending.
- [G04] Внешняя и проецируемая геометрия — инструменты SK-173…SK-188; статус: pending; проектный сценарий: pending.
- [G05] Вспомогательная геометрия, системы координат и поверхности — инструменты SK-189…SK-221; статус: pending; проектный сценарий: pending.
- [G06] Профили и диагностика — инструменты SK-222…SK-245; статус: pending; проектный сценарий: pending.
- [G07] Автоматические ограничения и распознавание — инструменты SK-246…SK-264; статус: pending; проектный сценарий: pending.
- [G08] Выбор, удаление и видимость — инструменты SK-265…SK-282; статус: pending; проектный сценарий: pending.
- [G09] Импорт и параметрические кривые — инструменты SK-283…SK-296; статус: pending; проектный сценарий: pending.
- [G10] 3D-эскиз и 3D-ограничения — инструменты SK-297…SK-313; статус: pending; проектный сценарий: pending.
- [G11] Управление ограничениями, solver, параметры и история — инструменты SK-314…SK-340; статус: pending; проектный сценарий: pending.
- [G12] Сетка, привязки и динамический ввод — инструменты SK-341…SK-357; статус: pending; проектный сценарий: pending.

## Реестр отдельных проверок

`pending` означает, что проверка ещё не выполнена; статус меняется только по результату реального запуска. Имена исходного списка и повторы сохранены.

| ID | Инструмент | Индивидуальный тест | Тест группы | Общий проект |
|---|---|---|---|---|
| SK-001 | Точка | pending | pending | pending |
| SK-002 | Вспомогательная точка | pending | pending | pending |
| SK-003 | Отрезок | pending | pending | pending |
| SK-004 | Линия | pending | pending | pending |
| SK-005 | Бесконечная линия | pending | pending | pending |
| SK-006 | Осевая линия | pending | pending | pending |
| SK-007 | Вспомогательная линия | pending | pending | pending |
| SK-008 | Ломаная | pending | pending | pending |
| SK-009 | Цепочка линий | pending | pending | pending |
| SK-010 | Прямоугольник по двум углам | pending | pending | pending |
| SK-011 | Прямоугольник из центра | pending | pending | pending |
| SK-012 | Прямоугольник по трём точкам | pending | pending | pending |
| SK-013 | Повернутый прямоугольник | pending | pending | pending |
| SK-014 | Параллелограмм | pending | pending | pending |
| SK-015 | Скруглённый прямоугольник | pending | pending | pending |
| SK-016 | Окружность по центру и радиусу | pending | pending | pending |
| SK-017 | Окружность по центру и диаметру | pending | pending | pending |
| SK-018 | Окружность по двум точкам | pending | pending | pending |
| SK-019 | Окружность по трём точкам | pending | pending | pending |
| SK-020 | Касательная окружность | pending | pending | pending |
| SK-021 | Окружность по двум касательным | pending | pending | pending |
| SK-022 | Окружность по трём касательным | pending | pending | pending |
| SK-023 | Дуга по центру | pending | pending | pending |
| SK-024 | Дуга по трём точкам | pending | pending | pending |
| SK-025 | Касательная дуга | pending | pending | pending |
| SK-026 | Дуга по началу, центру и концу | pending | pending | pending |
| SK-027 | Дуга по началу, концу и радиусу | pending | pending | pending |
| SK-028 | Эллипс | pending | pending | pending |
| SK-029 | Эллипс из центра | pending | pending | pending |
| SK-030 | Эллипс по трём точкам | pending | pending | pending |
| SK-031 | Эллиптическая дуга | pending | pending | pending |
| SK-032 | Сплайн по точкам | pending | pending | pending |
| SK-033 | Сплайн по контрольным точкам | pending | pending | pending |
| SK-034 | B-сплайн | pending | pending | pending |
| SK-035 | Кривая Безье | pending | pending | pending |
| SK-036 | NURBS-кривая | pending | pending | pending |
| SK-037 | Интерполяционный сплайн | pending | pending | pending |
| SK-038 | Замкнутый сплайн | pending | pending | pending |
| SK-039 | Парабола | pending | pending | pending |
| SK-040 | Гипербола | pending | pending | pending |
| SK-041 | Коническая кривая | pending | pending | pending |
| SK-042 | Коническая дуга | pending | pending | pending |
| SK-043 | Правильный многоугольник | pending | pending | pending |
| SK-044 | Вписанный многоугольник | pending | pending | pending |
| SK-045 | Описанный многоугольник | pending | pending | pending |
| SK-046 | Многоугольник из центра | pending | pending | pending |
| SK-047 | Прямой паз | pending | pending | pending |
| SK-048 | Паз по центрам | pending | pending | pending |
| SK-049 | Паз по общей длине | pending | pending | pending |
| SK-050 | Паз от центральной точки | pending | pending | pending |
| SK-051 | Дуговой паз по трём точкам | pending | pending | pending |
| SK-052 | Дуговой паз из центра | pending | pending | pending |
| SK-053 | Касательный дуговой паз | pending | pending | pending |
| SK-054 | Спираль | pending | pending | pending |
| SK-055 | Уравнительная кривая | pending | pending | pending |
| SK-056 | Параметрическая кривая | pending | pending | pending |
| SK-057 | Кривая по координатам | pending | pending | pending |
| SK-058 | Текст | pending | pending | pending |
| SK-059 | Многострочный текст | pending | pending | pending |
| SK-060 | Текст по линии | pending | pending | pending |
| SK-061 | Текст по дуге | pending | pending | pending |
| SK-062 | Текст по кривой | pending | pending | pending |
| SK-063 | Разбить текст на кривые | pending | pending | pending |
| SK-064 | Совпадение | pending | pending | pending |
| SK-065 | Точка на объекте | pending | pending | pending |
| SK-066 | Фиксация | pending | pending | pending |
| SK-067 | Блокировка геометрии | pending | pending | pending |
| SK-068 | Горизонтальность | pending | pending | pending |
| SK-069 | Вертикальность | pending | pending | pending |
| SK-070 | Параллельность | pending | pending | pending |
| SK-071 | Перпендикулярность | pending | pending | pending |
| SK-072 | Коллинеарность | pending | pending | pending |
| SK-073 | Касательность | pending | pending | pending |
| SK-074 | Концентричность | pending | pending | pending |
| SK-075 | Равенство радиусов | pending | pending | pending |
| SK-076 | Равенство диаметров | pending | pending | pending |
| SK-077 | Равенство длин | pending | pending | pending |
| SK-078 | Равенство геометрии | pending | pending | pending |
| SK-079 | Симметрия | pending | pending | pending |
| SK-080 | Средняя точка | pending | pending | pending |
| SK-081 | Центр | pending | pending | pending |
| SK-082 | Совпадение с осевой линией | pending | pending | pending |
| SK-083 | Плавность | pending | pending | pending |
| SK-084 | Непрерывность G0 | pending | pending | pending |
| SK-085 | Непрерывность G1 | pending | pending | pending |
| SK-086 | Непрерывность G2 | pending | pending | pending |
| SK-087 | Pierce | pending | pending | pending |
| SK-088 | Пересечение | pending | pending | pending |
| SK-089 | Нормальность | pending | pending | pending |
| SK-090 | Одинаковое положение | pending | pending | pending |
| SK-091 | Одинаковая ориентация | pending | pending | pending |
| SK-092 | Горизонтальное расстояние | pending | pending | pending |
| SK-093 | Вертикальное расстояние | pending | pending | pending |
| SK-094 | Наклонное расстояние | pending | pending | pending |
| SK-095 | Расстояние между точками | pending | pending | pending |
| SK-096 | Расстояние от точки до линии | pending | pending | pending |
| SK-097 | Длина линии | pending | pending | pending |
| SK-098 | Радиус | pending | pending | pending |
| SK-099 | Диаметр | pending | pending | pending |
| SK-100 | Угол между линиями | pending | pending | pending |
| SK-101 | Угол линии | pending | pending | pending |
| SK-102 | Угол дуги | pending | pending | pending |
| SK-103 | X-координата | pending | pending | pending |
| SK-104 | Y-координата | pending | pending | pending |
| SK-105 | Ординатный размер | pending | pending | pending |
| SK-106 | Базовый размер | pending | pending | pending |
| SK-107 | Длина дуги | pending | pending | pending |
| SK-108 | Длина сплайна | pending | pending | pending |
| SK-109 | Радиус кривизны | pending | pending | pending |
| SK-110 | Управляющий размер | pending | pending | pending |
| SK-111 | Справочный размер | pending | pending | pending |
| SK-112 | Зависимый размер | pending | pending | pending |
| SK-113 | Размер по формуле | pending | pending | pending |
| SK-114 | Размер по выражению | pending | pending | pending |
| SK-115 | Связанный размер | pending | pending | pending |
| SK-116 | Глобальная переменная | pending | pending | pending |
| SK-117 | Умный размер | pending | pending | pending |
| SK-118 | Быстрый размер | pending | pending | pending |
| SK-119 | Авторазмер | pending | pending | pending |
| SK-120 | Линейный размер | pending | pending | pending |
| SK-121 | Угловой размер | pending | pending | pending |
| SK-122 | Радиальный размер | pending | pending | pending |
| SK-123 | Диаметральный размер | pending | pending | pending |
| SK-124 | Ординатный размер | pending | pending | pending |
| SK-125 | Размер длины дуги | pending | pending | pending |
| SK-126 | Обрезка | pending | pending | pending |
| SK-127 | Быстрая обрезка | pending | pending | pending |
| SK-128 | Обрезка до ближайшего объекта | pending | pending | pending |
| SK-129 | Обрезка между пересечениями | pending | pending | pending |
| SK-130 | Продление | pending | pending | pending |
| SK-131 | Продление до ближайшего объекта | pending | pending | pending |
| SK-132 | Продление до выбранного объекта | pending | pending | pending |
| SK-133 | Разделить объект | pending | pending | pending |
| SK-134 | Разорвать объект | pending | pending | pending |
| SK-135 | Разрыв в точке | pending | pending | pending |
| SK-136 | Разрыв в пересечении | pending | pending | pending |
| SK-137 | Соединить объекты | pending | pending | pending |
| SK-138 | Объединить точки | pending | pending | pending |
| SK-139 | Объединить кривые | pending | pending | pending |
| SK-140 | Скругление | pending | pending | pending |
| SK-141 | Скругление угла | pending | pending | pending |
| SK-142 | Множественное скругление | pending | pending | pending |
| SK-143 | Фаска | pending | pending | pending |
| SK-144 | Равносторонняя фаска | pending | pending | pending |
| SK-145 | Фаска по двум расстояниям | pending | pending | pending |
| SK-146 | Фаска по расстоянию и углу | pending | pending | pending |
| SK-147 | Смещение | pending | pending | pending |
| SK-148 | Смещение цепочки | pending | pending | pending |
| SK-149 | Смещение контура | pending | pending | pending |
| SK-150 | Двустороннее смещение | pending | pending | pending |
| SK-151 | Переменное смещение | pending | pending | pending |
| SK-152 | Перемещение | pending | pending | pending |
| SK-153 | Копирование | pending | pending | pending |
| SK-154 | Перемещение с копированием | pending | pending | pending |
| SK-155 | Поворот | pending | pending | pending |
| SK-156 | Масштабирование | pending | pending | pending |
| SK-157 | Растяжение | pending | pending | pending |
| SK-158 | Выравнивание | pending | pending | pending |
| SK-159 | Линейный массив | pending | pending | pending |
| SK-160 | Прямоугольный массив | pending | pending | pending |
| SK-161 | Направленный массив | pending | pending | pending |
| SK-162 | Круговой массив | pending | pending | pending |
| SK-163 | Полярный массив | pending | pending | pending |
| SK-164 | Массив по кривой | pending | pending | pending |
| SK-165 | Массив по траектории | pending | pending | pending |
| SK-166 | Массив по сетке | pending | pending | pending |
| SK-167 | Массив по эскизу | pending | pending | pending |
| SK-168 | Зеркальный массив | pending | pending | pending |
| SK-169 | Зеркальное отражение | pending | pending | pending |
| SK-170 | Зеркальное отражение цепочки | pending | pending | pending |
| SK-171 | Зеркальное отражение относительно линии | pending | pending | pending |
| SK-172 | Динамическое зеркало | pending | pending | pending |
| SK-173 | Проецирование геометрии | pending | pending | pending |
| SK-174 | Проецирование ребра | pending | pending | pending |
| SK-175 | Проецирование точки | pending | pending | pending |
| SK-176 | Проецирование грани | pending | pending | pending |
| SK-177 | Проецирование силуэта | pending | pending | pending |
| SK-178 | Проецирование пересечения | pending | pending | pending |
| SK-179 | Преобразование объектов | pending | pending | pending |
| SK-180 | Использование ребра | pending | pending | pending |
| SK-181 | Внешняя геометрия | pending | pending | pending |
| SK-182 | Включение геометрии | pending | pending | pending |
| SK-183 | Пересечение грани плоскостью эскиза | pending | pending | pending |
| SK-184 | Кривая пересечения | pending | pending | pending |
| SK-185 | Поперечное сечение | pending | pending | pending |
| SK-186 | Проекция 3D-геометрии | pending | pending | pending |
| SK-187 | Проекция по нормали | pending | pending | pending |
| SK-188 | Проекция по вектору | pending | pending | pending |
| SK-189 | Вспомогательная окружность | pending | pending | pending |
| SK-190 | Вспомогательная дуга | pending | pending | pending |
| SK-191 | Вспомогательный прямоугольник | pending | pending | pending |
| SK-192 | Переключение обычной и вспомогательной геометрии | pending | pending | pending |
| SK-193 | Начало координат | pending | pending | pending |
| SK-194 | Ось X | pending | pending | pending |
| SK-195 | Ось Y | pending | pending | pending |
| SK-196 | Локальная система координат | pending | pending | pending |
| SK-197 | Опорная ось | pending | pending | pending |
| SK-198 | Опорная точка | pending | pending | pending |
| SK-199 | Плоскость эскиза | pending | pending | pending |
| SK-200 | Базовая плоскость | pending | pending | pending |
| SK-201 | Базовая ось | pending | pending | pending |
| SK-202 | Базовая точка | pending | pending | pending |
| SK-203 | Переориентация эскиза | pending | pending | pending |
| SK-204 | Смена плоскости эскиза | pending | pending | pending |
| SK-205 | Привязка эскиза к грани | pending | pending | pending |
| SK-206 | Отвязка эскиза | pending | pending | pending |
| SK-207 | Эскиз на плоскости XY | pending | pending | pending |
| SK-208 | Эскиз на плоскости XZ | pending | pending | pending |
| SK-209 | Эскиз на плоскости YZ | pending | pending | pending |
| SK-210 | Эскиз на плоской грани | pending | pending | pending |
| SK-211 | Эскиз на базовой плоскости | pending | pending | pending |
| SK-212 | Эскиз на смещённой плоскости | pending | pending | pending |
| SK-213 | Эскиз на наклонной плоскости | pending | pending | pending |
| SK-214 | Эскиз на касательной плоскости | pending | pending | pending |
| SK-215 | Эскиз на поверхности | pending | pending | pending |
| SK-216 | Оборачивание эскиза по поверхности | pending | pending | pending |
| SK-217 | Проекция эскиза на поверхность | pending | pending | pending |
| SK-218 | Геодезическая кривая | pending | pending | pending |
| SK-219 | Кривая на поверхности | pending | pending | pending |
| SK-220 | UV-кривая | pending | pending | pending |
| SK-221 | Сплайн на поверхности | pending | pending | pending |
| SK-222 | Определение замкнутого профиля | pending | pending | pending |
| SK-223 | Определение открытого профиля | pending | pending | pending |
| SK-224 | Выбор профиля | pending | pending | pending |
| SK-225 | Выбор области | pending | pending | pending |
| SK-226 | Вложенный профиль | pending | pending | pending |
| SK-227 | Внешний контур | pending | pending | pending |
| SK-228 | Внутренний контур | pending | pending | pending |
| SK-229 | Множественные области | pending | pending | pending |
| SK-230 | Показ степеней свободы | pending | pending | pending |
| SK-231 | Поиск недоопределённой геометрии | pending | pending | pending |
| SK-232 | Поиск полностью определённой геометрии | pending | pending | pending |
| SK-233 | Поиск переопределённой геометрии | pending | pending | pending |
| SK-234 | Поиск конфликтующих ограничений | pending | pending | pending |
| SK-235 | Поиск избыточных ограничений | pending | pending | pending |
| SK-236 | Поиск открытых контуров | pending | pending | pending |
| SK-237 | Поиск разрывов | pending | pending | pending |
| SK-238 | Поиск наложений | pending | pending | pending |
| SK-239 | Поиск дубликатов | pending | pending | pending |
| SK-240 | Поиск самопересечений | pending | pending | pending |
| SK-241 | Поиск слишком коротких объектов | pending | pending | pending |
| SK-242 | Проверка эскиза | pending | pending | pending |
| SK-243 | Восстановление эскиза | pending | pending | pending |
| SK-244 | Автоматическое исправление эскиза | pending | pending | pending |
| SK-245 | Анализ профилей | pending | pending | pending |
| SK-246 | Автогоризонтальность | pending | pending | pending |
| SK-247 | Автовертикальность | pending | pending | pending |
| SK-248 | Автосовпадение | pending | pending | pending |
| SK-249 | Автокасательность | pending | pending | pending |
| SK-250 | Автоперпендикулярность | pending | pending | pending |
| SK-251 | Автопараллельность | pending | pending | pending |
| SK-252 | Автосредняя точка | pending | pending | pending |
| SK-253 | Автоконцентричность | pending | pending | pending |
| SK-254 | Авторавенство | pending | pending | pending |
| SK-255 | Автоматическое наложение ограничений | pending | pending | pending |
| SK-256 | Автоматическое нанесение размеров | pending | pending | pending |
| SK-257 | Автоматическое замыкание профиля | pending | pending | pending |
| SK-258 | Автоматическое удаление дубликатов | pending | pending | pending |
| SK-259 | Автоматическое объединение объектов | pending | pending | pending |
| SK-260 | Автоматическое упрощение геометрии | pending | pending | pending |
| SK-261 | Автоматическое определение симметрии | pending | pending | pending |
| SK-262 | Автоматическое определение массивов | pending | pending | pending |
| SK-263 | Автоматическое определение касательности | pending | pending | pending |
| SK-264 | Автоматическое определение равной геометрии | pending | pending | pending |
| SK-265 | Выбор объекта | pending | pending | pending |
| SK-266 | Выбор рамкой | pending | pending | pending |
| SK-267 | Пересекающий выбор рамкой | pending | pending | pending |
| SK-268 | Выбор лассо | pending | pending | pending |
| SK-269 | Выбор цепочки | pending | pending | pending |
| SK-270 | Выбор контура | pending | pending | pending |
| SK-271 | Выбор связанной геометрии | pending | pending | pending |
| SK-272 | Выбор похожих объектов | pending | pending | pending |
| SK-273 | Выбор вспомогательной геометрии | pending | pending | pending |
| SK-274 | Выбор ограничений | pending | pending | pending |
| SK-275 | Выбор профилей | pending | pending | pending |
| SK-276 | Удаление объекта | pending | pending | pending |
| SK-277 | Удаление ограничения | pending | pending | pending |
| SK-278 | Удаление размера | pending | pending | pending |
| SK-279 | Подавление объекта | pending | pending | pending |
| SK-280 | Подавление ограничения | pending | pending | pending |
| SK-281 | Скрытие геометрии | pending | pending | pending |
| SK-282 | Отображение геометрии | pending | pending | pending |
| SK-283 | Импорт DXF | pending | pending | pending |
| SK-284 | Импорт DWG | pending | pending | pending |
| SK-285 | Импорт SVG | pending | pending | pending |
| SK-286 | Импорт CSV-точек | pending | pending | pending |
| SK-287 | Импорт координат | pending | pending | pending |
| SK-288 | Вставка векторной геометрии | pending | pending | pending |
| SK-289 | Трассировка геометрии | pending | pending | pending |
| SK-290 | Кривая по уравнению | pending | pending | pending |
| SK-291 | Кривая по закону | pending | pending | pending |
| SK-292 | Кривая через XYZ-точки | pending | pending | pending |
| SK-293 | Полиномиальная кривая | pending | pending | pending |
| SK-294 | Кривая перехода | pending | pending | pending |
| SK-295 | Смешанная кривая | pending | pending | pending |
| SK-296 | Кривая с управлением кривизной | pending | pending | pending |
| SK-297 | 3D-точка | pending | pending | pending |
| SK-298 | 3D-линия | pending | pending | pending |
| SK-299 | 3D-ломаная | pending | pending | pending |
| SK-300 | 3D-дуга | pending | pending | pending |
| SK-301 | 3D-сплайн | pending | pending | pending |
| SK-302 | 3D-совпадение | pending | pending | pending |
| SK-303 | 3D-параллельность | pending | pending | pending |
| SK-304 | 3D-перпендикулярность | pending | pending | pending |
| SK-305 | Ограничение вдоль X | pending | pending | pending |
| SK-306 | Ограничение вдоль Y | pending | pending | pending |
| SK-307 | Ограничение вдоль Z | pending | pending | pending |
| SK-308 | Ограничение на плоскости | pending | pending | pending |
| SK-309 | Ограничение на грани | pending | pending | pending |
| SK-310 | Ограничение на кривой | pending | pending | pending |
| SK-311 | 3D-расстояние | pending | pending | pending |
| SK-312 | Расстояние XYZ | pending | pending | pending |
| SK-313 | 3D-угол | pending | pending | pending |
| SK-314 | Добавление ограничения | pending | pending | pending |
| SK-315 | Удаление ограничения | pending | pending | pending |
| SK-316 | Редактирование ограничения | pending | pending | pending |
| SK-317 | Подавление ограничения | pending | pending | pending |
| SK-318 | Добавление размера | pending | pending | pending |
| SK-319 | Редактирование размера | pending | pending | pending |
| SK-320 | Удаление размера | pending | pending | pending |
| SK-321 | Фиксация объекта | pending | pending | pending |
| SK-322 | Снятие фиксации | pending | pending | pending |
| SK-323 | Решение эскиза | pending | pending | pending |
| SK-324 | Пересчёт эскиза | pending | pending | pending |
| SK-325 | Проверка степеней свободы | pending | pending | pending |
| SK-326 | Получение конфликтов | pending | pending | pending |
| SK-327 | Получение профилей | pending | pending | pending |
| SK-328 | Получение открытых контуров | pending | pending | pending |
| SK-329 | Получение ограничений | pending | pending | pending |
| SK-330 | Проверка валидности эскиза | pending | pending | pending |
| SK-331 | Проверка геометрии | pending | pending | pending |
| SK-332 | Выражения | pending | pending | pending |
| SK-333 | Формулы | pending | pending | pending |
| SK-334 | Параметры | pending | pending | pending |
| SK-335 | Глобальные параметры | pending | pending | pending |
| SK-336 | Пользовательские переменные | pending | pending | pending |
| SK-337 | Единицы измерения | pending | pending | pending |
| SK-338 | История операций | pending | pending | pending |
| SK-339 | Отмена операции | pending | pending | pending |
| SK-340 | Повтор операции | pending | pending | pending |
| SK-341 | Привязка к сетке | pending | pending | pending |
| SK-342 | Отображение сетки | pending | pending | pending |
| SK-343 | Настройка шага сетки | pending | pending | pending |
| SK-344 | Объектные привязки | pending | pending | pending |
| SK-345 | Привязка к концам | pending | pending | pending |
| SK-346 | Привязка к середине | pending | pending | pending |
| SK-347 | Привязка к центру | pending | pending | pending |
| SK-348 | Привязка к пересечению | pending | pending | pending |
| SK-349 | Привязка к касательной | pending | pending | pending |
| SK-350 | Привязка к перпендикуляру | pending | pending | pending |
| SK-351 | Привязка к ближайшей точке | pending | pending | pending |
| SK-352 | Привязка к квадранту | pending | pending | pending |
| SK-353 | Привязка к сетке | pending | pending | pending |
| SK-354 | Динамический ввод | pending | pending | pending |
| SK-355 | Предпросмотр ограничений | pending | pending | pending |
| SK-356 | Предпросмотр размеров | pending | pending | pending |
| SK-357 | Предпросмотр профилей | pending | pending | pending |

## Примечания о текущей реализации

- Уже реализованные исходные инструменты также проходят индивидуальный, групповой и общий тесты; наличие прежних тестов засчитывается только после явного сопоставления с реестром.
- Схемные миграции, поддерживаемые типы, API, UI и dependency choices устанавливаются из фактического кода и официальных источников; эта таблица не является обещанием допустимости неточной/mesh-only подмены точных кривых или B-rep-операций.
- Временные проекты и исходные файлы интеграционных фикстур хранятся в изолированной временной workspace; проекты пользователя и их файлы не используются/не перезаписываются.
