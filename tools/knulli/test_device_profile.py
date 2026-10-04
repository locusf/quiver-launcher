import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("device_profile", Path(__file__).with_name("device_profile.py"))
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


class DeviceProfileTests(unittest.TestCase):
    def test_exports_hardware_not_process_environment(self):
        with patch.dict(profile.os.environ, {"QUIVER_GITHUB_TOKEN": "do-not-export", "HOME": "/private"}), \
                patch.object(profile.platform, "machine", return_value="aarch64"), \
                patch.object(profile.platform, "release", return_value="4.9.170"), \
                patch.object(profile.platform, "libc_ver", return_value=("glibc", "2.40")), \
                patch.object(profile, "text", return_value="hardware-value"), \
                patch.object(profile, "controllers", return_value=[{
                    "name": "Pad", "index": 0, "buttons": 17, "axes": 4, "hats": 1,
                    "sdl_mapping": "guid,Pad,a:b4,start:b10,"}]):
            result = profile.collect()
        self.assertEqual(result["architecture"], "aarch64")
        self.assertEqual(result["controllers"][0]["buttons"], 17)
        self.assertNotIn("do-not-export", str(result))
        self.assertNotIn("/private", str(result))
        self.assertNotIn("environment", result)

    def test_hardware_read_failure_is_not_silently_defaulted(self):
        with patch.object(profile, "text", side_effect=FileNotFoundError("missing framebuffer")):
            with self.assertRaises(FileNotFoundError):
                profile.collect()


if __name__ == "__main__":
    unittest.main()
