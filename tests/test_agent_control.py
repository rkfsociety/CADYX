from __future__ import annotations

import tempfile
import unittest

from cadyx.core import CadyxCore
from cadyx.errors import ConflictError, ValidationError
from cadyx.kernel import PrimitiveKernelAdapter


BOX = {"type": "create_box", "width": 2, "height": 3, "depth": 4}


class AgentControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.core = CadyxCore(self.temp.name, kernel=PrimitiveKernelAdapter())
        self.project = self.core.create_project("Resume")["project"]["id"]
        self.token = self.core.acquire_lease(self.project, "agent", "agent")["lease_id"]

    def test_stop_resume_and_unknown_command_result(self):
        self.core.execute_command(self.project, self.token, BOX)
        stopped = self.core.stop_agent(self.project, self.token)
        self.assertEqual(stopped["document"]["revision"], 1)
        self.assertEqual(len(stopped["history"]), 1)
        self.assertEqual(stopped["lock"]["state"], "FREE")
        resumed = self.core.resume_agent(self.project, "agent", 1)
        token = resumed["lease"]["lease_id"]
        self.assertNotEqual(token, self.token)
        with self.assertRaises(ConflictError):
            self.core.execute_command(self.project, self.token, BOX)
        with self.assertRaises(ValidationError):
            self.core.execute_command(self.project, token, BOX)
        self.core.execute_command(self.project, token, BOX, expected_revision=1)
        # Lost response: retrying the old revision cannot duplicate the command.
        with self.assertRaises(ConflictError) as error:
            self.core.execute_command(self.project, token, BOX, expected_revision=1)
        self.assertEqual(error.exception.code, "REVISION_CONFLICT")
        self.assertEqual(len(self.core.inspect_project(self.project)["history"]), 2)

    def test_human_change_requires_reinspection(self):
        self.core.stop_agent(self.project, self.token)
        human = self.core.acquire_lease(self.project, "human", "human")["lease_id"]
        self.core.execute_command(self.project, human, BOX)
        self.core.release_lease(self.project, human)
        with self.assertRaises(ConflictError) as error:
            self.core.resume_agent(self.project, "agent", 0)
        self.assertEqual(error.exception.code, "REVISION_CONFLICT")
        self.assertEqual(self.core.inspect_project(self.project)["lock"]["state"], "FREE")
        self.assertEqual(self.core.resume_agent(self.project, "agent", 1)["document"]["revision"], 1)

    def test_validation_failure_keeps_lease_and_stop_fence_across_hosts(self):
        path = self.core.workspace.root / self.project / "project.json"
        original = path.read_bytes()
        path.write_text("{}")
        with self.assertRaises(ValidationError):
            self.core.stop_agent(self.project, self.token)
        path.write_bytes(original)
        other = CadyxCore(self.temp.name, kernel=PrimitiveKernelAdapter())
        with self.assertRaises(ConflictError) as error:
            other.execute_command(self.project, self.token, BOX)
        self.assertEqual(error.exception.code, "AGENT_STOPPING")
        self.assertEqual(other.leases.get(self.project).lease_id, self.token)
        other.stop_agent(self.project, self.token)

    def test_invalid_geometry_prevents_stop_and_resume(self):
        self.core.execute_command(self.project, self.token, BOX)
        original = self.core.kernel.validate
        def reject(geometry):
            raise ValidationError("Invalid geometry")
        self.core.kernel.validate = reject
        with self.assertRaises(ValidationError):
            self.core.stop_agent(self.project, self.token)
        self.assertEqual(self.core.leases.get(self.project).lease_id, self.token)
        self.core.kernel.validate = original
        self.core.stop_agent(self.project, self.token)
        self.core.kernel.validate = reject
        with self.assertRaises(ValidationError):
            self.core.resume_agent(self.project, "agent", 1)
        self.assertIsNone(self.core.leases.get(self.project))

    def test_stop_cannot_release_human_or_replacement_lease(self):
        self.core.release_lease(self.project, self.token)
        human = self.core.acquire_lease(self.project, "human", "human")["lease_id"]
        for token in (self.token, human):
            with self.assertRaises(ConflictError):
                self.core.stop_agent(self.project, token)
        self.core.execute_command(self.project, human, BOX)
