from __future__ import annotations

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
