from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from cadyx.errors import ValidationError, public_data, public_text
from cadyx.mcp_server import CadyxAPI, MCPApplication


class ErrorDiagnosticsTests(unittest.TestCase):
    def test_structured_kernel_error_has_safe_actionable_fields(self) -> None:
        error = ValidationError(
            r"OCCT failed at D:\private\cad\part.step Bearer supersecrettoken",
            code="KERNEL_OPERATION_FAILED",
            diagnostics={"body_id": "body:one", "revision": 4},
        )
        payload = error.as_dict()
        self.assertEqual(payload["error"], "KERNEL_OPERATION_FAILED")
        self.assertNotIn("D:\\private", json.dumps(payload))
        self.assertNotIn("supersecrettoken", json.dumps(payload))
        self.assertEqual(payload["diagnostics"]["category"], "kernel")
        self.assertEqual(payload["diagnostics"]["body_id"], "body:one")
        self.assertEqual(payload["diagnostics"]["revision"], 4)
        self.assertIn("correction_hint", payload["diagnostics"])
        self.assertIsNone(payload["diagnostics"]["measurements"])

    def test_solver_failure_preserves_constraint_witness_measurements(self) -> None:
        error = ValidationError(
            "Sketch constraints are inconsistent.", code="SKETCH_CONSTRAINT_INCONSISTENT",
            diagnostics={"entity_ids": ["constraint:one"],
                         "measurements": {"residual": 0.25, "tolerance": 1e-7}},
        )
        diagnostics = error.as_dict()["diagnostics"]
        self.assertEqual(diagnostics["category"], "constraint_solver")
        self.assertEqual(diagnostics["entity_ids"], ["constraint:one"])
        self.assertEqual(diagnostics["measurements"]["residual"], 0.25)
        self.assertIn("correction_hint", diagnostics)

    def test_public_text_redacts_paths_credentials_and_bearer_tokens(self) -> None:
        value = public_text("failed at C:\\Users\\roman\\part.step token=abc123 Bearer xyz789")
        self.assertNotIn("C:\\Users", value)
        self.assertNotIn("abc123", value)
        self.assertNotIn("xyz789", value)

    def test_public_data_redacts_secrets_and_paths_in_mapping_keys(self) -> None:
        diagnostics = {
            "token=abc123": {r"C:\\Users\\private\\part.step": "Bearer xyz789"},
            "revision": 4,
        }
        sanitized = public_data(diagnostics)
        encoded = json.dumps(sanitized)
        for secret in ("abc123", "xyz789", r"C:\\Users\\private"):
            self.assertNotIn(secret, encoded)
        self.assertEqual(sanitized["revision"], 4)

        error = ValidationError("Invalid input", diagnostics=diagnostics)
        encoded_error = json.dumps(error.as_dict())
        for secret in ("abc123", "xyz789", r"C:\\Users\\private"):
            self.assertNotIn(secret, encoded_error)

    def test_unicode_bearer_and_json_quoted_credentials_are_redacted(self) -> None:
        bearer = "é" * 32
        payload = json.dumps({
            "token": "abc123",
            "password": 'quoted"secret',
            "api_key": "xyz987",
        })
        sanitized = public_text(f"Bearer {bearer} {payload}")
        for secret in (bearer, "abc123", "quoted", "xyz987"):
            self.assertNotIn(secret, sanitized)
        self.assertIn('"token": "[redacted]"', sanitized)
        self.assertIn('"password": "[redacted]"', sanitized)
        self.assertIn('"api_key": "[redacted]"', sanitized)

    def test_sensitive_diagnostic_mapping_values_are_redacted(self) -> None:
        diagnostics = {
            "token": "plain-secret",
            "nested": {"api_key": "another-secret", "revision": 3},
            "authorization": "Basic Zm9vOmJhcg==",
            "safe": "keep",
        }
        sanitized = public_data(diagnostics)
        self.assertEqual(sanitized["token"], "[redacted]")
        self.assertEqual(sanitized["nested"]["api_key"], "[redacted]")
        self.assertEqual(sanitized["nested"]["revision"], 3)
        self.assertEqual(sanitized["authorization"], "[redacted]")
        self.assertEqual(sanitized["safe"], "keep")
        serialized = json.dumps(ValidationError("Invalid input", diagnostics=diagnostics).as_dict())
        for secret in ("plain-secret", "another-secret", "Zm9vOmJhcg=="):
            self.assertNotIn(secret, serialized)

    def test_public_text_redacts_authorization_headers_and_quoted_values(self) -> None:
        examples = [
            ("Authorization: Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
            ("Proxy-Authorization: Digest digest-secret", "digest-secret"),
            ('client_secret="two words secret"', "two words secret"),
            ("auth_token='single quoted secret'", "single quoted secret"),
            ("private_key=KEYXYZ", "KEYXYZ"),
        ]
        for original, secret in examples:
            with self.subTest(original=original):
                self.assertNotIn(secret, public_text(original))

        sanitized = public_text(json.dumps({
            "authorization": "Basic json-secret",
            "session_token": "session-secret",
            "client_secret": 'quoted"secret',
        }))
        for secret in ("json-secret", "session-secret", 'quoted"secret'):
            self.assertNotIn(secret, sanitized)
        self.assertIn('"authorization": "[redacted]"', sanitized)

    def test_public_data_handles_cycles_and_bounded_nesting(self) -> None:
        mapping = {}
        mapping["self"] = mapping
        sequence = []
        sequence.append(sequence)
        shared = {"safe": "kept"}
        diagnostics = {
            "mapping": mapping, "sequence": sequence,
            "first": shared, "second": shared,
        }
        sanitized = public_data(diagnostics)
        self.assertEqual(sanitized["mapping"]["self"], "[circular]")
        self.assertEqual(sanitized["sequence"][0], "[circular]")
        self.assertEqual(sanitized["first"], sanitized["second"])
        self.assertIn("[circular]", json.dumps(
            ValidationError("Invalid input", diagnostics=diagnostics).as_dict()
        ))

        nested = {}
        cursor = nested
        for _ in range(70):
            child = {}
            cursor["child"] = child
            cursor = child
        self.assertIn("[truncated]", json.dumps(public_data(nested)))

    def test_unexpected_mcp_failure_does_not_disclose_exception_details(self) -> None:
        app = MCPApplication()
        with patch.object(app, "_dispatch", side_effect=RuntimeError(r"failed C:\private\token=abc123")):
            result = app.call_tool("cadyx_health", {})
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["error"], "INTERNAL_ERROR")
        self.assertNotIn("C:\\private", json.dumps(result))
        self.assertNotIn("abc123", json.dumps(result))

    def test_mcp_transport_failure_has_typed_uncertain_diagnostic(self) -> None:
        app = MCPApplication(CadyxAPI())
        with patch("http.client.HTTPConnection.request", side_effect=OSError("Bearer supersecret")):
            result = app.call_tool("cadyx_health", {})
        self.assertEqual(result["structuredContent"]["error"], "TRANSIENT_TRANSPORT")
        self.assertEqual(result["structuredContent"]["diagnostics"]["category"], "transport")
        self.assertIn("uncertain", result["structuredContent"]["message"])
        self.assertNotIn("supersecret", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
