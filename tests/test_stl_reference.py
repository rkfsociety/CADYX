import hashlib, struct, tempfile, unittest
from pathlib import Path
from cadyx.errors import ValidationError

class STLReferenceTests(unittest.TestCase):
    def test_binary_and_solid_header_preserve_coordinates(self):
        from cadyx.stl_reference import parse_stl
        data = b"solid binary".ljust(80, b"\0") + struct.pack("<I", 1)
        data += struct.pack("<12fH", 0, 0, 1, 0, 0, 0, 2, 0, 0, 0, 3, 0, 0)
        geometry, stats = parse_stl(data)
        self.assertEqual(stats["bounds"], {"min": [0, 0, 0], "max": [2, 3, 0]})
        self.assertEqual(geometry["triangle_count"], 1)
        self.assertEqual(stats["units"], "unitless")

    def test_ascii_and_malformed_sources(self):
        from cadyx.stl_reference import parse_stl
        data = b"solid part\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 2 0 0\nvertex 0 3 0\nendloop\nendfacet\nendsolid part\n"
        geometry, _ = parse_stl(data)
        self.assertEqual(geometry["vertex_count"], 3)
        for bad in (data[:-20], data.replace(b"2 0 0", b"nan 0 0"), data.replace(b"2 0 0", b"0 0 0")):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                parse_stl(bad)

    def test_import_hash_fences_changed_file_and_sections_use_mesh(self):
        from cadyx.stl_reference import _execute
        from cadyx.models import Document
        from cadyx.scan_reverse import _mesh_body
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "reference.stl"
            data = b"binary".ljust(80, b"\0") + struct.pack("<I", 1)
            data += struct.pack("<12fH", 0, 0, 1, 0, 0, 0, 2, 0, 0, 0, 3, 0, 0)
            path.write_bytes(data)
            document = Document.create()
            command = {"source_path": str(path), "expected_sha256": hashlib.sha256(data).hexdigest()}
            result = _execute(None, document, command)
            self.assertEqual(_mesh_body(document, result["body_id"])["geometry"]["triangle_count"], 1)
            document.validate()
            command["expected_sha256"] = "0" * 64
            with self.assertRaises(ValidationError):
                _execute(None, document, command)
            self.assertEqual(len(document.objects), 2)

class BodyVisibilityTests(unittest.TestCase):
    def test_visibility_preserves_body_and_geometry(self):
        from cadyx.stl_reference import _execute
        from cadyx.models import Document
        document = Document.create()
        body = {"id":"body:test","type":"body","geometry":{"unchanged":True}}
        document.objects.append(body)
        _execute(None, document, {"type":"set_body_visibility","body_id":"body:test","visible":False})
        self.assertFalse(body["visible"])
        self.assertEqual(body["geometry"], {"unchanged":True})
        with self.assertRaises(ValidationError):
            _execute(None, document, {"type":"set_body_visibility","body_id":"body:test","visible":0})
