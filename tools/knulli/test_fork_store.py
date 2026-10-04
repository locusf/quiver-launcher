import importlib.util
from pathlib import Path
import json
import subprocess
import tempfile
import unittest
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location("fork_store_tests_module",
    Path(__file__).parent / "agent" / "fork_store.py")
forks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(forks)


class FakeGitHub:
    def __init__(self, head):
        self.calls = []
        self.head = head
        self.fork_exists = False
        self.branch_exists = False
        self.enabled = False
        self.license = "GPL-3.0"
        self.is_fork = True

    def __call__(self, method, path, body=None, missing_ok=False):
        self.calls.append((method, path, body))
        if path == "repos/upstream/game":
            return {"id": 1, "full_name": "upstream/game", "name": "game",
                    "private": False, "license": {"spdx_id": self.license}}
        if path == "user":
            return {"login": "builder"}
        if path == "repos/builder/game":
            if not self.fork_exists:
                return None
            return {"fork": self.is_fork, "source": {"id": 1}}
        if path == "repos/upstream/game/forks":
            self.fork_exists = True
            return {}
        if path.endswith("/actions/permissions"):
            if method == "PUT":
                self.enabled = body["enabled"]
            return {"enabled": self.enabled}
        if "/git/ref/heads/" in path:
            return {"object": {"sha": self.head}} if self.branch_exists else None
        if path.endswith("/git/refs"):
            self.branch_exists = True
            return {"object": {"sha": self.head}}
        if "/contents/" in path:
            return None
        if "/git/refs/heads/" in path and method == "PATCH":
            self.head = body["sha"]
            return {}
        if path.endswith("/git/blobs"):
            return {"sha": "c" * 40}
        if "/git/commits/" in path:
            return {"tree": {"sha": "d" * 40}}
        if path.endswith("/git/trees"):
            return {"sha": "e" * 40}
        if path.endswith("/git/commits"):
            return {"sha": "f" * 40}
        raise AssertionError(f"Unexpected API operation {method} {path}")


class ForkStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.source = Path(self.temporary.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.source / "game.c").write_text("int main(void) { return 1; }\n")
        self.git("add", "game.c")
        self.git("commit", "-qm", "Fixture source")
        self.head = self.git("rev-parse", "HEAD").strip()
        self.api = FakeGitHub(self.head)
        self.store = forks.ForkStore(self.source, "upstream/game", self.head,
                                     {"schema": 1, "architecture": "aarch64"}, self.api)

    def tearDown(self):
        self.temporary.cleanup()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.source), *args], text=True)

    def test_creates_licensed_fork_with_actions_disabled_and_dedicated_branch(self):
        self.store.ensure_fork()
        self.assertEqual(self.store.repository, "builder/game")
        self.assertTrue(self.store.branch.startswith("quiver/knulli/"))
        self.assertIn(("PUT", "repos/builder/game/actions/permissions", {"enabled": False}), self.api.calls)
        created = next(body for method, path, body in self.api.calls if path.endswith("/git/refs"))
        self.assertEqual(created["sha"], self.head)
        self.assertEqual(created["ref"], "refs/heads/" + self.store.branch)

    def test_unrecognized_license_never_creates_fork(self):
        self.api.license = "NOASSERTION"
        with self.assertRaisesRegex(RuntimeError, "open-source license"):
            self.store.ensure_fork()
        self.assertFalse(any(method == "POST" for method, _, _ in self.api.calls))

    def test_existing_unrelated_repository_is_not_overwritten(self):
        self.api.fork_exists = True
        self.api.is_fork = False
        with self.assertRaisesRegex(RuntimeError, "not a fork"):
            self.store.ensure_fork()

    def test_existing_fork_actions_are_not_silently_disabled(self):
        self.api.fork_exists = True
        self.api.enabled = True
        with self.assertRaisesRegex(RuntimeError, "Disable Actions"):
            self.store.ensure_fork()
        self.assertFalse(any(method in ("PUT", "PATCH", "POST") for method, _, _ in self.api.calls))

    def test_checkpoint_pushes_source_and_failure_context_without_force(self):
        self.store.ensure_fork()
        (self.source / "game.c").write_text("int main(void) { return 0; }\n")
        real_git = self.store.git
        self.store.git = lambda *args: "" if args[0] in ("fetch", "read-tree", "update-ref") else real_git(*args)
        events = []
        engine = SimpleNamespace(round_number=1, attempts=[], calls=60,
                                 last_outcome={"success": False, "error": "Missing Boost"},
                                 last_tool_error=None, last_summary="Install missing dependency.",
                                 record=lambda *args, **kwargs: events.append((args, kwargs)))
        result = self.store.checkpoint(engine, "tool-budget-exhausted")
        tree = next(body["tree"] for method, path, body in self.api.calls if path.endswith("/git/trees"))
        self.assertIn("game.c", [entry["path"] for entry in tree])
        saved = json.loads(next(entry["content"] for entry in tree if entry["path"] == forks.STATE_PATH))
        self.assertEqual(saved["last_build"]["error"], "Missing Boost")
        self.assertFalse(saved["runtime_verified"])
        update = next(body for method, path, body in self.api.calls if method == "PATCH")
        self.assertFalse(update["force"])
        self.assertEqual(result["commit"], "f" * 40)
        self.assertEqual(saved["status"], "tool-budget-exhausted")

    def test_concurrent_remote_progress_is_not_overwritten(self):
        self.store.ensure_fork()
        self.api.head = "a" * 40
        with self.assertRaisesRegex(RuntimeError, "concurrently"):
            self.store.checkpoint(SimpleNamespace(), "blocked")
        self.assertFalse(any(method == "PATCH" for method, _, _ in self.api.calls))

    def test_source_edit_policy_blocks_workflows_licenses_and_symlink_parents(self):
        (self.source / "alias").symlink_to(self.source / ".git", target_is_directory=True)
        for path in ("../outside", ".github/workflows/build.yml", ".git/config",
                     ".quiver-agent/state.json", "LICENSE", "alias/config"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                forks.validate_source_edit(self.source, path)

    def test_submodule_edits_are_rejected_before_a_parent_checkpoint_can_lose_them(self):
        submodule = self.source / "thirdparty" / "library"
        submodule.mkdir(parents=True)
        (submodule / ".git").write_text("gitdir: ../../.git/modules/library\n")
        with self.assertRaisesRegex(ValueError, "separate fork"):
            forks.validate_source_edit(self.source, "thirdparty/library/source.c")


if __name__ == "__main__":
    unittest.main()
