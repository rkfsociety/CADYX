import ctypes
import json
import re
import unittest
from pathlib import Path

from cadyx.errors import ValidationError
from cadyx.native_kernel import (
    NativeKernel,
    _NATIVE_ABI_VERSION,
    _NATIVE_FUNCTION_SIGNATURES,
    _OPTIONAL_NATIVE_FUNCTIONS,
    _load_native_functions,
)


class NativeFunction:
    def __init__(self, callback):
        self.callback = callback
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.callback(*args)


class NativeKernelFfiTests(unittest.TestCase):
    @staticmethod
    def kernel(functions):
        native = NativeKernel.__new__(NativeKernel)
        native._functions = functions
        return native

    def test_every_geometry_bridge_export_has_one_central_signature(self):
        native_root = Path(__file__).resolve().parent.parent / "native"
        binding_source = (native_root.parent / "cadyx" / "native_kernel.py").read_text(encoding="utf-8")
        abi_source = (native_root / "kernel_abi.hpp").read_text(encoding="utf-8")
        signature_table = re.search(
            r"(?ms)^_NATIVE_FUNCTION_SIGNATURES\s*=\s*\{(.*?)^\}", binding_source
        )
        self.assertIsNotNone(signature_table)
        declared = re.findall(r'^\s*"(cadyx_[A-Za-z0-9_]+)"\s*:', signature_table.group(1), re.M)
        self.assertEqual(len(declared), len(set(declared)))

        exported = set()
        export_sources = set()
        for source_path in native_root.glob("*.cpp"):
            if source_path.name == "scan.cpp":
                continue
            source = source_path.read_text(encoding="utf-8")
            source_exports = set(re.findall(
                r"\bCADYX_API\s+[^;{}]*?\b(cadyx_[A-Za-z0-9_]+)\s*\(", source, re.S,
            ))
            if source_exports:
                export_sources.add(source_path.name)
                exported.update(source_exports)

        abi_exports = re.search(
            r"(?ms)^#define CADYX_ABI_EXPORTS\(X\) \\\n(.*?)(?=^struct CadyxOperationNode)",
            abi_source,
        )
        self.assertIsNotNone(abi_exports)
        manifest_symbols = set(re.findall(r"\bX\((cadyx_[A-Za-z0-9_]+)\)", abi_exports.group(1)))

        self.assertEqual(exported, set(_NATIVE_FUNCTION_SIGNATURES))
        self.assertEqual(exported, set(declared))
        self.assertEqual(manifest_symbols, exported)
        self.assertEqual(export_sources, {"kernel.cpp"})

    def test_native_result_buffers_and_error_copying_have_one_owner(self):
        native_root = Path(__file__).resolve().parent.parent / "native"
        allocation_sources = set()
        error_helper_sources = set()
        for source_path in native_root.glob("*.cpp"):
            if source_path.name == "scan.cpp":
                continue
            source = source_path.read_text(encoding="utf-8")
            if re.search(r"new\s+char\s*\[", source):
                allocation_sources.add(source_path.name)
            if re.search(r"\bvoid\s+CopyError\s*\(", source):
                error_helper_sources.add(source_path.name)

        self.assertEqual(allocation_sources, {"kernel_common.cpp"})
        self.assertEqual(error_helper_sources, {"kernel_common.cpp"})

    def test_native_abi_manifest_is_checked_before_binding_optional_exports(self):
        exports = set(_NATIVE_FUNCTION_SIGNATURES) - _OPTIONAL_NATIVE_FUNCTIONS
        manifest_buffer = json.dumps({"abi_version": _NATIVE_ABI_VERSION, "exports": sorted(exports)}).encode()
        native_functions = {name: NativeFunction(lambda: None) for name in exports}
        native_functions["cadyx_kernel_abi_manifest"] = NativeFunction(lambda: manifest_buffer)
        version_function = native_functions["cadyx_kernel_version"]

        class Library:
            def __getattr__(self, name):
                return native_functions[name]

        def manifest_callback():
            self.assertIsNone(version_function.argtypes)
            return manifest_buffer

        native_functions["cadyx_kernel_abi_manifest"] = NativeFunction(manifest_callback)
        functions, manifest = _load_native_functions(Library())

        self.assertEqual(manifest["abi_version"], _NATIVE_ABI_VERSION)
        self.assertIn("cadyx_kernel_abi_manifest", functions)
        self.assertIsNone(functions.get("cadyx_advanced_brep"))
        self.assertEqual(version_function.argtypes, [])

    def test_unsupported_native_abi_fails_before_binding_other_symbols(self):
        manifest = NativeFunction(lambda: json.dumps({"abi_version": 999, "exports": []}).encode())

        class Library:
            cadyx_kernel_abi_manifest = manifest

            def __getattr__(self, name):
                raise AssertionError(f"unexpected symbol access: {name}")

        with self.assertRaisesRegex(RuntimeError, "Unsupported native bridge ABI version 999"):
            _load_native_functions(Library())

    def test_native_abi_rejects_an_advertised_symbol_missing_from_the_dll(self):
        actual_exports = set(_NATIVE_FUNCTION_SIGNATURES) - _OPTIONAL_NATIVE_FUNCTIONS
        exports = actual_exports.copy()
        exports.add("cadyx_new_symbol")
        manifest = NativeFunction(lambda: json.dumps({
            "abi_version": _NATIVE_ABI_VERSION,
            "exports": sorted(exports),
        }).encode())

        class Library:
            cadyx_kernel_abi_manifest = manifest

            def __getattr__(self, name):
                if name == "cadyx_kernel_abi_manifest":
                    return manifest
                if name in actual_exports:
                    return NativeFunction(lambda: None)
                raise AttributeError(name)

        with self.assertRaisesRegex(RuntimeError, "advertises missing export 'cadyx_new_symbol'"):
            _load_native_functions(Library())

    def test_modeling_modules_do_not_bind_or_access_native_symbols(self):
        package_root = Path(__file__).resolve().parent.parent / "cadyx"
        for filename in ("advanced_features.py", "advanced_topology.py", "agent_authoring.py"):
            source = (package_root / filename).read_text(encoding="utf-8")
            self.assertNotRegex(source, r"\.library\b|\.argtypes\b|\.restype\b|ctypes\.")

    def test_optional_metrics_result_is_decoded_and_freed_by_native_boundary(self):
        result_buffer = ctypes.create_string_buffer(b'{"volume":12.5}')
        freed = []
        function = NativeFunction(lambda *_args: ctypes.addressof(result_buffer))
        free = NativeFunction(lambda pointer: freed.append(pointer))
        native = self.kernel({"cadyx_brep_metrics": function, "cadyx_free": free})

        self.assertEqual(native.brep_metrics(b"brep"), {"volume": 12.5})
        self.assertEqual(freed, [ctypes.addressof(result_buffer)])
        self.assertIsNone(function.argtypes)

    def test_native_error_is_translated_without_freeing_a_null_result(self):
        error = ctypes.create_string_buffer(64)
        error.value = b"OCCT rejected B-rep"
        freed = []
        native = self.kernel({"cadyx_free": NativeFunction(lambda pointer: freed.append(pointer))})

        with self.assertRaisesRegex(ValidationError, "OCCT rejected B-rep"):
            native._json_result(None, error)

        self.assertEqual(freed, [])

    def test_advanced_result_and_generated_face_indices_are_owned_by_boundary(self):
        raw_result = b"serialized-brep"
        result_buffer = ctypes.create_string_buffer(raw_result)
        freed = []

        def advanced(*args):
            ctypes.cast(args[7], ctypes.POINTER(ctypes.c_size_t)).contents.value = len(raw_result)
            args[8].value = b"[2,5]"
            return ctypes.addressof(result_buffer)

        native = self.kernel({
            "cadyx_advanced_brep": NativeFunction(advanced),
            "cadyx_free": NativeFunction(lambda pointer: freed.append(pointer)),
        })

        self.assertEqual(native.advanced_brep(b"source", 2, [1.0], [3]), (raw_result, [2, 5]))
        self.assertEqual(freed, [ctypes.addressof(result_buffer)])

    def test_missing_optional_symbols_preserve_fallback_and_stale_runtime_errors(self):
        native = self.kernel({})

        self.assertIsNone(native.brep_metrics(b"source"))
        self.assertIsNone(native.agent_topology(b"source"))
        with self.assertRaisesRegex(RuntimeError, "rebuild advanced modeling support"):
            native.advanced_brep(b"source", 1, [])
        with self.assertRaisesRegex(RuntimeError, "rebuild agent authoring support"):
            native.agent_feature_brep(b"source", 1, [])


if __name__ == "__main__":
    unittest.main()
