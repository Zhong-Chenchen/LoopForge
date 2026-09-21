"""End-to-end tests for the DevFlow state machine.

Unlike the CLI regression tests, these tests do not synthesise state by hand:
they create a real project, execute real project tests, fill the real templates,
walk every gate and failure branch through the public CLI, and verify the final
artifact set with the same ``validate`` gate a user would run.

The suite runs against the repository workflow by default. ``scripts/e2e.sh``
re-runs the small flow with ``DEVFLOW_E2E_WORKFLOW`` pointed at the workflow
script shipped by the built wheel, proving the published artifact is runnable.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from e2e_support import (
    STAGE_EXECUTION,
    assign_stage,
    fill_stage_artifacts,
    init_state,
    read_state,
    record_helper,
    run_completed_stage,
    run_workflow,
    WORKFLOW,
)


def write_sample_project(root):
    (root / "app.py").write_text(
        "def normalize(value):\n    return value.strip().lower()\n",
        encoding="utf-8",
    )
    (root / "test_app.py").write_text(
        "import unittest\nfrom app import normalize\n\n"
        "class NormalizeTest(unittest.TestCase):\n"
        "    def test_normalize(self):\n"
        "        self.assertEqual('hello', normalize(' Hello '))\n",
        encoding="utf-8",
    )


def run_project_tests(root):
    return subprocess.run(
        [sys.executable, "-m", "unittest", "-v"],
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    )


def reach_review_ready(state_path):
    """Drive REQUIREMENT (confirmed), DESIGN and IMPLEMENT so REVIEW is next."""
    run_completed_stage(state_path, "REQUIREMENT")
    run_workflow(
        "approve", "--state", state_path, "--stage", "REQUIREMENT", "--user-confirmed"
    )
    run_completed_stage(state_path, "DESIGN")
    run_completed_stage(state_path, "IMPLEMENT")


class WorkflowEndToEndTests(unittest.TestCase):
    def test_medium_flow_executes_real_project_tests(self):
        """A medium task reaches verified completion using real code and tests."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_sample_project(root)
            self.assertIn("OK", run_project_tests(root).stderr)

            state_path = init_state(
                root, "medium-e2e", "medium",
                fallback_reason="CI e2e profile has no agent host",
            )
            run_completed_stage(state_path, "REQUIREMENT")
            self.assertEqual("awaiting_approval", read_state(state_path)["status"])
            run_workflow(
                "approve", "--state", state_path, "--stage", "REQUIREMENT",
                "--user-confirmed",
            )
            run_completed_stage(state_path, "DESIGN")
            run_completed_stage(state_path, "IMPLEMENT")
            run_completed_stage(state_path, "REVIEW")

            run_workflow("prepare", "--state", state_path, "--stage", "TEST")
            run_workflow("start", "--state", state_path, "--stage", "TEST")
            project_run = run_project_tests(root)
            self.assertIn("OK", project_run.stderr)
            report = Path(read_state(state_path)["artifacts_dir"]) / "04-test/test-report.md"
            fill_stage_artifacts(state_path, "TEST", {
                "{{记录实际执行的完整命令和工作目录}}": (
                    f"`{sys.executable} -m unittest -v`（工作目录 `{root}`）"
                ),
                "{{区分通过、失败和阻断，并附关键证据}}": (
                    "passed；真实执行输出结尾：`"
                    + project_run.stderr.strip().splitlines()[-1] + "`"
                ),
            })
            self.assertIn(
                f"`{sys.executable} -m unittest -v`", report.read_text(encoding="utf-8")
            )
            run_workflow(
                "finish", "--state", state_path, "--stage", "TEST", "--result", "completed"
            )

            resume = json.loads(run_workflow("resume", "--state", state_path).stdout)
            self.assertEqual("assess-knowledge", resume["action"])
            run_workflow(
                "decide-knowledge", "--state", state_path, "--decision", "skip",
                "--reason", "只有常规变更和测试结果，没有新增可复用约束",
            )
            run_completed_stage(state_path, "SUMMARY")

            validation = run_workflow("validate", "--state", state_path)
            self.assertIn("OK", validation.stdout)
            state = read_state(state_path)
            self.assertEqual("completed", state["status"])
            self.assertEqual("skipped", state["stages"]["KNOWLEDGE"]["status"])

    def test_requirement_confirmation_gate_blocks_unsafe_progress(self):
        """An unconfirmed requirement must not advance to design."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = init_state(
                root, "gate", "medium",
                fallback_reason="CI e2e profile has no agent host",
            )
            run_completed_stage(state_path, "REQUIREMENT")
            state = read_state(state_path)
            self.assertEqual("awaiting_approval", state["status"])
            self.assertEqual("REQUIREMENT", state["awaiting_approval"])

            premature = run_workflow(
                "prepare", "--state", state_path, "--stage", "DESIGN", check=False
            )
            self.assertNotEqual(0, premature.returncode)
            self.assertIn("approval required for REQUIREMENT", premature.stderr)

            unconfirmed = run_workflow(
                "approve", "--state", state_path, "--stage", "REQUIREMENT", check=False
            )
            self.assertNotEqual(0, unconfirmed.returncode)
            self.assertIn("requires explicit user confirmation", unconfirmed.stderr)

            resume = json.loads(run_workflow("resume", "--state", state_path).stdout)
            self.assertEqual("approve", resume["action"])
            self.assertTrue(resume["requires_user_confirmation"])
            self.assertIn("--user-confirmed", resume["command"])

            run_workflow(
                "approve", "--state", state_path, "--stage", "REQUIREMENT",
                "--user-confirmed",
            )
            self.assertEqual("in_progress", read_state(state_path)["status"])

    def test_manual_mode_gates_require_explicit_approval(self):
        """DESIGN, REVIEW and TEST must all be approved in manual mode."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = init_state(
                root, "manual", "medium", mode="manual",
                fallback_reason="CI e2e profile has no agent host",
            )
            run_completed_stage(state_path, "REQUIREMENT")
            run_workflow(
                "approve", "--state", state_path, "--stage", "REQUIREMENT",
                "--user-confirmed",
            )

            run_completed_stage(state_path, "DESIGN")
            state = read_state(state_path)
            self.assertEqual("awaiting_approval", state["status"])
            self.assertEqual("DESIGN", state["awaiting_approval"])
            blocked = run_workflow(
                "prepare", "--state", state_path, "--stage", "IMPLEMENT", check=False
            )
            self.assertNotEqual(0, blocked.returncode)
            self.assertIn("approval required for DESIGN", blocked.stderr)
            run_workflow("approve", "--state", state_path, "--stage", "DESIGN")

            run_completed_stage(state_path, "IMPLEMENT")
            run_completed_stage(state_path, "REVIEW")
            self.assertEqual("REVIEW", read_state(state_path)["awaiting_approval"])
            run_workflow("approve", "--state", state_path, "--stage", "REVIEW")

            run_completed_stage(state_path, "TEST")
            self.assertEqual("TEST", read_state(state_path)["awaiting_approval"])
            run_workflow("approve", "--state", state_path, "--stage", "TEST")
            self.assertEqual("in_progress", read_state(state_path)["status"])

    def test_review_failure_returns_to_implement(self):
        """A failed review sends the workflow back to implementation."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = init_state(
                root, "review-fail", "medium",
                fallback_reason="CI e2e profile has no agent host",
            )
            reach_review_ready(state_path)
            run_workflow("prepare", "--state", state_path, "--stage", "REVIEW")
            run_workflow("start", "--state", state_path, "--stage", "REVIEW")
            run_workflow(
                "finish", "--state", state_path, "--stage", "REVIEW", "--result", "failed"
            )
            state = read_state(state_path)
            self.assertEqual("IMPLEMENT", state["next_stage"])
            self.assertEqual("pending", state["stages"]["IMPLEMENT"]["status"])
            self.assertEqual("failed", state["stages"]["REVIEW"]["status"])
            self.assertEqual("in_progress", state["status"])

    def test_repeated_stage_failure_blocks_workflow(self):
        """Two failures of the same non-review stage block the workflow."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = init_state(
                root, "retry-limit", "medium",
                fallback_reason="CI e2e profile has no agent host",
            )
            run_completed_stage(state_path, "REQUIREMENT")
            run_workflow(
                "approve", "--state", state_path, "--stage", "REQUIREMENT",
                "--user-confirmed",
            )
            run_completed_stage(state_path, "DESIGN")

            for attempt in (1, 2):
                run_workflow("prepare", "--state", state_path, "--stage", "IMPLEMENT")
                run_workflow("start", "--state", state_path, "--stage", "IMPLEMENT")
                run_workflow(
                    "finish", "--state", state_path, "--stage", "IMPLEMENT",
                    "--result", "failed",
                )
                state = read_state(state_path)
                if attempt == 1:
                    self.assertEqual("IMPLEMENT", state["next_stage"])
                    self.assertEqual("in_progress", state["status"])

            self.assertEqual(2, state["stages"]["IMPLEMENT"]["retry_count"])
            self.assertEqual("blocked", state["status"])
            self.assertIsNone(state["next_stage"])

    def test_solo_overflow_upgrades_to_medium_route(self):
        """An overflowing solo task is re-routed through the medium stages."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = init_state(root, "overflow", "small")
            run_workflow("prepare", "--state", state_path, "--stage", "SOLO")
            run_workflow("start", "--state", state_path, "--stage", "SOLO")
            run_workflow(
                "finish", "--state", state_path, "--stage", "SOLO",
                "--result", "overflow", "--host-adapter", "codebuddy",
            )
            state = read_state(state_path)
            self.assertEqual("medium", state["size_class"])
            self.assertEqual(
                ["REQUIREMENT", "DESIGN", "IMPLEMENT", "REVIEW", "TEST", "KNOWLEDGE", "SUMMARY"],
                state["route"],
            )
            self.assertEqual("REQUIREMENT", state["next_stage"])
            self.assertEqual("in_progress", state["status"])
            self.assertEqual("skipped", state["stages"]["SOLO"]["status"])
            self.assertEqual("isolated", state["execution_mode"])
            self.assertEqual("codebuddy", state["host_adapter"])

    def test_resume_is_read_only_and_reports_next_action(self):
        """resume must report the next action without mutating the state file."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = init_state(
                root, "resume-readonly", "medium",
                fallback_reason="CI e2e profile has no agent host",
            )
            before = state_path.read_bytes()
            resume = json.loads(run_workflow("resume", "--state", state_path).stdout)
            self.assertTrue(resume["ok"])
            self.assertEqual("prepare", resume["action"])
            self.assertEqual("REQUIREMENT", resume["stage"])
            self.assertEqual(before, state_path.read_bytes())

    def test_finish_rejects_missing_artifact(self):
        """finish must not accept a completed stage whose artifact is gone."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = init_state(root, "artifact-gate", "small")
            run_workflow("prepare", "--state", state_path, "--stage", "SOLO")
            run_workflow("start", "--state", state_path, "--stage", "SOLO")
            report = (
                Path(read_state(state_path)["artifacts_dir"]) / "01-solo/solo-report.md"
            )
            report.unlink()
            missing = run_workflow(
                "finish", "--state", state_path, "--stage", "SOLO",
                "--result", "completed", check=False,
            )
            self.assertNotEqual(0, missing.returncode)
            self.assertIn("required artifacts missing", missing.stderr)
            self.assertEqual(
                "in_progress", read_state(state_path)["stages"]["SOLO"]["status"]
            )


def complete_medium_isolated(root, slug, host, coordinator_id="main-1"):
    """Drive the default isolated medium route with real executor registration."""
    state_path = init_state(
        root, slug, "medium",
        execution_mode="isolated", host_adapter=host, coordinator_id=coordinator_id,
    )
    team_name = read_state(state_path).get("team_name")
    for stage in read_state(state_path)["route"]:
        if stage == "KNOWLEDGE":
            run_workflow(
                "decide-knowledge", "--state", state_path, "--decision", "skip",
                "--reason", "只有常规变更和测试结果，没有新增可复用约束",
            )
            continue
        run_workflow("prepare", "--state", state_path, "--stage", stage)
        if STAGE_EXECUTION[stage] != "coordinator":
            assign_stage(state_path, stage, host, f"agent-{stage.lower()}", team_name)
            if stage == "DESIGN":
                record_helper(
                    state_path, stage, host, "helper-design",
                    "核对设计与需求输入", team_name,
                )
        run_workflow("start", "--state", state_path, "--stage", stage)
        fill_stage_artifacts(state_path, stage)
        run_workflow(
            "finish", "--state", state_path, "--stage", stage, "--result", "completed"
        )
        if stage == "REQUIREMENT":
            run_workflow(
                "approve", "--state", state_path, "--stage", "REQUIREMENT",
                "--user-confirmed",
            )
    return state_path, team_name


class IsolatedDispatchEndToEndTests(unittest.TestCase):
    def test_medium_isolated_team_dispatch_completes(self):
        """The default medium route registers a Team executor and helper."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path, team_name = complete_medium_isolated(
                root, "isolated-team", "codebuddy"
            )
            validation = run_workflow("validate", "--state", state_path)
            self.assertIn("OK", validation.stdout)
            state = read_state(state_path)
            self.assertEqual("completed", state["status"])
            self.assertTrue(team_name)
            for stage in ("DESIGN", "IMPLEMENT", "REVIEW", "TEST"):
                self.assertEqual(team_name, state["stages"][stage]["team_name"])
                self.assertTrue(state["stages"][stage]["executor_id"])
            self.assertEqual("agent-design", state["stages"]["DESIGN"]["executor_id"])
            helper = state["stages"]["DESIGN"]["helpers"][0]
            self.assertEqual("helper-design", helper["executor_id"])
            self.assertEqual(team_name, helper["team_name"])
            self.assertEqual("task", helper["dispatch_tool"])

    def test_medium_isolated_spawn_dispatch_completes(self):
        """The default medium route also works for spawn-topology hosts."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path, team_name = complete_medium_isolated(
                root, "isolated-spawn", "claude"
            )
            self.assertIsNone(team_name)
            validation = run_workflow("validate", "--state", state_path)
            self.assertIn("OK", validation.stdout)
            state = read_state(state_path)
            self.assertEqual("completed", state["status"])
            self.assertEqual(
                "devflow-stage-executor", state["stages"]["TEST"]["executor_type"]
            )
            self.assertIsNone(state["stages"]["TEST"]["team_name"])

    def test_fresh_team_resume_rotates_team_and_reassigns(self):
        """--fresh-team rotates the Team and clears stale executor bindings."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = init_state(
                root, "rotate", "medium",
                execution_mode="isolated", host_adapter="codebuddy",
                coordinator_id="main-1",
            )
            old_team = read_state(state_path)["team_name"]
            run_completed_stage(state_path, "REQUIREMENT")
            run_workflow(
                "approve", "--state", state_path, "--stage", "REQUIREMENT",
                "--user-confirmed",
            )
            run_workflow("prepare", "--state", state_path, "--stage", "DESIGN")
            assign_stage(state_path, "DESIGN", "codebuddy", "agent-design", old_team)

            resume = json.loads(run_workflow(
                "resume", "--state", state_path, "--fresh-team"
            ).stdout)
            self.assertEqual("rotate-team", resume["action"])
            run_workflow("rotate-team", "--state", state_path)

            state = read_state(state_path)
            new_team = state["team_name"]
            self.assertNotEqual(old_team, new_team)
            self.assertIsNone(state["stages"]["DESIGN"]["executor_id"])

            assign_stage(state_path, "DESIGN", "codebuddy", "agent-design", new_team)
            run_workflow("start", "--state", state_path, "--stage", "DESIGN")
            fill_stage_artifacts(state_path, "DESIGN")
            run_workflow(
                "finish", "--state", state_path, "--stage", "DESIGN",
                "--result", "completed",
            )
            validation = run_workflow("validate", "--state", state_path)
            self.assertIn("OK", validation.stdout)

    def test_test_stage_recovers_from_real_failing_command(self):
        """A real failing project test fails TEST, then a fix completes the run."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_sample_project(root)
            (root / "app.py").write_text(
                "def normalize(value):\n    return value\n", encoding="utf-8"
            )
            state_path = init_state(
                root, "real-failure", "medium",
                fallback_reason="CI e2e profile has no agent host",
            )
            reach_review_ready(state_path)
            run_completed_stage(state_path, "REVIEW")

            run_workflow("prepare", "--state", state_path, "--stage", "TEST")
            run_workflow("start", "--state", state_path, "--stage", "TEST")
            failed = subprocess.run(
                [sys.executable, "-m", "unittest"],
                cwd=root, text=True, capture_output=True,
            )
            self.assertNotEqual(0, failed.returncode)
            run_workflow(
                "finish", "--state", state_path, "--stage", "TEST", "--result", "failed"
            )
            state = read_state(state_path)
            self.assertEqual("TEST", state["next_stage"])
            self.assertEqual("in_progress", state["status"])

            write_sample_project(root)
            self.assertIn("OK", run_project_tests(root).stderr)
            run_workflow("prepare", "--state", state_path, "--stage", "TEST")
            run_workflow("start", "--state", state_path, "--stage", "TEST")
            fill_stage_artifacts(state_path, "TEST", {
                "{{记录实际执行的完整命令和工作目录}}": (
                    f"`{sys.executable} -m unittest -v`（工作目录 `{root}`）"
                ),
                "{{区分通过、失败和阻断，并附关键证据}}": "failed -> passed；修复后真实执行通过",
            })
            run_workflow(
                "finish", "--state", state_path, "--stage", "TEST", "--result", "completed"
            )
            run_workflow(
                "decide-knowledge", "--state", state_path, "--decision", "skip",
                "--reason", "只有常规变更和测试结果",
            )
            run_completed_stage(state_path, "SUMMARY")
            validation = run_workflow("validate", "--state", state_path)
            self.assertIn("OK", validation.stdout)
            self.assertEqual("completed", read_state(state_path)["status"])

    def test_isolated_start_requires_registered_executor(self):
        """In isolated mode a stage cannot start before its executor is assigned."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = init_state(
                root, "executor-guard", "medium",
                execution_mode="isolated", host_adapter="codebuddy",
                coordinator_id="main-1",
            )
            run_completed_stage(state_path, "REQUIREMENT")
            run_workflow(
                "approve", "--state", state_path, "--stage", "REQUIREMENT",
                "--user-confirmed",
            )
            run_workflow("prepare", "--state", state_path, "--stage", "DESIGN")
            blocked = run_workflow(
                "start", "--state", state_path, "--stage", "DESIGN", check=False
            )
            self.assertNotEqual(0, blocked.returncode)
            self.assertIn(
                "configure the required devflow-architect executor", blocked.stderr
            )

    def test_isolated_assign_rejects_wrong_team(self):
        """Team-topology assignment must use the workflow team name."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = init_state(
                root, "team-guard", "medium",
                execution_mode="isolated", host_adapter="codebuddy",
                coordinator_id="main-1",
            )
            run_completed_stage(state_path, "REQUIREMENT")
            run_workflow(
                "approve", "--state", state_path, "--stage", "REQUIREMENT",
                "--user-confirmed",
            )
            run_workflow("prepare", "--state", state_path, "--stage", "DESIGN")
            wrong = run_workflow(
                "assign", "--state", state_path, "--stage", "DESIGN",
                "--role", "devflow-architect",
                "--executor-type", "devflow-stage-team-member",
                "--executor-id", "agent-design",
                "--dispatch-tool", "task",
                "--team-name", "wrong-team",
                check=False,
            )
            self.assertNotEqual(0, wrong.returncode)
            self.assertIn("require --team-name", wrong.stderr)


class InstalledWorkflowEndToEndTests(unittest.TestCase):
    def test_small_flow_completes_with_workflow_under_test(self):
        """A small flow completes with whatever workflow script is under test."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            write_sample_project(root)
            state_path = init_state(root, "installed-e2e", "small")
            run_completed_stage(state_path, "SOLO")
            run_completed_stage(state_path, "SUMMARY")
            validation = run_workflow("validate", "--state", state_path)
            self.assertIn("OK", validation.stdout)
            self.assertEqual("completed", read_state(state_path)["status"])
            self.assertTrue(WORKFLOW.is_file())


if __name__ == "__main__":
    unittest.main()
