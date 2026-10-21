import unittest

from cadyx.assembly_mates import inspect_assembly_mates
from cadyx.command_assembly import AssemblyCommands
from cadyx.commands import CommandSystem
from cadyx.core_services import ProjectLifecycleService
from cadyx.kernel import PrimitiveKernelAdapter
from cadyx.models import Document


class ReadOnlyAssemblyMateInspectionTests(unittest.TestCase):
    def test_project_inspection_exposes_assembly_degrees_of_freedom(self):
        system = CommandSystem(PrimitiveKernelAdapter())
        document = Document.create()
        body = system.execute(document, {"type": "create_box", "width": 1, "height": 2,
                                         "depth": 3})["body_id"]
        part = system.execute(document, {"type": "create_assembly_part",
                                         "body_id": body})["part_id"]
        first = system.execute(document, {"type": "add_assembly_instance",
                                          "target_id": part})["instance_id"]
        second = system.execute(document, {"type": "add_assembly_instance",
                                           "target_id": part})["instance_id"]
        system.execute(document, {"type": "add_assembly_mate", "kind": "fixed",
                                  "a_instance_id": first, "b_instance_id": second})
        project = {"id": "inspection-fixture", "name": "Inspection fixture",
                   "document": document.to_dict(), "history": []}

        class Workspace:
            def load(self, project_id):
                return project

        class LeaseManager:
            @staticmethod
            def snapshot_for_listing(project_id):
                return {"state": "FREE", "recovery_required": False}

        class Events:
            @staticmethod
            def activity_state_snapshot(project_id):
                return [], None

            @staticmethod
            def agent_dashboard_is_active(project_id):
                return False

        class ScanCapture:
            @staticmethod
            def is_running(project_id):
                return False

        class Core:
            workspace = Workspace()
            leases = LeaseManager()
            events = Events()
            _scan_capture = ScanCapture()

            @staticmethod
            def _summary(saved_project):
                return {"id": saved_project["id"], "name": saved_project["name"]}

            @staticmethod
            def _validate_geometry_once(project_id, saved_document):
                saved_document.validate()

        snapshot = ProjectLifecycleService(Core()).inspect_project(project["id"])
        self.assertEqual(snapshot["assembly_kinematics"]["internal_dof"], 0)
        self.assertEqual(snapshot["assembly_kinematics"]["status"], "fully_mated")
        self.assertEqual(len(snapshot["document"]["assembly"]["mates"]), 1)


if __name__ == "__main__":
    unittest.main()
