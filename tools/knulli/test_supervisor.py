import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("supervisor", Path(__file__).with_name("supervisor.py"))
supervisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supervisor)


class SupervisorTests(unittest.TestCase):
    def test_game_runs_only_after_launcher_exits_then_launcher_restarts(self):
        with tempfile.TemporaryDirectory() as root:
            request = Path(root) / "launch-request.json"
            calls = []

            def run(command, **kwargs):
                calls.append((command, kwargs))
                if len(calls) == 1:
                    request.write_text(json.dumps({
                        "FileName": "/games/My Game/launch.sh",
                        "Arguments": ["one argument", ";not-a-shell-command"],
                        "WorkingDirectory": "/games/My Game",
                        "Environment": {"HOME": "/userdata/system", "REMOVED": None},
                    }))
                    return subprocess.CompletedProcess(command, 75)
                return subprocess.CompletedProcess(command, 0)

            with patch.dict(supervisor.os.environ, {"QUIVER_DATA_HOME": root}), \
                    patch.object(supervisor, "show_first_page"), \
                    patch.object(supervisor.subprocess, "run", side_effect=run):
                self.assertEqual(supervisor.main(), 0)
            self.assertEqual(len(calls), 3)
            self.assertEqual(calls[1][0], ["/games/My Game/launch.sh", "one argument", ";not-a-shell-command"])
            self.assertEqual(calls[1][1]["env"], {"HOME": "/userdata/system"})
            self.assertFalse(request.exists())

    def test_failed_launcher_is_not_restarted(self):
        with tempfile.TemporaryDirectory() as root, \
                patch.dict(supervisor.os.environ, {"QUIVER_DATA_HOME": root}), \
                patch.object(supervisor, "show_first_page"), \
                patch.object(supervisor.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)) as run:
            self.assertEqual(supervisor.main(), 1)
            run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
