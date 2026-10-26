from __future__ import annotations

import unittest

from cadyx.errors import ConflictError
from cadyx.workspace import ProjectWorkspace


class ProjectTypeTransitionTests(unittest.TestCase):
    def test_empty_assembly_graph_allows_single_model(self) -> None:
        ProjectWorkspace._validate_project_type_transition(
            {"document": {"assembly": {
                "parts": [], "subassemblies": [], "instances": [], "mates": [],
            }}},
            "single_model",
        )

    def test_nonempty_assembly_graph_rejects_single_model(self) -> None:
        with self.assertRaisesRegex(ConflictError, "assembly components"):
            ProjectWorkspace._validate_project_type_transition(
                {"document": {"assembly": {
                    "parts": [], "subassemblies": [{"id": "subassembly:test"}],
                    "instances": [], "mates": [],
                }}},
                "single_model",
            )
