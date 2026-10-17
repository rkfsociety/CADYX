"""Native-free ratchets for public annotations and static-quality configuration."""
from __future__ import annotations

import ast
import importlib.util
import io
import json
import re
import subprocess
import sys
import tokenize
import tomllib
from collections import Counter
import unittest
from pathlib import Path


BOUNDARIES = ("core", "commands", "workspace", "web", "agent_runtime")
BOUNDARY_BASELINE_CAPS = {
    "core": 13,
    "commands": 0,
    "workspace": 1,
    "web": 1,
    "agent_runtime": 0,
}
MYPY_CORE_SERVICE_FILES = {
    "cadyx/core.py",
    "cadyx/commands.py",
    "cadyx/workspace.py",
    "cadyx/web.py",
}
MYPY_ERROR_BASELINE_CAPS = {
    "core": {
        "arg-type": 16,
        "assignment": 9,
        "attr-defined": 1,
        "call-arg": 2,
        "dict-item": 1,
        "misc": 2,
        "operator": 27,
        "typeddict-item": 2,
        "union-attr": 7,
        "var-annotated": 3,
    },
    "commands": {},
    "workspace": {
        "arg-type": 3,
        "assignment": 2,
        "attr-defined": 5,
        "no-untyped-def": 1,
        "valid-type": 6,
        "var-annotated": 2,
    },
    "web": {
        "arg-type": 7,
        "index": 8,
        "union-attr": 5,
    },
}
RUFF_BUG_RISK_RULES = {"B006", "B008"}


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


def boundary_baseline(root: Path) -> dict[str, list[dict[str, str]]]:
    value = json.loads((root / "tests" / "static-quality-baseline.json").read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError("Static-quality baseline must be an object.")
    return value


def mypy_error_baseline(root: Path) -> dict[str, dict[str, int]]:
    value = json.loads((root / "tests" / "mypy-error-baseline.json").read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError("mypy error baseline must be an object.")
    return value


class StaticQualityTests(unittest.TestCase):
    def test_static_config_covers_core_services_and_bug_risk_rules(self) -> None:
        root = Path(__file__).resolve().parents[1]
        config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        mypy_files = set(config["tool"]["mypy"]["files"])
        self.assertTrue(
            MYPY_CORE_SERVICE_FILES.issubset(mypy_files),
            "Core service modules must stay in configured mypy coverage.",
        )
        ratchet_files = set(config["tool"]["cadyx"]["static-quality"]["mypy-ratchet-files"])
        self.assertEqual(ratchet_files, MYPY_CORE_SERVICE_FILES)
        ruff_rules = set(config["tool"]["ruff"]["lint"]["select"])
        self.assertTrue(
            RUFF_BUG_RISK_RULES.issubset(ruff_rules),
            "Configured Ruff bug-risk rules must not be removed from the source gate.",
        )

    def test_mypy_error_baseline_can_only_decrease(self) -> None:
        root = Path(__file__).resolve().parents[1]
        baseline = mypy_error_baseline(root)
        self.assertEqual(set(baseline), set(MYPY_ERROR_BASELINE_CAPS))
        for module, caps in MYPY_ERROR_BASELINE_CAPS.items():
            with self.subTest(module=module):
                codes = baseline[module]
                self.assertIsInstance(codes, dict)
                self.assertTrue(
                    set(codes).issubset(caps),
                    "New mypy error codes cannot be added to the legacy baseline.",
                )
                for code, count in codes.items():
                    self.assertIsInstance(code, str)
                    self.assertIs(type(count), int)
                    self.assertGreater(count, 0)
                    self.assertLessEqual(
                        count,
                        caps[code],
                        "Legacy mypy error-code counts may only decrease.",
                    )

    def test_suppressions_have_codes_and_inline_reasons(self) -> None:
        root = Path(__file__).resolve().parents[1]
        pattern = re.compile(r"(?:type:\s*ignore\[[\w, -]+\]|noqa:\s*[A-Z]+\d+(?:,\s*[A-Z]+\d+)*)\s+--\s+\S")
        for path in (root / "cadyx").glob("*.py"):
            for token in tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline):
                if token.type == tokenize.COMMENT and re.search(r"type:\s*ignore|\bnoqa\b", token.string):
                    with self.subTest(path=path.name, line=token.start[0]):
                        self.assertRegex(token.string, pattern)

    def test_public_boundary_annotations_match_documented_ratchet(self) -> None:
        root = Path(__file__).resolve().parents[1]
        baseline = boundary_baseline(root)
        self.assertEqual(set(baseline), set(BOUNDARIES))
        for module in BOUNDARIES:
            with self.subTest(module=module):
                records = baseline[module]
                self.assertIsInstance(records, list)
                allowed: Counter[str] = Counter()
                for record in records:
                    self.assertIsInstance(record, dict)
                    self.assertEqual(set(record), {"issue", "reason"})
                    issue = record["issue"]
                    reason = record["reason"]
                    self.assertIsInstance(issue, str)
                    self.assertIsInstance(reason, str)
                    self.assertTrue(reason.strip(), "Every unchecked annotation needs a reason.")
                    allowed[issue] += 1
                self.assertEqual(
                    sum(allowed.values()),
                    BOUNDARY_BASELINE_CAPS[module],
                    "Lower the hard cap whenever the committed annotation baseline shrinks.",
                )
                source = (root / "cadyx" / f"{module}.py").read_text(encoding="utf-8")
                current = Counter(annotation_errors(source))
                self.assertEqual(
                    current,
                    allowed,
                    "Unchecked public annotations must exactly match the documented baseline.",
                )

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

    def test_configured_bug_risk_rules_fail_negative_probes(self) -> None:
        if importlib.util.find_spec("ruff") is None:
            self.skipTest("Ruff negative probes run in the native-free source gate with dev dependencies installed.")
        probes = {
            "B006": "def probe(values=[]):\n    return values\n",
            "B008": "def build():\n    return 1\n\ndef probe(value=build()):\n    return value\n",
        }
        for rule, source in probes.items():
            with self.subTest(rule=rule):
                completed = subprocess.run(
                    [sys.executable, "-m", "ruff", "check", "--isolated", "--select", rule, "-"],
                    input=source,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn(rule, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
