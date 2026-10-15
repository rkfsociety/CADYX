# План инструментария эскизов CADYX

Источник: предоставленный список. В нём 357 пунктов; повторы сохранены как отдельные пользовательские инструменты и получают отдельные тест-кейсы.

## Цель и правила приёмки

- Реализовать семантику каждого пункта в CADYX, через существующие Core, Command System, модель данных, геометрическое ядро, Agent API и viewport по применимости. Браузер остаётся пассивным наблюдателем согласно `.codex/rules.md`; интерактивные инструменты должны иметь API-контракт и тестируемую модель событий/выбора, прежде чем появятся UI-контролы.
- Каждый инструмент сначала получает отдельную unit/API-проверку поведения; одиночную видимую CAD-модель для каждого инструмента не создавать. По решению пользователя, визуальные проверки откладываются до групповых стресс-проектов и полного интеграционного проекта, где пользователь сможет наблюдать последовательность операций в панели.
- После прохождения всех индивидуальных видимых прогонов запускать групповые сценарии: отдельный видимый CADYX-проект на каждую функциональную группу, все инструменты группы последовательно в одной проектной модели с раздельными объектами/эскизами и проверкой результата после каждого шага.
- После прохождения всех групповых сценариев выполнить финальный интеграционный прогон всех 357 пунктов строго последовательно в одном видимом CADYX-проекте; использовать раздельные эскизы/объекты и недеструктивные фикстуры, сохраняя один project id. Проверить историю, валидность документа, ссылки, профили, viewport-сцену и отсутствие незавершённых операций.
- Не объявлять инструмент готовым, если его имя только зарегистрировано: результат должен менять/анализировать документ согласно контракту или давать проверяемое состояние UI-инструмента. Неподдерживаемые семантики остаются `pending` с конкретной причиной и не проходят ворота следующей стадии.

## Этапы и ворота

1. **Инвентаризация и контракты:** определить для каждого пункта тип результата, параметры, поддержку XY/XZ/YZ/3D, ссылочную семантику, ограничения и представление в документе. Подготовить миграционный и kernel/API/UI план без изменения данных пользователя.
2. **Базовая модель:** развивать геометрические типы/параметризацию, constraints/solver, dimensions/expressions, операции редактирования, выбор/привязки, reference geometry, импорт и 3D по функциональным границам; каждый этап включает отдельные тесты каждого затронутого пункта.
3. **Индивидуальные проверки реализации:** каждый пункт отдельно закрывается адресным unit/API-тестом на корректный результат и отклонение некорректных входов; отдельный проект и визуализация для этого не требуются.
4. **Групповые модельные стресс-проверки:** для групп G01–G12 создать отдельные именованные CADYX-проекты; последовательно выполнить все реализованные инструменты группы в общей модели с видимым ходом работ и проверкой результата после каждого шага. При сбое исправить первопричину и повторить затронутый сценарий.
5. **Общий интеграционный стресс-проект:** одним последовательным сценарием применить все 357 пунктов в одном новом видимом CADYX-проекте; записать единый project id и результат для каждого инструмента. При сбое исправить первопричину и повторить затронутые проверки.
6. **Финальный аудит и доставка:** проверить compatibility/migrations, итоговые тесты, полный diff, `.codex` память и документацию; выполнить отдельный task-only commit. Push только по прямому запросу.

**Текущий прогресс:** SK-001…SK-029 имеют отдельные unit/API-проверки; групповые и финальный интеграционный стресс-проекты не начинались. Для первых 27 пунктов ранее создано 31 видимых spot-check прогон (справочные проверки, не отдельный gate). Документ переведён на схему v5 для точных эллипсов; их визуальная проверка остаётся в отложенных групповых стресс-проектах.

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

## Видимые проектные прогоны

- Видимый checkpoint SK-010…SK-019 и SK-043: проект `Эскизы — индивидуальные видимые тесты`, ID `6865cd10-79be-4af2-9e58-6b9495d02c42`; команды завершены на revision 2, 4, 6, 8, 10, 12, 14, 16, 18 и 20.
- Видимый checkpoint SK-001…SK-009: проект `Эскизы — базовые инструменты SK-001—SK-009`, ID `f31862d2-81fc-4293-9bab-67befe312c45`; команды завершены на revision 2, 4, 6, 8, 10, 12, 14, 16 и 18, каждая в отдельном именованном XY-эскизе.
- Видимый checkpoint SK-044…SK-045: проект `Эскизы — индивидуальные видимые тесты SK-044—SK-045`, ID `5be80ab4-e3f7-47ee-94cc-7c818b6186ee`; отдельные именованные XY-эскизы, вписанный шестиугольник завершён на revision 2, описанный квадрат — на revision 4; сцена PNG просмотрена, lock `FREE`, активности завершены.
- Видимый индивидуальный прогон SK-046: проект `Эскизы — индивидуальный видимый тест SK-046`, ID `47cbd83e-d459-4924-b7bf-74f2caf46621`; пятиугольник из центра `(0,0)` и первой вершины `(6,8)`, 5 сторон, замкнут; revision 2, сцена PNG просмотрена, lock `FREE`, активных операций нет.
- Видимый индивидуальный прогон SK-015: проект `Эскизы — индивидуальный видимый тест SK-015`, ID `19c00d98-d2b6-471c-8822-640aa0e77aea`; XY-профиль 50×30 с радиусом углов 5, четыре круговые дуги и четыре прямые стороны, результат замкнут; revision 2, габариты Agent API `[0,0,0]`…`[50,30,0]`, изображение просмотрено, lock `FREE`, активных операций нет.
- Видимый индивидуальный прогон SK-020: проект `Эскизы — индивидуальный видимый тест SK-020`, ID `eb4726d6-bbb6-4099-881e-fb8e6e317b4a`; пользователь уточнил семантику: окружность касается отрезка в заданной точке, радиус задаётся явно. Для отрезка `(0,0)`—`(20,0)` создан круг радиуса 5 с центром `(10,5)`, касание в `(10,0)` подтверждено документом и сценой; revision 3, PNG просмотрен, dashboard 100%, lock `FREE`, активных операций нет.
- Видимый индивидуальный прогон SK-021: проект `Эскизы — индивидуальный видимый тест SK-021`, ID `78bbaf2a-88e2-4059-b88e-8ae7bfbb2c39`; по двум конечным перпендикулярным отрезкам построен круг радиуса 2 с центром `(-2,2)`, точки касания `(-2,0)` и `(0,2)` находятся на соответствующих отрезках; revision 4, PNG просмотрен, dashboard 100%, lock `FREE`, активных операций нет.
- Видимый индивидуальный прогон SK-022: проект `Эскизы — индивидуальный видимый тест SK-022`, ID `ec553c46-0766-4584-b23e-fedbf9034cef`; по трём конечным касательным сегментам построен круг с центром `(-2,2)`, радиусом 2 и точками касания `(-2,0)`, `(0,2)`, `(-3.4142135624,0.5857864376)`; revision 5, сцена и PNG просмотрены, dashboard 100%, lock `FREE`, все операции завершены.
- Видимый индивидуальный прогон SK-023: проект `Эскизы — индивидуальный видимый тест SK-023`, ID `d7ad465c-992c-48ac-b5a4-c83fc1774be3`; дуга центра `(0,0)`, радиуса 10, от `(10,0)` до `(0,10)` с противочасовой четвертью; revision 2, сцена API имеет границы `[0,0,0]`…`[10,10,0]`, PNG просмотрен, dashboard 100%, lock `FREE`, активных операций нет.
- Видимый индивидуальный прогон SK-024: проект `Эскизы — индивидуальный видимый тест SK-024`, ID `9f9e40e3-79c4-40c5-94f0-ceba74bbe4b2`; точная полуокружность радиуса 10 из `(10,0)` через `(0,-10)` в `(-10,0)`, центр `(0,0)`, sweep `-π`; revision 2, scene bounds `[-10,-10,0]`…`[10,0,0]`, PNG просмотрен, dashboard 100%, lock `FREE`, активных операций нет.
- Видимый индивидуальный прогон SK-025: проект `Эскизы — индивидуальный видимый тест SK-025`, ID `e8999c6d-05a5-4ffa-86ec-45d0092683b4`; дуга радиуса 2 и sweep `π/2` касается отрезка `(0,0)`—`(10,0)` в точке `(5,0)`, центр `(5,2)`; revision 3, сцена bounds `[0,0,0]`…`[10,2,0]`, PNG просмотрен, dashboard 100%, lock `FREE`, финальные операции завершены (ранняя ошибочная ссылка на эскиз завершилась до изменения документа и сохранена в журнале активности).
- Видимый индивидуальный прогон SK-026: проект `Эскизы — индивидуальный видимый тест SK-026`, ID `3cdf85e6-4874-4603-920e-29ded926048c`; четверть окружности радиуса 5 с началом `(5,0)`, центром `(0,0)` и концом `(0,5)`, sweep `π/2`; revision 2, scene bounds `[0,0,0]`…`[5,5,0]`, PNG просмотрен, dashboard 100%, lock `FREE`, проверенная операция завершена.
- Видимый индивидуальный прогон SK-027: проект `Эскизы — индивидуальный видимый тест SK-027`, ID `4121192b-6d4c-41c1-a108-063cb5bc1f23`; малая дуга между `(0,0)` и `(4,0)` радиуса 3, центр `(2,√5)`, CCW sweep `1.4594553125`; revision 2, scene bounds `[0,-0.7603827062,0]`…`[4,2.2360679775,0]`, PNG просмотрен, dashboard 100%, lock `FREE`, операции завершены.
- Инспекция второго проекта: 36 объектов, 18 записей истории, revision 18, project lock `FREE`; сцена Agent API revision 18 содержит только активный эскиз и его геометрию, финальный viewport PNG просмотрен.

## Реестр отдельных проверок

`pending` означает, что проверка ещё не выполнена; статус меняется только по результату реального запуска. Имена исходного списка и повторы сохранены.

| ID | Инструмент | Индивидуальный unit/API-тест | Ранее выполненный spot-check | Тест группы | Общий проект |
|---|---|---|---|---|---|
| SK-001 | Точка | test_regular_point (passed) | PASS · rev 2 | pending | pending |
| SK-002 | Вспомогательная точка | test_construction_point (passed) | PASS · rev 4 | pending | pending |
| SK-003 | Отрезок | test_segment (passed) | PASS · rev 6 | pending | pending |
| SK-004 | Линия | test_line (passed) | PASS · rev 8 | pending | pending |
| SK-005 | Бесконечная линия | test_infinite_line (passed) | PASS · rev 10 | pending | pending |
| SK-006 | Осевая линия | test_centerline (passed) | PASS · rev 12 | pending | pending |
| SK-007 | Вспомогательная линия | test_construction_line (passed) | PASS · rev 14 | pending | pending |
| SK-008 | Ломаная | test_open_polyline (passed) | PASS · rev 16 | pending | pending |
| SK-009 | Цепочка линий | test_line_chain (passed) | PASS · rev 18 | pending | pending |
| SK-010 | Прямоугольник по двум углам | test_rectangle_two_corners (passed) | PASS · rev 2 | pending | pending |
| SK-011 | Прямоугольник из центра | test_rectangle_center (passed) | PASS · rev 4 | pending | pending |
| SK-012 | Прямоугольник по трём точкам | test_rectangle_three_points (passed) | PASS · rev 6 | pending | pending |
| SK-013 | Повернутый прямоугольник | test_rotated_rectangle (passed) | PASS · rev 8 | pending | pending |
| SK-014 | Параллелограмм | test_parallelogram (passed) | PASS · rev 10 | pending | pending |
| SK-015 | Скруглённый прямоугольник | test_rounded_rectangle (passed) | PASS · rev 2 | pending | pending |
| SK-016 | Окружность по центру и радиусу | test_circle_center_radius (passed) | PASS · rev 14 | pending | pending |
| SK-017 | Окружность по центру и диаметру | test_circle_center_diameter (passed) | PASS · rev 16 | pending | pending |
| SK-018 | Окружность по двум точкам | test_circle_two_points (passed) | PASS · rev 18 | pending | pending |
| SK-019 | Окружность по трём точкам | test_circle_three_points (passed) | PASS · rev 20 | pending | pending |
| SK-020 | Касательная окружность | test_circle_tangent_to_line_at_point (passed) | PASS · rev 3 | pending | pending |
| SK-021 | Окружность по двум касательным | test_circle_tangent_to_two_lines (passed) | PASS · rev 4 | pending | pending |
| SK-022 | Окружность по трём касательным | test_circle_tangent_to_three_lines (passed) | PASS · rev 5 | pending | pending |
| SK-023 | Дуга по центру | test_arc_center (passed) | PASS · rev 2 | pending | pending |
| SK-024 | Дуга по трём точкам | test_arc_three_points (passed) | PASS · rev 2 | pending | pending |
| SK-025 | Касательная дуга | test_arc_tangent (passed) | PASS · rev 3 | pending | pending |
| SK-026 | Дуга по началу, центру и концу | test_arc_start_center_end (passed) | PASS · rev 2 | pending | pending |
| SK-027 | Дуга по началу, концу и радиусу | test_arc_radius (passed) | PASS · rev 2 | pending | pending |
| SK-028 | Эллипс | add_sketch_ellipse (passed) | PASS · unit/API | pending | pending |
| SK-029 | Эллипс из центра | add_sketch_ellipse_center (passed) | PASS · unit/API | pending | pending |
| SK-030 | Эллипс по трём точкам | pending | pending | pending | pending |
| SK-031 | Эллиптическая дуга | pending | pending | pending | pending |
| SK-032 | Сплайн по точкам | pending | pending | pending | pending |
| SK-033 | Сплайн по контрольным точкам | pending | pending | pending | pending |
| SK-034 | B-сплайн | pending | pending | pending | pending |
| SK-035 | Кривая Безье | pending | pending | pending | pending |
| SK-036 | NURBS-кривая | pending | pending | pending | pending |
| SK-037 | Интерполяционный сплайн | pending | pending | pending | pending |
| SK-038 | Замкнутый сплайн | pending | pending | pending | pending |
| SK-039 | Парабола | pending | pending | pending | pending |
| SK-040 | Гипербола | pending | pending | pending | pending |
| SK-041 | Коническая кривая | pending | pending | pending | pending |
| SK-042 | Коническая дуга | pending | pending | pending | pending |
| SK-043 | Правильный многоугольник | test_regular_polygon (passed) | PASS · rev 12 | pending | pending |
| SK-044 | Вписанный многоугольник | test_inscribed_polygon (passed) | PASS · rev 2 | pending | pending |
| SK-045 | Описанный многоугольник | test_circumscribed_polygon (passed) | PASS · rev 4 | pending | pending |
| SK-046 | Многоугольник из центра | test_polygon_from_center (passed) | PASS · rev 2 | pending | pending |
| SK-047 | Прямой паз | pending | pending | pending | pending |
| SK-048 | Паз по центрам | pending | pending | pending | pending |
| SK-049 | Паз по общей длине | pending | pending | pending | pending |
| SK-050 | Паз от центральной точки | pending | pending | pending | pending |
| SK-051 | Дуговой паз по трём точкам | pending | pending | pending | pending |
| SK-052 | Дуговой паз из центра | pending | pending | pending | pending |
| SK-053 | Касательный дуговой паз | pending | pending | pending | pending |
| SK-054 | Спираль | pending | pending | pending | pending |
| SK-055 | Уравнительная кривая | pending | pending | pending | pending |
| SK-056 | Параметрическая кривая | pending | pending | pending | pending |
| SK-057 | Кривая по координатам | pending | pending | pending | pending |
| SK-058 | Текст | pending | pending | pending | pending |
| SK-059 | Многострочный текст | pending | pending | pending | pending |
| SK-060 | Текст по линии | pending | pending | pending | pending |
| SK-061 | Текст по дуге | pending | pending | pending | pending |
| SK-062 | Текст по кривой | pending | pending | pending | pending |
| SK-063 | Разбить текст на кривые | pending | pending | pending | pending |
| SK-064 | Совпадение | pending | pending | pending | pending |
| SK-065 | Точка на объекте | pending | pending | pending | pending |
| SK-066 | Фиксация | pending | pending | pending | pending |
| SK-067 | Блокировка геометрии | pending | pending | pending | pending |
| SK-068 | Горизонтальность | pending | pending | pending | pending |
| SK-069 | Вертикальность | pending | pending | pending | pending |
| SK-070 | Параллельность | pending | pending | pending | pending |
| SK-071 | Перпендикулярность | pending | pending | pending | pending |
| SK-072 | Коллинеарность | pending | pending | pending | pending |
| SK-073 | Касательность | pending | pending | pending | pending |
| SK-074 | Концентричность | pending | pending | pending | pending |
| SK-075 | Равенство радиусов | pending | pending | pending | pending |
| SK-076 | Равенство диаметров | pending | pending | pending | pending |
| SK-077 | Равенство длин | pending | pending | pending | pending |
| SK-078 | Равенство геометрии | pending | pending | pending | pending |
| SK-079 | Симметрия | pending | pending | pending | pending |
| SK-080 | Средняя точка | pending | pending | pending | pending |
| SK-081 | Центр | pending | pending | pending | pending |
| SK-082 | Совпадение с осевой линией | pending | pending | pending | pending |
| SK-083 | Плавность | pending | pending | pending | pending |
| SK-084 | Непрерывность G0 | pending | pending | pending | pending |
| SK-085 | Непрерывность G1 | pending | pending | pending | pending |
| SK-086 | Непрерывность G2 | pending | pending | pending | pending |
| SK-087 | Pierce | pending | pending | pending | pending |
| SK-088 | Пересечение | pending | pending | pending | pending |
| SK-089 | Нормальность | pending | pending | pending | pending |
| SK-090 | Одинаковое положение | pending | pending | pending | pending |
| SK-091 | Одинаковая ориентация | pending | pending | pending | pending |
| SK-092 | Горизонтальное расстояние | pending | pending | pending | pending |
| SK-093 | Вертикальное расстояние | pending | pending | pending | pending |
| SK-094 | Наклонное расстояние | pending | pending | pending | pending |
| SK-095 | Расстояние между точками | pending | pending | pending | pending |
| SK-096 | Расстояние от точки до линии | pending | pending | pending | pending |
| SK-097 | Длина линии | pending | pending | pending | pending |
| SK-098 | Радиус | pending | pending | pending | pending |
| SK-099 | Диаметр | pending | pending | pending | pending |
| SK-100 | Угол между линиями | pending | pending | pending | pending |
| SK-101 | Угол линии | pending | pending | pending | pending |
| SK-102 | Угол дуги | pending | pending | pending | pending |
| SK-103 | X-координата | pending | pending | pending | pending |
| SK-104 | Y-координата | pending | pending | pending | pending |
| SK-105 | Ординатный размер | pending | pending | pending | pending |
| SK-106 | Базовый размер | pending | pending | pending | pending |
| SK-107 | Длина дуги | pending | pending | pending | pending |
| SK-108 | Длина сплайна | pending | pending | pending | pending |
| SK-109 | Радиус кривизны | pending | pending | pending | pending |
| SK-110 | Управляющий размер | pending | pending | pending | pending |
| SK-111 | Справочный размер | pending | pending | pending | pending |
| SK-112 | Зависимый размер | pending | pending | pending | pending |
| SK-113 | Размер по формуле | pending | pending | pending | pending |
| SK-114 | Размер по выражению | pending | pending | pending | pending |
| SK-115 | Связанный размер | pending | pending | pending | pending |
| SK-116 | Глобальная переменная | pending | pending | pending | pending |
| SK-117 | Умный размер | pending | pending | pending | pending |
| SK-118 | Быстрый размер | pending | pending | pending | pending |
| SK-119 | Авторазмер | pending | pending | pending | pending |
| SK-120 | Линейный размер | pending | pending | pending | pending |
| SK-121 | Угловой размер | pending | pending | pending | pending |
| SK-122 | Радиальный размер | pending | pending | pending | pending |
| SK-123 | Диаметральный размер | pending | pending | pending | pending |
| SK-124 | Ординатный размер | pending | pending | pending | pending |
| SK-125 | Размер длины дуги | pending | pending | pending | pending |
| SK-126 | Обрезка | pending | pending | pending | pending |
| SK-127 | Быстрая обрезка | pending | pending | pending | pending |
| SK-128 | Обрезка до ближайшего объекта | pending | pending | pending | pending |
| SK-129 | Обрезка между пересечениями | pending | pending | pending | pending |
| SK-130 | Продление | pending | pending | pending | pending |
| SK-131 | Продление до ближайшего объекта | pending | pending | pending | pending |
| SK-132 | Продление до выбранного объекта | pending | pending | pending | pending |
| SK-133 | Разделить объект | pending | pending | pending | pending |
| SK-134 | Разорвать объект | pending | pending | pending | pending |
| SK-135 | Разрыв в точке | pending | pending | pending | pending |
| SK-136 | Разрыв в пересечении | pending | pending | pending | pending |
| SK-137 | Соединить объекты | pending | pending | pending | pending |
| SK-138 | Объединить точки | pending | pending | pending | pending |
| SK-139 | Объединить кривые | pending | pending | pending | pending |
| SK-140 | Скругление | pending | pending | pending | pending |
| SK-141 | Скругление угла | pending | pending | pending | pending |
| SK-142 | Множественное скругление | pending | pending | pending | pending |
| SK-143 | Фаска | pending | pending | pending | pending |
| SK-144 | Равносторонняя фаска | pending | pending | pending | pending |
| SK-145 | Фаска по двум расстояниям | pending | pending | pending | pending |
| SK-146 | Фаска по расстоянию и углу | pending | pending | pending | pending |
| SK-147 | Смещение | pending | pending | pending | pending |
| SK-148 | Смещение цепочки | pending | pending | pending | pending |
| SK-149 | Смещение контура | pending | pending | pending | pending |
| SK-150 | Двустороннее смещение | pending | pending | pending | pending |
| SK-151 | Переменное смещение | pending | pending | pending | pending |
| SK-152 | Перемещение | pending | pending | pending | pending |
| SK-153 | Копирование | pending | pending | pending | pending |
| SK-154 | Перемещение с копированием | pending | pending | pending | pending |
| SK-155 | Поворот | pending | pending | pending | pending |
| SK-156 | Масштабирование | pending | pending | pending | pending |
| SK-157 | Растяжение | pending | pending | pending | pending |
| SK-158 | Выравнивание | pending | pending | pending | pending |
| SK-159 | Линейный массив | pending | pending | pending | pending |
| SK-160 | Прямоугольный массив | pending | pending | pending | pending |
| SK-161 | Направленный массив | pending | pending | pending | pending |
| SK-162 | Круговой массив | pending | pending | pending | pending |
| SK-163 | Полярный массив | pending | pending | pending | pending |
| SK-164 | Массив по кривой | pending | pending | pending | pending |
| SK-165 | Массив по траектории | pending | pending | pending | pending |
| SK-166 | Массив по сетке | pending | pending | pending | pending |
| SK-167 | Массив по эскизу | pending | pending | pending | pending |
| SK-168 | Зеркальный массив | pending | pending | pending | pending |
| SK-169 | Зеркальное отражение | pending | pending | pending | pending |
| SK-170 | Зеркальное отражение цепочки | pending | pending | pending | pending |
| SK-171 | Зеркальное отражение относительно линии | pending | pending | pending | pending |
| SK-172 | Динамическое зеркало | pending | pending | pending | pending |
| SK-173 | Проецирование геометрии | pending | pending | pending | pending |
| SK-174 | Проецирование ребра | pending | pending | pending | pending |
| SK-175 | Проецирование точки | pending | pending | pending | pending |
| SK-176 | Проецирование грани | pending | pending | pending | pending |
| SK-177 | Проецирование силуэта | pending | pending | pending | pending |
| SK-178 | Проецирование пересечения | pending | pending | pending | pending |
| SK-179 | Преобразование объектов | pending | pending | pending | pending |
| SK-180 | Использование ребра | pending | pending | pending | pending |
| SK-181 | Внешняя геометрия | pending | pending | pending | pending |
| SK-182 | Включение геометрии | pending | pending | pending | pending |
| SK-183 | Пересечение грани плоскостью эскиза | pending | pending | pending | pending |
| SK-184 | Кривая пересечения | pending | pending | pending | pending |
| SK-185 | Поперечное сечение | pending | pending | pending | pending |
| SK-186 | Проекция 3D-геометрии | pending | pending | pending | pending |
| SK-187 | Проекция по нормали | pending | pending | pending | pending |
| SK-188 | Проекция по вектору | pending | pending | pending | pending |
| SK-189 | Вспомогательная окружность | pending | pending | pending | pending |
| SK-190 | Вспомогательная дуга | pending | pending | pending | pending |
| SK-191 | Вспомогательный прямоугольник | pending | pending | pending | pending |
| SK-192 | Переключение обычной и вспомогательной геометрии | pending | pending | pending | pending |
| SK-193 | Начало координат | pending | pending | pending | pending |
| SK-194 | Ось X | pending | pending | pending | pending |
| SK-195 | Ось Y | pending | pending | pending | pending |
| SK-196 | Локальная система координат | pending | pending | pending | pending |
| SK-197 | Опорная ось | pending | pending | pending | pending |
| SK-198 | Опорная точка | pending | pending | pending | pending |
| SK-199 | Плоскость эскиза | pending | pending | pending | pending |
| SK-200 | Базовая плоскость | pending | pending | pending | pending |
| SK-201 | Базовая ось | pending | pending | pending | pending |
| SK-202 | Базовая точка | pending | pending | pending | pending |
| SK-203 | Переориентация эскиза | pending | pending | pending | pending |
| SK-204 | Смена плоскости эскиза | pending | pending | pending | pending |
| SK-205 | Привязка эскиза к грани | pending | pending | pending | pending |
| SK-206 | Отвязка эскиза | pending | pending | pending | pending |
| SK-207 | Эскиз на плоскости XY | pending | pending | pending | pending |
| SK-208 | Эскиз на плоскости XZ | pending | pending | pending | pending |
| SK-209 | Эскиз на плоскости YZ | pending | pending | pending | pending |
| SK-210 | Эскиз на плоской грани | pending | pending | pending | pending |
| SK-211 | Эскиз на базовой плоскости | pending | pending | pending | pending |
| SK-212 | Эскиз на смещённой плоскости | pending | pending | pending | pending |
| SK-213 | Эскиз на наклонной плоскости | pending | pending | pending | pending |
| SK-214 | Эскиз на касательной плоскости | pending | pending | pending | pending |
| SK-215 | Эскиз на поверхности | pending | pending | pending | pending |
| SK-216 | Оборачивание эскиза по поверхности | pending | pending | pending | pending |
| SK-217 | Проекция эскиза на поверхность | pending | pending | pending | pending |
| SK-218 | Геодезическая кривая | pending | pending | pending | pending |
| SK-219 | Кривая на поверхности | pending | pending | pending | pending |
| SK-220 | UV-кривая | pending | pending | pending | pending |
| SK-221 | Сплайн на поверхности | pending | pending | pending | pending |
| SK-222 | Определение замкнутого профиля | pending | pending | pending | pending |
| SK-223 | Определение открытого профиля | pending | pending | pending | pending |
| SK-224 | Выбор профиля | pending | pending | pending | pending |
| SK-225 | Выбор области | pending | pending | pending | pending |
| SK-226 | Вложенный профиль | pending | pending | pending | pending |
| SK-227 | Внешний контур | pending | pending | pending | pending |
| SK-228 | Внутренний контур | pending | pending | pending | pending |
| SK-229 | Множественные области | pending | pending | pending | pending |
| SK-230 | Показ степеней свободы | pending | pending | pending | pending |
| SK-231 | Поиск недоопределённой геометрии | pending | pending | pending | pending |
| SK-232 | Поиск полностью определённой геометрии | pending | pending | pending | pending |
| SK-233 | Поиск переопределённой геометрии | pending | pending | pending | pending |
| SK-234 | Поиск конфликтующих ограничений | pending | pending | pending | pending |
| SK-235 | Поиск избыточных ограничений | pending | pending | pending | pending |
| SK-236 | Поиск открытых контуров | pending | pending | pending | pending |
| SK-237 | Поиск разрывов | pending | pending | pending | pending |
| SK-238 | Поиск наложений | pending | pending | pending | pending |
| SK-239 | Поиск дубликатов | pending | pending | pending | pending |
| SK-240 | Поиск самопересечений | pending | pending | pending | pending |
| SK-241 | Поиск слишком коротких объектов | pending | pending | pending | pending |
| SK-242 | Проверка эскиза | pending | pending | pending | pending |
| SK-243 | Восстановление эскиза | pending | pending | pending | pending |
| SK-244 | Автоматическое исправление эскиза | pending | pending | pending | pending |
| SK-245 | Анализ профилей | pending | pending | pending | pending |
| SK-246 | Автогоризонтальность | pending | pending | pending | pending |
| SK-247 | Автовертикальность | pending | pending | pending | pending |
| SK-248 | Автосовпадение | pending | pending | pending | pending |
| SK-249 | Автокасательность | pending | pending | pending | pending |
| SK-250 | Автоперпендикулярность | pending | pending | pending | pending |
| SK-251 | Автопараллельность | pending | pending | pending | pending |
| SK-252 | Автосредняя точка | pending | pending | pending | pending |
| SK-253 | Автоконцентричность | pending | pending | pending | pending |
| SK-254 | Авторавенство | pending | pending | pending | pending |
| SK-255 | Автоматическое наложение ограничений | pending | pending | pending | pending |
| SK-256 | Автоматическое нанесение размеров | pending | pending | pending | pending |
| SK-257 | Автоматическое замыкание профиля | pending | pending | pending | pending |
| SK-258 | Автоматическое удаление дубликатов | pending | pending | pending | pending |
| SK-259 | Автоматическое объединение объектов | pending | pending | pending | pending |
| SK-260 | Автоматическое упрощение геометрии | pending | pending | pending | pending |
| SK-261 | Автоматическое определение симметрии | pending | pending | pending | pending |
| SK-262 | Автоматическое определение массивов | pending | pending | pending | pending |
| SK-263 | Автоматическое определение касательности | pending | pending | pending | pending |
| SK-264 | Автоматическое определение равной геометрии | pending | pending | pending | pending |
| SK-265 | Выбор объекта | pending | pending | pending | pending |
| SK-266 | Выбор рамкой | pending | pending | pending | pending |
| SK-267 | Пересекающий выбор рамкой | pending | pending | pending | pending |
| SK-268 | Выбор лассо | pending | pending | pending | pending |
| SK-269 | Выбор цепочки | pending | pending | pending | pending |
| SK-270 | Выбор контура | pending | pending | pending | pending |
| SK-271 | Выбор связанной геометрии | pending | pending | pending | pending |
| SK-272 | Выбор похожих объектов | pending | pending | pending | pending |
| SK-273 | Выбор вспомогательной геометрии | pending | pending | pending | pending |
| SK-274 | Выбор ограничений | pending | pending | pending | pending |
| SK-275 | Выбор профилей | pending | pending | pending | pending |
| SK-276 | Удаление объекта | pending | pending | pending | pending |
| SK-277 | Удаление ограничения | pending | pending | pending | pending |
| SK-278 | Удаление размера | pending | pending | pending | pending |
| SK-279 | Подавление объекта | pending | pending | pending | pending |
| SK-280 | Подавление ограничения | pending | pending | pending | pending |
| SK-281 | Скрытие геометрии | pending | pending | pending | pending |
| SK-282 | Отображение геометрии | pending | pending | pending | pending |
| SK-283 | Импорт DXF | pending | pending | pending | pending |
| SK-284 | Импорт DWG | pending | pending | pending | pending |
| SK-285 | Импорт SVG | pending | pending | pending | pending |
| SK-286 | Импорт CSV-точек | pending | pending | pending | pending |
| SK-287 | Импорт координат | pending | pending | pending | pending |
| SK-288 | Вставка векторной геометрии | pending | pending | pending | pending |
| SK-289 | Трассировка геометрии | pending | pending | pending | pending |
| SK-290 | Кривая по уравнению | pending | pending | pending | pending |
| SK-291 | Кривая по закону | pending | pending | pending | pending |
| SK-292 | Кривая через XYZ-точки | pending | pending | pending | pending |
| SK-293 | Полиномиальная кривая | pending | pending | pending | pending |
| SK-294 | Кривая перехода | pending | pending | pending | pending |
| SK-295 | Смешанная кривая | pending | pending | pending | pending |
| SK-296 | Кривая с управлением кривизной | pending | pending | pending | pending |
| SK-297 | 3D-точка | pending | pending | pending | pending |
| SK-298 | 3D-линия | pending | pending | pending | pending |
| SK-299 | 3D-ломаная | pending | pending | pending | pending |
| SK-300 | 3D-дуга | pending | pending | pending | pending |
| SK-301 | 3D-сплайн | pending | pending | pending | pending |
| SK-302 | 3D-совпадение | pending | pending | pending | pending |
| SK-303 | 3D-параллельность | pending | pending | pending | pending |
| SK-304 | 3D-перпендикулярность | pending | pending | pending | pending |
| SK-305 | Ограничение вдоль X | pending | pending | pending | pending |
| SK-306 | Ограничение вдоль Y | pending | pending | pending | pending |
| SK-307 | Ограничение вдоль Z | pending | pending | pending | pending |
| SK-308 | Ограничение на плоскости | pending | pending | pending | pending |
| SK-309 | Ограничение на грани | pending | pending | pending | pending |
| SK-310 | Ограничение на кривой | pending | pending | pending | pending |
| SK-311 | 3D-расстояние | pending | pending | pending | pending |
| SK-312 | Расстояние XYZ | pending | pending | pending | pending |
| SK-313 | 3D-угол | pending | pending | pending | pending |
| SK-314 | Добавление ограничения | pending | pending | pending | pending |
| SK-315 | Удаление ограничения | pending | pending | pending | pending |
| SK-316 | Редактирование ограничения | pending | pending | pending | pending |
| SK-317 | Подавление ограничения | pending | pending | pending | pending |
| SK-318 | Добавление размера | pending | pending | pending | pending |
| SK-319 | Редактирование размера | pending | pending | pending | pending |
| SK-320 | Удаление размера | pending | pending | pending | pending |
| SK-321 | Фиксация объекта | pending | pending | pending | pending |
| SK-322 | Снятие фиксации | pending | pending | pending | pending |
| SK-323 | Решение эскиза | pending | pending | pending | pending |
| SK-324 | Пересчёт эскиза | pending | pending | pending | pending |
| SK-325 | Проверка степеней свободы | pending | pending | pending | pending |
| SK-326 | Получение конфликтов | pending | pending | pending | pending |
| SK-327 | Получение профилей | pending | pending | pending | pending |
| SK-328 | Получение открытых контуров | pending | pending | pending | pending |
| SK-329 | Получение ограничений | pending | pending | pending | pending |
| SK-330 | Проверка валидности эскиза | pending | pending | pending | pending |
| SK-331 | Проверка геометрии | pending | pending | pending | pending |
| SK-332 | Выражения | pending | pending | pending | pending |
| SK-333 | Формулы | pending | pending | pending | pending |
| SK-334 | Параметры | pending | pending | pending | pending |
| SK-335 | Глобальные параметры | pending | pending | pending | pending |
| SK-336 | Пользовательские переменные | pending | pending | pending | pending |
| SK-337 | Единицы измерения | pending | pending | pending | pending |
| SK-338 | История операций | pending | pending | pending | pending |
| SK-339 | Отмена операции | pending | pending | pending | pending |
| SK-340 | Повтор операции | pending | pending | pending | pending |
| SK-341 | Привязка к сетке | pending | pending | pending | pending |
| SK-342 | Отображение сетки | pending | pending | pending | pending |
| SK-343 | Настройка шага сетки | pending | pending | pending | pending |
| SK-344 | Объектные привязки | pending | pending | pending | pending |
| SK-345 | Привязка к концам | pending | pending | pending | pending |
| SK-346 | Привязка к середине | pending | pending | pending | pending |
| SK-347 | Привязка к центру | pending | pending | pending | pending |
| SK-348 | Привязка к пересечению | pending | pending | pending | pending |
| SK-349 | Привязка к касательной | pending | pending | pending | pending |
| SK-350 | Привязка к перпендикуляру | pending | pending | pending | pending |
| SK-351 | Привязка к ближайшей точке | pending | pending | pending | pending |
| SK-352 | Привязка к квадранту | pending | pending | pending | pending |
| SK-353 | Привязка к сетке | pending | pending | pending | pending |
| SK-354 | Динамический ввод | pending | pending | pending | pending |
| SK-355 | Предпросмотр ограничений | pending | pending | pending | pending |
| SK-356 | Предпросмотр размеров | pending | pending | pending | pending |
| SK-357 | Предпросмотр профилей | pending | pending | pending | pending |

## Примечания о текущей реализации

- Уже реализованные исходные инструменты также проходят индивидуальный, групповой и общий тесты; наличие прежних тестов засчитывается только после явного сопоставления с реестром.
- Схемные миграции, поддерживаемые типы, API, UI и dependency choices устанавливаются из фактического кода и официальных источников; эта таблица не является обещанием допустимости неточной/mesh-only подмены точных кривых или B-rep-операций.
- Видимые модельные прогоны создаются как отдельные именованные проекты в локальном CADYX workspace, поэтому пользователь может наблюдать каждый шаг в веб-панели; пользовательские проекты и файлы не используются/не перезаписываются. ID проектов фиксируются здесь по мере появления.
- Подтверждённая семантика SK-020: окружность касательна к выбранной линии в заданной контактной точке; пользователь задаёт радиус и сторону относительно направления линии. Команда `add_sketch_circle_tangent_to_line` требует линию того же эскиза; контакт должен лежать на конечном отрезке либо на опорной прямой бесконечной линии.
- Для SK-021 команда `add_sketch_circle_tangent_to_two_lines` принимает радиус и стороны смещения относительно направлений двух линий; пара сторон выбирает угловое решение. Проверка конечных отрезков требует, чтобы обе проекции касания лежали в их пределах.
- Для SK-022 команда `add_sketch_circle_tangent_to_three_lines` решает центр и радиус из трёх линий и трёх сторон касания; нулевые/вырожденные решения отклоняются, касания должны попадать на конечные отрезки.
- SK-025 уточнён пользователем: касательная дуга задаётся выбранной линией, точкой касания, радиусом и знаковым углом; API-команда `add_sketch_arc_tangent` принимает `sweep_degrees` и `side` относительно направления линии.
- SK-027 задаёт large_arc отдельно от clockwise; это снимает неоднозначность двух окружностей, проходящих через концы при заданном радиусе.
- SK-028 задаётся концами большой оси и малым радиусом; SK-029 — центром, большой/малой полуосями и углом поворота. В документе это единый точный аналитический тип `ellipse`; viewport и закрытый профиль используют 128 связанных отрезков представления.
- Отдельные unit/API-тесты обязательны для реализации; видимые spot-check проекты, созданные ранее для SK-001…SK-027, сохранены как дополнительное свидетельство. Новые видимые CAD-модели создавать на групповом и полном интеграционном стресс-этапах.
- Решение пользователя от 2026-10-15: отказаться от долгого поштучного визуального создания моделей; проверять визуально и под нагрузкой позже в групповых и общем проектах.
