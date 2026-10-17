import threading
import unittest
from collections import OrderedDict
from tempfile import TemporaryDirectory

from cadyx.core import CadyxCore
from cadyx.models import Document
from cadyx.runtime_cache import RuntimeCachePolicy, remember_bounded_bytes


class _KernelStub:
    def validate(self, geometry):
        return None


class RuntimeCacheTests(unittest.TestCase):
    def test_byte_bounded_lru_tracks_eviction_order_and_exact_bytes(self):
        cache = OrderedDict()
        retained = 0
        retained = remember_bounded_bytes(
            cache, "a", b"12", current_bytes=retained,
            max_entries=2, max_bytes=5, size_of=len,
        )
        retained = remember_bounded_bytes(
            cache, "b", b"345", current_bytes=retained,
            max_entries=2, max_bytes=5, size_of=len,
        )
        cache.move_to_end("a")
        retained = remember_bounded_bytes(
            cache, "c", b"6", current_bytes=retained,
            max_entries=2, max_bytes=5, size_of=len,
        )
        self.assertEqual(list(cache), ["a", "c"])
        self.assertEqual(retained, 3)
        retained = remember_bounded_bytes(
            cache, "oversized", b"123456", current_bytes=retained,
            max_entries=2, max_bytes=5, size_of=len,
        )
        self.assertEqual(cache, OrderedDict())
        self.assertEqual(retained, 0)

    def test_policy_budgets_are_validated_in_one_object(self):
        policy = RuntimeCachePolicy(
            validated_document_entries=1,
            scene_entries=2,
            png_entries=3,
            png_bytes=4,
            viewport_snapshot_entries=5,
            viewport_snapshot_bytes=6,
        )
        self.assertEqual(policy.png_bytes, 4)
        with self.assertRaises(ValueError):
            RuntimeCachePolicy(scene_entries=-1)
        with self.assertRaises(ValueError):
            RuntimeCachePolicy(png_bytes=True)

    def test_delete_project_invalidates_only_that_projects_runtime_caches(self):
        with TemporaryDirectory() as directory:
            core = CadyxCore(directory, kernel=_KernelStub())
            first = core.create_project("First", select=False)["project"]["id"]
            second = core.create_project("Second", select=False)["project"]["id"]
            first_key, second_key = (first, "int:0"), (second, "int:0")
            core._validated_document_keys.update(((first_key, None), (second_key, None)))
            core._document_validation_locks[first_key] = threading.Lock()
            core._document_validation_locks[second_key] = threading.Lock()
            core._scene_cache[(first, "r0")] = {"revision": 0}
            core._scene_cache[(second, "r0")] = {"revision": 0}
            first_png = (first, "r0", 1, 1, 0)
            second_png = (second, "r0", 1, 1, 0)
            core._png_cache[first_png] = b"abc"
            core._png_cache[second_png] = b"de"
            core._png_cache_bytes = 5
            core._viewport_snapshots[first] = (0, b"1234")
            core._viewport_snapshots[second] = (0, b"56")
            core._viewport_snapshot_bytes = 6

            core.delete_project(first)

            self.assertNotIn(first_key, core._validated_document_keys)
            self.assertIn(second_key, core._validated_document_keys)
            self.assertNotIn(first_key, core._document_validation_locks)
            self.assertIn(second_key, core._document_validation_locks)
            self.assertFalse(any(key[0] == first for key in core._scene_cache))
            self.assertTrue(any(key[0] == second for key in core._scene_cache))
            self.assertFalse(any(key[0] == first for key in core._png_cache))
            self.assertTrue(any(key[0] == second for key in core._png_cache))
            self.assertEqual(core._png_cache_bytes, 2)
            self.assertNotIn(first, core._viewport_snapshots)
            self.assertIn(second, core._viewport_snapshots)
            self.assertEqual(core._viewport_snapshot_bytes, 2)

    def test_concurrent_validation_still_coalesces_per_document_key(self):
        with TemporaryDirectory() as directory:
            core = CadyxCore(directory, kernel=_KernelStub())
            project_id = core.create_project("Validation", select=False)["project"]["id"]
            document = Document.create()
            entered = threading.Event()
            release = threading.Event()
            call_guard = threading.Lock()
            calls = 0

            def validate(_document):
                nonlocal calls
                with call_guard:
                    calls += 1
                entered.set()
                self.assertTrue(release.wait(2))

            core._validate_geometry = validate
            threads = [
                threading.Thread(target=core._validate_geometry_once, args=(project_id, document))
                for _ in range(2)
            ]
            threads[0].start()
            self.assertTrue(entered.wait(2))
            threads[1].start()
            release.set()
            for thread in threads:
                thread.join(2)
                self.assertFalse(thread.is_alive())
            self.assertEqual(calls, 1)


if __name__ == "__main__":
    unittest.main()
