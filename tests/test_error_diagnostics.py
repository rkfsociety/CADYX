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
