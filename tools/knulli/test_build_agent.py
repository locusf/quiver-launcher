import importlib.util
import io
from pathlib import Path
import json
import struct
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("build_agent", Path(__file__).parent / "agent" / "build_agent.py")
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


def write_elf(path, machine=183):
    header = bytearray(64)
    header[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<HH", header, 16, 2, machine)
    path.write_bytes(header)


class AgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.source = root / "source"
        self.output = root / "output"
        self.source.mkdir()
        (self.source / "input.c").write_text(
            "SDL_GameControllerGetButton(controller, SDL_CONTROLLER_BUTTON_A);\n"
            "int input_enabled = 1;\n"
            "int confirm = SDL_CONTROLLER_BUTTON_A;\n")
        (self.source / "LICENSE").write_text("Fixture license notice\n")
        self.report = {"api": "sdl2-gamecontroller", "support": "native", "notes": "Uses normalized SDL2 buttons.",
                       "activation": "Input is enabled on all targets.", "bindings": "Uses logical SDL A.",
                       "evidence": [
                           {"path": "input.c", "line": 1, "quote": "SDL_GameControllerGetButton", "role": "api"},
                           {"path": "input.c", "line": 2, "quote": "input_enabled = 1", "role": "activation"},
                           {"path": "input.c", "line": 3, "quote": "SDL_CONTROLLER_BUTTON_A", "role": "bindings"}]}

    def tearDown(self):
        self.temporary.cleanup()

    def successful_build(self, directory):
        package = directory / "files" / "package"
        package.mkdir()
        write_elf(package / "game")
        (package / "launch.sh").write_text('#!/bin/sh\ncd "$(dirname "$0")"\nexec ./game\n')
        (directory / "build.log").write_text("Build succeeded\n")
        return 0

    def test_reasoning_tools_can_repair_failed_attempt_and_publish_only_validated_package(self):
        count = 0

        def runner(directory):
            nonlocal count
            count += 1
            if count == 1:
                (directory / "build.log").write_text("fatal error: missing config.h\n")
                return 2
            return self.successful_build(directory)

        engine = agent.BuildAgent(self.source, self.output, {"architecture": "aarch64"}, runner)
        self.assertIn("input.c", engine.list_source())
        self.assertIn("SDL_GameControllerGetButton", engine.inspect_source("input.c"))
        first = engine.attempt_build("make", "game", self.report)
        self.assertFalse(first["success"])
        self.assertIn("missing config.h", first["log_tail"])
        with self.assertRaisesRegex(RuntimeError, "No validated build"):
            engine.finish("Done")
        second = engine.attempt_build("cmake -S . -B build && cmake --build build", "game", self.report)
        self.assertTrue(second["success"])
        report = engine.finish("Generated config before compiling. SDL2 mappings preserved; device testing pending.")
        self.assertEqual(report["attempts"], 2)
        self.assertFalse(report["runtime_verified"])
        self.assertFalse(report["controller"]["runtime_verified"])
        self.assertTrue((self.output / "package" / "quiver-agent-report.json").is_file())
        self.assertEqual((self.output / "package" / "source-licenses" / "LICENSE").read_text(),
                         "Fixture license notice\n")

    def test_wrong_architecture_is_not_success(self):
        def runner(directory):
            self.successful_build(directory)
            write_elf(directory / "files" / "package" / "game", machine=62)
            return 0
        engine = agent.BuildAgent(self.source, self.output, {}, runner)
        result = engine.attempt_build("make", "game", self.report)
        self.assertFalse(result["success"])
        self.assertIn("ARM64 ELF", result["error"])

    def test_unsupported_input_is_reported_not_claimed_verified(self):
        engine = agent.BuildAgent(self.source, self.output, {}, self.successful_build)
        report = dict(self.report, support="unsupported", notes="Keyboard input requires adaptation.")
        engine.attempt_build("make", "game", report)
        final = engine.finish("Compiled, but input support needs adaptation.")
        self.assertEqual(final["controller"]["support"], "unsupported")
        self.assertFalse(final["controller"]["runtime_verified"])

    def test_fabricated_input_evidence_is_rejected_before_build(self):
        engine = agent.BuildAgent(self.source, self.output, {}, self.successful_build)
        report = dict(self.report, evidence=[
            dict(item, quote="SDL_Fake") if item["role"] == "api" else item for item in self.report["evidence"]])
        with self.assertRaisesRegex(ValueError, "does not match"):
            engine.attempt_build("make", "game", report)
        self.assertEqual(len(engine.attempts), 0)

    def test_event_handlers_alone_do_not_establish_controller_support(self):
        engine = agent.BuildAgent(self.source, self.output, {}, self.successful_build)
        report = dict(self.report, evidence=self.report["evidence"][:1])
        with self.assertRaisesRegex(ValueError, "activation-guard"):
            engine.attempt_build("make", "game", report)
        self.assertEqual(len(engine.attempts), 0)

    def test_read_tools_reject_traversal_git_and_symlinks(self):
        engine = agent.BuildAgent(self.source, self.output, {})
        (self.source / "escape").symlink_to("/etc/passwd")
        for path in ("../secret", "/etc/passwd", ".git/config", "escape"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                engine.inspect_source(path)

    def test_package_symlinks_cannot_escape_to_host(self):
        def runner(directory):
            self.successful_build(directory)
            (directory / "files" / "package" / "secret").symlink_to("/etc/passwd")
            return 0
        engine = agent.BuildAgent(self.source, self.output, {}, runner)
        self.assertFalse(engine.attempt_build("make", "game", self.report)["success"])
        self.assertIsNone(engine.result)

    def test_attempt_budget_is_enforced(self):
        engine = agent.BuildAgent(self.source, self.output, {}, lambda _: 1)
        for _ in range(agent.MAX_ATTEMPTS):
            self.assertFalse(engine.attempt_build("make", "game", self.report)["success"])
        with self.assertRaisesRegex(RuntimeError, "exhausted"):
            engine.attempt_build("make", "game", self.report)

    def test_inspection_budget_leaves_room_for_build_and_resets_after_feedback(self):
        engine = agent.BuildAgent(self.source, self.output, {}, self.successful_build)
        for _ in range(20):
            engine.list_source()
        with self.assertRaisesRegex(RuntimeError, "Call attempt_build now"):
            engine.list_source()
        self.assertTrue(engine.attempt_build("make", "game", self.report)["success"])
        self.assertIn("input.c", engine.list_source())

    def test_container_has_no_network_credentials_or_writable_recipe(self):
        engine = agent.BuildAgent(self.source, self.output, {})
        directory = self.output / "attempt-1"
        directory.mkdir()
        with patch.dict(agent.os.environ, {"COPILOT_GITHUB_TOKEN": "secret"}), \
                patch.object(agent.subprocess, "run") as run:
            run.return_value.returncode = 1
            engine.run_container(directory)
        args = run.call_args.args[0]
        self.assertEqual(args[args.index("--network") + 1], "none")
        self.assertIn(f"{directory / 'recipe'}:/recipe:ro", args)
        self.assertNotIn("secret", " ".join(args))
        self.assertNotIn("/var/run/docker.sock", " ".join(args))

    async def test_failed_build_idle_turn_resumes_reasoning_with_error_context(self):
        count = 0
        prompts = []

        def runner(directory):
            nonlocal count
            count += 1
            if count == 1:
                (directory / "build.log").write_text("fatal error: missing config.h\n")
                return 2
            return self.successful_build(directory)

        engine = agent.BuildAgent(self.source, self.output, {}, runner)

        async def send(prompt, timeout):
            prompts.append((prompt, timeout))
            if len(prompts) == 1:
                engine.attempt_build("make", "game", self.report)
                return SimpleNamespace(data=SimpleNamespace(content="Build failed: config.h is missing."))
            self.assertIn("missing config.h", prompt)
            self.assertIn('"attempts_remaining": 4', prompt)
            self.assertEqual(len(engine.attempts), 1)
            engine.attempt_build("cmake -S . -B build && cmake --build build", "game", self.report)
            engine.finish("Generated missing configuration before building.")
            return None

        await agent.drive_reasoning(SimpleNamespace(send_and_wait=send), engine)
        self.assertEqual(len(prompts), 2)
        self.assertTrue(engine.published)
        self.assertEqual(len(engine.attempts), 2)
        self.assertLessEqual(prompts[1][1], prompts[0][1])
        events = [json.loads(line) for line in (self.output / "agent-report.jsonl").read_text().splitlines()]
        self.assertEqual(sum(event["event"] == "reasoning_resume" for event in events), 1)

    async def test_validated_build_without_finish_gets_a_followup_turn(self):
        engine = agent.BuildAgent(self.source, self.output, {}, self.successful_build)
        turns = 0

        async def send(prompt, timeout):
            nonlocal turns
            turns += 1
            if turns == 1:
                engine.attempt_build("make", "game", self.report)
            else:
                self.assertIn("call finish", prompt)
                engine.finish("Completed.")

        await agent.drive_reasoning(SimpleNamespace(send_and_wait=send), engine)
        self.assertEqual(turns, 2)
        self.assertEqual(len(engine.attempts), 1)

    async def test_reasoning_retries_cannot_reset_build_budget(self):
        engine = agent.BuildAgent(self.source, self.output, {}, lambda _: 1)
        turns = 0

        async def send(prompt, timeout):
            nonlocal turns
            turns += 1
            engine.attempt_build(f"make TRY={turns}", "game", self.report)

        with self.assertRaisesRegex(RuntimeError, "five unsuccessful"):
            await agent.drive_reasoning(SimpleNamespace(send_and_wait=send), engine)
        self.assertEqual(turns, 5)
        self.assertEqual(len(engine.attempts), 5)

    async def test_no_progress_turns_are_bounded_and_not_success(self):
        engine = agent.BuildAgent(self.source, self.output, {})
        turns = 0

        async def send(prompt, timeout):
            nonlocal turns
            turns += 1
            return SimpleNamespace(data=SimpleNamespace(content="Stopped without building."))

        with self.assertRaisesRegex(RuntimeError, "six turns"):
            await agent.drive_reasoning(SimpleNamespace(send_and_wait=send), engine)
        self.assertEqual(turns, agent.MAX_REASONING_TURNS)
        self.assertFalse(engine.published)

    async def test_expired_overall_deadline_does_not_restart_reasoning(self):
        engine = agent.BuildAgent(self.source, self.output, {})
        with patch.object(agent, "AGENT_TIMEOUT_SECONDS", 0):
            with self.assertRaisesRegex(TimeoutError, "deadline"):
                await agent.drive_reasoning(SimpleNamespace(), engine)

    def test_reported_empty_profile_fails_before_constructing_agent(self):
        for value in (None, "", "  "):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "no reasoning agent was started"):
                agent.load_target_profile(value)

    def test_empty_profile_entrypoint_reports_actionable_error_without_starting_agent(self):
        previous = agent.os.getcwd()
        try:
            agent.os.chdir(self.temporary.name)
            with patch.dict(agent.os.environ, {"TARGET_PROFILE": ""}), \
                    patch.object(agent, "BuildAgent") as factory, \
                    patch.object(agent.sys, "stderr", new=io.StringIO()) as errors:
                self.assertEqual(agent.main(), 1)
            factory.assert_not_called()
            self.assertIn("Update/restart Quiver", errors.getvalue())
            report = (Path(self.temporary.name) / "attempt-output" / "agent-error.txt").read_text()
            self.assertIn("no reasoning agent was started", report)
        finally:
            agent.os.chdir(previous)

    def test_profile_validation_requires_real_hardware_metadata(self):
        for value in ("not json", "null", "[]", '{"schema":1,"architecture":"aarch64"}'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                agent.load_target_profile(value)
        profile = {"schema": 1, "architecture": "aarch64", "display": {}, "controllers": []}
        self.assertEqual(agent.load_target_profile(json.dumps(profile)), profile)

    def test_source_fix_is_persistent_and_invalidates_previous_build(self):
        engine = agent.BuildAgent(self.source, self.output, {}, self.successful_build)
        engine.attempt_build("make", "game", self.report)
        engine.edit_source("input.c", "int input_enabled = 1;", "int input_enabled = 2;")
        self.assertIn("input_enabled = 2", (self.source / "input.c").read_text())
        self.assertIsNone(engine.result)
        self.assertFalse(engine.last_outcome["success"])
        with self.assertRaisesRegex(RuntimeError, "No validated build"):
            engine.finish("Done")

    def test_compiler_timeout_cannot_outlive_the_shared_round_deadline(self):
        engine = agent.BuildAgent(self.source, self.output, {})
        engine.deadline = agent.time.monotonic() + 10
        directory = self.output / "attempt-1"
        directory.mkdir()
        with patch.object(agent.subprocess, "run") as run:
            run.return_value.returncode = 1
            engine.run_container(directory)
        self.assertGreater(run.call_args.kwargs["timeout"], 0)
        self.assertLessEqual(run.call_args.kwargs["timeout"], 10)

    async def test_tool_budget_pushes_fork_then_starts_fresh_round_with_context(self):
        checkpoints = []
        store = SimpleNamespace(state=None, repository="builder/game", branch="quiver/test",
                                ensure_fork=lambda: None, restore=lambda: None)

        def checkpoint(engine, status):
            checkpoints.append((engine.round_number, status, engine.calls))
            store.state = {"round": engine.round_number, "summary": engine.last_summary,
                           "last_build": engine.last_outcome}
            return {"repository": store.repository, "branch": store.branch,
                    "commit": str(engine.round_number), "url": "https://github.com/builder/game/tree/quiver/test"}

        store.checkpoint = checkpoint
        deadlines = []

        async def run_round(engine):
            deadlines.append(engine.deadline)
            if engine.round_number == 1:
                engine.add_source("config.h", "#define KNULLI 1\n")
                engine.last_summary = "Added missing generated configuration."
                engine.last_outcome = {"success": False, "error": "config.h missing in old build"}
                engine.calls = agent.MAX_TOOL_CALLS
                raise agent.ToolBudgetExhausted("exhausted")
            self.assertEqual(engine.calls, 0)
            self.assertIn("missing generated configuration", engine.continuation["summary"])
            self.assertTrue((engine.source / "config.h").is_file())
            engine.runner = self.successful_build
            engine.attempt_build("make with config", "game", self.report)
            engine.finish("Compiled using the saved configuration fix.")

        await agent.run_fork_rounds(self.source, self.output, {}, store, run_round)
        self.assertEqual([item[1] for item in checkpoints], ["tool-budget-exhausted", "compiled-unverified"])
        self.assertEqual(deadlines[0], deadlines[1])
        report = json.loads((self.output / "package" / "quiver-agent-report.json").read_text())
        self.assertEqual(report["fork"]["commit"], "2")
        self.assertEqual(report["round"], 2)

    async def test_fork_continuation_rounds_are_bounded_and_progress_is_saved(self):
        checkpoints = []
        store = SimpleNamespace(state={"round": 8}, repository="builder/game", branch="quiver/test",
                                ensure_fork=lambda: {"round": 8}, restore=lambda: None)

        def checkpoint(engine, status):
            checkpoints.append(engine.round_number)
            store.state = {"round": engine.round_number}
            return {"url": "https://github.com/builder/game/tree/quiver/test"}

        store.checkpoint = checkpoint

        async def run_round(engine):
            raise agent.ToolBudgetExhausted("exhausted")

        with self.assertRaisesRegex(RuntimeError, "Three agent rounds exhausted"):
            await agent.run_fork_rounds(self.source, self.output, {}, store, run_round)
        self.assertEqual(checkpoints, [9, 10, 11])


if __name__ == "__main__":
    unittest.main()
