"""Native-free ratchet for annotations on public application boundaries."""
from __future__ import annotations

import ast
import json
import io
import re
import tokenize
from collections import Counter
import unittest
from pathlib import Path


BOUNDARIES = ("core", "commands", "web", "agent_runtime")


def annotation_errors(source: str) -> list[str]:
    errors: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("_") and node.name != "__init__":
            continue
        arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        arguments += [arg for arg in (node.args.vararg, node.args.kwarg) if arg is not None]
        annotations = [(arg.arg, arg.annotation) for arg in arguments if arg.arg not in ("self", "cls")]
        annotations.append(("return", node.returns))
        for name, annotation in annotations:
            if annotation is None:
                errors.append(f"{node.name}:{name}: missing annotation")
            elif isinstance(annotation, ast.Name) and annotation.id in ("Any", "object"):
                errors.append(f"{node.name}:{name}: unchecked annotation")
    return errors


class StaticQualityTests(unittest.TestCase):
    def test_suppressions_have_codes_and_inline_reasons(self) -> None:
        root = Path(__file__).resolve().parents[1]
        pattern = re.compile(r"(?:type:\s*ignore\[[\w, -]+\]|noqa:\s*[A-Z]+\d+(?:,\s*[A-Z]+\d+)*)\s+--\s+\S")
        for path in (root / "cadyx").glob("*.py"):
            for token in tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline):
                if token.type == tokenize.COMMENT and re.search(r"type:\s*ignore|\bnoqa\b", token.string):
                    with self.subTest(path=path.name, line=token.start[0]):
                        self.assertRegex(token.string, pattern)

    def test_public_boundary_annotations(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for module in BOUNDARIES:
            with self.subTest(module=module):
                source = (root / "cadyx" / f"{module}.py").read_text(encoding="utf-8")
                baseline = json.loads((root / "tests" / "static-quality-baseline.json").read_text(encoding="utf-8"))
                current = Counter(annotation_errors(source))
                allowed = Counter(baseline[module])
                self.assertEqual(dict(current - allowed), {}, "New unchecked public annotation")

    def test_ratchet_rejects_unchecked_signatures(self) -> None:
        for source in (
            "def call(value): pass",
            "def call(value: Any) -> Any: pass",
            "async def call(*values, **options) -> None: pass",
            "def call(value: object) -> None: pass",
        ):
            with self.subTest(source=source):
                self.assertTrue(annotation_errors(source))

    def test_ratchet_accepts_typed_payloads(self) -> None:
        self.assertEqual(annotation_errors(
            "def call(value: dict[str, Any], *, revision: int) -> None: pass"
        ), [])


if __name__ == "__main__":
    unittest.main()
