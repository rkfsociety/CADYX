from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one exact match, found {count}")
    return text.replace(old, new, 1)


presentation = '''"""Canonical command-facing presentation metadata."""
from __future__ import annotations

from types import MappingProxyType
from typing import Mapping


DEFAULT_ACTIVITY_ACTION = "Выполняет операцию"

_BUILTIN_ACTIVITY_ACTIONS = {
    "create_box": "Строит модель",
    "create_sketch": "Создаёт эскиз",
    "add_sketch_element": "Рисует элемент эскиза",
    "add_sketch_polyline": "Рисует ломаную",
    "add_sketch_involute_gear": "Строит эвольвентный профиль",
    "add_sketch_rectangle": "Рисует прямоугольник",
    "add_sketch_rectangle_two_corners": "Рисует прямоугольник по двум углам",
    "add_sketch_rectangle_center": "Рисует прямоугольник от центра",
    "add_sketch_rectangle_three_points": "Рисует прямоугольник по трём точкам",
    "add_sketch_rotated_rectangle": "Рисует повёрнутый прямоугольник",
    "add_sketch_parallelogram": "Рисует параллелограмм",
    "add_sketch_regular_polygon": "Рисует правильный многоугольник",
    "add_sketch_circle": "Рисует окружность",
    "add_sketch_circle_diameter": "Рисует окружность по диаметру",
    "add_sketch_circle_two_points": "Рисует окружность по двум точкам",
    "add_sketch_circle_three_points": "Рисует окружность по трём точкам",
    "add_sketch_circle_tangent_to_line": "Строит окружность касательно к линии",
    "add_sketch_circle_tangent_to_two_lines": "Строит окружность по двум касательным",
    "add_sketch_circle_tangent_to_three_lines": "Строит окружность по трём касательным",
    "add_sketch_arc_center": "Строит дугу по центру",
    "add_sketch_arc_three_points": "Строит дугу по трём точкам",
    "add_sketch_arc_start_center_end": "Строит дугу по началу, центру и концу",
    "add_sketch_arc_radius": "Строит дугу по началу, концу и радиусу",
    "add_sketch_arc_tangent": "Строит касательную дугу",
    "add_sketch_rounded_rectangle": "Строит скруглённый прямоугольник",
    "mirror_sketch_elements": "Зеркально копирует геометрию эскиза",
    "pattern_sketch_elements": "Создаёт массив геометрии эскиза",
    "add_sketch_constraint": "Проверяет ограничение эскиза",
    "update_sketch_elements": "Меняет геометрию эскиза",
    "extrude_sketch": "Выдавливает профиль",
    "revolve_sketch": "Вращает профиль",
    "sweep_sketch": "Перемещает профиль по траектории",
    "fillet_edges": "Скругляет рёбра",
    "replace_sketch_profile": "Перерисовывает профиль",
    "replace_sketch_path": "Рисует траекторию",
    "fuse_bodies": "Объединяет тела",
    "cut_bodies": "Вырезает материал",
    "remove_body": "Удаляет тело из модели",
    "set_parameter": "Меняет параметры модели",
    "create_metric_thread": "Строит метрическую резьбу",
    "resize_imported_thread": "Увеличивает диаметр резьбы",
    "transform_imported_brep": "Выравнивает импортированную модель",
    "create_sketch_from_section": "Извлекает сечение STEP в эскиз",
    "loft_sketches": "Строит loft по эскизам сечений",
    "import_step": "Импортирует точную STEP-геометрию",
    "export_brep": "Проверяет и экспортирует модель САПР",
    "import_scan": "Обрабатывает скан",
    "set_scan_parameter": "Пересобирает скан",
    "remove_sketch_object": "Удаляет объект эскиза",
}

BUILTIN_ACTIVITY_ACTIONS: Mapping[str, str] = MappingProxyType(_BUILTIN_ACTIVITY_ACTIONS)
'''
write("cadyx/command_presentation.py", presentation)

extensions = read("cadyx/extensions.py")
extensions = replace_once(
    extensions,
    "from .errors import ValidationError\n",
    "from .command_presentation import BUILTIN_ACTIVITY_ACTIONS, DEFAULT_ACTIVITY_ACTION\nfrom .errors import ValidationError\n",
    label="extensions presentation import",
)
extensions = replace_once(
    extensions,
    "    refine: Callable[[tuple[dict[str, Any], ...]], tuple[dict[str, Any], ...]] | None = None\n",
    "    refine: Callable[[tuple[dict[str, Any], ...]], tuple[dict[str, Any], ...]] | None = None\n    activity_actions: Mapping[str, str] | None = None\n",
    label="extension metadata field",
)
extensions = replace_once(
    extensions,
    "        middleware = []\n        names: set[str] = set()\n\n        def add_contracts(entries: tuple[dict[str, Any], ...]) -> None:\n            for entry in entries:\n                name = entry[\"type\"]\n                if name in seen:\n                    raise ValueError(f\"Duplicate command registration: {name}.\")\n                seen.add(name)\n\n        add_contracts(contracts)\n",
    "        middleware = []\n        names: set[str] = set()\n        activity_actions: dict[str, str] = {}\n\n        def add_contracts(\n            entries: tuple[dict[str, Any], ...],\n            actions: Mapping[str, str] | None = None,\n        ) -> None:\n            entry_names: set[str] = set()\n            for entry in entries:\n                name = entry[\"type\"]\n                if name in seen:\n                    raise ValueError(f\"Duplicate command registration: {name}.\")\n                seen.add(name)\n                entry_names.add(name)\n                action = (\n                    actions[name]\n                    if actions is not None and name in actions\n                    else BUILTIN_ACTIVITY_ACTIONS.get(name, DEFAULT_ACTIVITY_ACTION)\n                )\n                if not isinstance(action, str) or not action.strip():\n                    raise ValueError(f\"Command {name} needs a non-empty activity action.\")\n                activity_actions[name] = action\n            if actions is not None:\n                unknown_actions = set(actions) - entry_names\n                if unknown_actions:\n                    names_text = \", \".join(sorted(unknown_actions))\n                    raise ValueError(f\"Extension activity metadata has no command: {names_text}.\")\n\n        add_contracts(contracts)\n",
    label="registry metadata composition",
)
extensions = replace_once(
    extensions,
    "            entries = deepcopy(extension.capabilities)\n            add_contracts(entries)\n",
    "            entries = deepcopy(extension.capabilities)\n            add_contracts(entries, extension.activity_actions)\n",
    label="extension metadata registration",
)
extensions = replace_once(
    extensions,
    "        self._capabilities = contracts\n        self.command_types = frozenset(seen)\n        self.handlers = MappingProxyType(handlers)\n",
    "        unknown_builtin_actions = set(BUILTIN_ACTIVITY_ACTIONS) - seen\n        if unknown_builtin_actions:\n            names_text = \", \".join(sorted(unknown_builtin_actions))\n            raise ValueError(f\"Built-in activity metadata has no registered command: {names_text}.\")\n        self._capabilities = contracts\n        self.command_types = frozenset(seen)\n        self.handlers = MappingProxyType(handlers)\n        self.activity_actions = MappingProxyType(activity_actions)\n",
    label="registry metadata publication",
)
extensions = replace_once(
    extensions,
    "    @property\n    def capabilities(self) -> tuple[dict[str, Any], ...]:\n        \"\"\"Return an isolated discovery snapshot, never the registry's metadata.\"\"\"\n        return deepcopy(self._capabilities)\n\n    def bind(\n",
    "    @property\n    def capabilities(self) -> tuple[dict[str, Any], ...]:\n        \"\"\"Return an isolated discovery snapshot, never the registry's metadata.\"\"\"\n        return deepcopy(self._capabilities)\n\n    def activity_action(self, command_name: Any) -> str:\n        \"\"\"Resolve presentation metadata without exposing Core to command-name tables.\"\"\"\n        if not isinstance(command_name, str):\n            return DEFAULT_ACTIVITY_ACTION\n        return self.activity_actions.get(command_name, DEFAULT_ACTIVITY_ACTION)\n\n    def bind(\n",
    label="registry metadata resolver",
)
write("cadyx/extensions.py", extensions)

commands = read("cadyx/commands.py")
commands = replace_once(
    commands,
    "        self._extension_execute = COMMAND_REGISTRY.bind(self)\n\n    def execute(\n",
    "        self._extension_execute = COMMAND_REGISTRY.bind(self)\n\n    @staticmethod\n    def activity_action(command_name: Any) -> str:\n        return COMMAND_REGISTRY.activity_action(command_name)\n\n    def execute(\n",
    label="command presentation facade",
)
write("cadyx/commands.py", commands)

core = read("cadyx/core.py")
core = replace_once(
    core,
    '                "action": self._activity_action(command_name),\n',
    '                "action": self.commands.activity_action(command_name),\n',
    label="core activity lookup",
)
activity_pattern = re.compile(
    r"\n    @staticmethod\n    def _activity_action\(command_name: Any\) -> str:\n.*?\n    @staticmethod\n    def _scan_source",
    re.DOTALL,
)
core, count = activity_pattern.subn("\n    @staticmethod\n    def _scan_source", core, count=1)
if count != 1:
    raise RuntimeError(f"core activity table removal: expected one match, found {count}")
write("cadyx/core.py", core)

test_source = '''from __future__ import annotations

import unittest
from pathlib import Path

from cadyx.capabilities import COMMAND_REGISTRY
from cadyx.command_presentation import BUILTIN_ACTIVITY_ACTIONS, DEFAULT_ACTIVITY_ACTION
from cadyx.extensions import CommandExtension, ExtensionRegistry


ROOT = Path(__file__).resolve().parents[1]


def _handler(*args, **kwargs):
    return {}


class CommandPresentationTests(unittest.TestCase):
    def test_registry_covers_every_registered_command(self) -> None:
        self.assertEqual(set(COMMAND_REGISTRY.activity_actions), set(COMMAND_REGISTRY.command_types))
        self.assertLessEqual(set(BUILTIN_ACTIVITY_ACTIONS), set(COMMAND_REGISTRY.command_types))
        self.assertTrue(all(COMMAND_REGISTRY.activity_actions.values()))
        self.assertEqual(COMMAND_REGISTRY.activity_action("create_box"), "Строит модель")
        self.assertEqual(COMMAND_REGISTRY.activity_action("export_brep"), "Проверяет и экспортирует модель САПР")
        self.assertEqual(COMMAND_REGISTRY.activity_action("not-a-command"), DEFAULT_ACTIVITY_ACTION)
        self.assertEqual(COMMAND_REGISTRY.activity_action(None), DEFAULT_ACTIVITY_ACTION)

    def test_extension_can_register_activity_metadata(self) -> None:
        extension = CommandExtension(
            "presentation-test",
            ({"type": "extension_command"},),
            _handler,
            activity_actions={"extension_command": "Показывает расширение"},
        )
        registry = ExtensionRegistry(
            ({"type": "base_command"},),
            (extension,),
            base_handlers={"base_command": _handler},
        )
        self.assertEqual(registry.activity_action("base_command"), DEFAULT_ACTIVITY_ACTION)
        self.assertEqual(registry.activity_action("extension_command"), "Показывает расширение")

    def test_extension_rejects_metadata_for_unregistered_command(self) -> None:
        extension = CommandExtension(
            "presentation-test",
            ({"type": "extension_command"},),
            _handler,
            activity_actions={"other_command": "Неверная метка"},
        )
        with self.assertRaisesRegex(ValueError, "has no command"):
            ExtensionRegistry(
                ({"type": "base_command"},),
                (extension,),
                base_handlers={"base_command": _handler},
            )

    def test_core_has_no_second_command_action_table(self) -> None:
        source = (ROOT / "cadyx" / "core.py").read_text(encoding="utf-8")
        self.assertNotIn("def _activity_action", source)
        self.assertNotIn('"create_box": "Строит модель"', source)
        self.assertIn("self.commands.activity_action(command_name)", source)


if __name__ == "__main__":
    unittest.main()
'''
write("tests/test_command_presentation.py", test_source)

roadmap = read(".codex/roadmap.md")
roadmap_pattern = re.compile(
    r"^33\. \*\*Candidate — Canonical command activity/presentation metadata:\*\*.*$",
    re.MULTILINE,
)
roadmap_replacement = (
    "33. **Complete — Canonical command activity/presentation metadata:** command activity labels now live in "
    "`cadyx/command_presentation.py` and are composed by `ExtensionRegistry` alongside command handlers and capabilities. "
    "Every registered command resolves an immutable activity action, the existing Russian labels are preserved, and unknown/external "
    "commands retain the generic `Выполняет операцию` fallback. `CommandExtension.activity_actions` lets extensions register their own "
    "presentation metadata without monkey-patching Core. `CadyxCore` delegates through `CommandSystem` and no longer carries a second "
    "command-name table. Evidence: [`cadyx/command_presentation.py`](../cadyx/command_presentation.py), "
    "[`cadyx/extensions.py`](../cadyx/extensions.py), [`cadyx/commands.py`](../cadyx/commands.py), "
    "[`cadyx/core.py`](../cadyx/core.py), and [`tests/test_command_presentation.py`](../tests/test_command_presentation.py)."
)
roadmap, count = roadmap_pattern.subn(roadmap_replacement, roadmap, count=1)
if count != 1:
    raise RuntimeError(f"roadmap stage 33 update: expected one match, found {count}")
write(".codex/roadmap.md", roadmap)
