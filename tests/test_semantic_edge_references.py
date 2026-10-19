import unittest

from cadyx.errors import ValidationError
from cadyx.kernel import OCCTKernelAdapter
from cadyx.semantic_selection import resolve_edge_references


class SemanticEdgeReferenceTests(unittest.TestCase):
    def test_equally_near_edge_references_fail_closed(self):
        kernel = OCCTKernelAdapter()
        geometry = kernel.create_box(width=10, height=100, depth=100)
        reference = {
            "curve": "line",
            "center": [5, 0, 50],
            "length": 100,
            "direction": [0, 0, 1],
        }

        with self.assertRaisesRegex(ValidationError, "equally near"):
            resolve_edge_references(kernel, geometry, [reference])


if __name__ == "__main__":
    unittest.main()
