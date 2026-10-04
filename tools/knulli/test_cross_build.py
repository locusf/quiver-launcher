import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("cross_build", Path(__file__).parent / "cross" / "build.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class CrossBuildTests(unittest.TestCase):
    def test_detects_supported_build_systems(self):
        for filename, expected in [
            ("CMakeLists.txt", "cmake"), ("meson.build", "meson"),
            ("configure", "configure"), ("configure.ac", "autoconf"), ("Makefile", "make"),
        ]:
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / filename).touch()
                self.assertEqual(build.detect_build_system(root), (root, expected))

    def test_nested_cmake_must_be_unambiguous(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("one", "two"):
                (root / name).mkdir()
                (root / name / "CMakeLists.txt").touch()
            with self.assertRaisesRegex(RuntimeError, "unambiguous"):
                build.detect_build_system(root)

    def test_rejects_wrong_architecture_and_shared_libraries(self):
        for machine, kind, interpreter, expected in [
            (183, 2, False, True), (183, 3, True, True),
            (183, 3, False, False), (62, 2, False, False),
        ]:
            with self.subTest(machine=machine, kind=kind, interpreter=interpreter), \
                    tempfile.TemporaryDirectory() as directory:
                header = bytearray(120)
                header[:6] = b"\x7fELF\x02\x01"
                struct.pack_into("<HH", header, 16, kind, machine)
                struct.pack_into("<Q", header, 32, 64)
                struct.pack_into("<HH", header, 54, 56, 1)
                if interpreter:
                    struct.pack_into("<I", header, 64, 3)
                path = Path(directory) / "game"
                path.write_bytes(header)
                self.assertEqual(build.is_arm64_executable(path), expected)


if __name__ == "__main__":
    unittest.main()
