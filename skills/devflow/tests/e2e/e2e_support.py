"""Shared helpers for the DevFlow end-to-end suite.

These tests drive the public ``workflow_state.py`` CLI against a real temporary
project. They deliberately avoid mocking: artifacts are materialised from the
real templates, real project test commands are executed, and the final state is
validated through the same gates a user would hit.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path


E2E_DIR = Path(__file__).resolve().parent
SKILL_ROOT = E2E_DIR.parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
DEFAULT_WORKFLOW = SCRIPTS / "workflow_state.py"

# ``scripts/e2e.sh`` points this at the *installed* workflow script to prove the
# published artifact (not the repo copy) can complete a run.
WORKFLOW = Path(os.environ.get("DEVFLOW_E2E_WORKFLOW", str(DEFAULT_WORKFLOW)))

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from template_registry import required_artifacts  # noqa: E402
from agent_registry import HELPER_ROLES, STAGE_EXECUTION, STAGE_ROLES  # noqa: E402
from adapter_registry import (  # noqa: E402
    DISPATCH_TOOLS,
    RESEARCH_EXECUTORS,
    STAGE_EXECUTORS,
    TOPOLOGIES,
)


def run_workflow(*args, cwd=None, check=True, workflow=None):
    """Invoke the DevFlow state machine CLI and return the completed process."""
    command = [
        sys.executable,
        str(workflow or WORKFLOW),
        *map(str, args),
    ]
    return subprocess.run(
        command,
        cwd=cwd,
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        text=True,
        capture_output=True,
        check=check,
    )


def read_state(state_path):
    return json.loads(Path(state_path).read_text(encoding="utf-8"))


def init_state(
    root,
    slug,
    size,
    *,
    mode="auto",
    execution_mode="single-context",
    host_adapter=None,
    fallback_reason=None,
    coordinator_id="main",
):
    args = [
        "init",
        "--project-root", root,
        "--slug", slug,
        "--size", size,
        "--mode", mode,
        "--execution-mode", execution_mode,
        "--coordinator-id", coordinator_id,
    ]
    if host_adapter:
        args += ["--host-adapter", host_adapter]
    if fallback_reason:
        args += ["--fallback-reason", fallback_reason]
    return Path(run_workflow(*args).stdout.strip())


def complete_template(path, extra_replacements=None):
    """Fill every template slot in a materialised artifact with usable evidence."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    replacements = {
        "{{说明当前实现、关键模块、仓库相对路径、约束和非目标}}": "当前实现位于 `app.py`，仅修改该文件。",
        "{{动作导向的任务名}}": "实现目标行为",
        "{{无，或任务 ID}}": "无",
        "{{仓库相对路径}}": "app.py",
        "{{工作目录}}": ".",
        "{{项目原生验证命令}}": "python3 -m unittest -v",
        "{{明确写“通过”或“不通过”；只有无阻断问题时才能写“通过”}}": "通过，无阻断问题。",
        "{{无接口变动时写“无接口变动”；有新增、修改或删除时生成文档并写“已生成：`03-code/api-docs.md`”}}": "无接口变动",
        "{{无接口变动时写“无接口变动”；有新增、修改或删除时生成文档并写“已生成：`01-solo/api-docs.md`”}}": "无接口变动",
        "{{列出仓库实际已有的测试层级及证据路径/配置/命令；若已有 E2E，明确其覆盖边界；若没有，不要为流程强行引入新框架}}": "现有 `unittest` 与 `python3 -m unittest`；没有浏览器 E2E 框架。",
        "{{逐项记录“验收标准或风险 | 项目已有的合适测试层级 | 用例或命令 | 结果”；项目已有 E2E 且本次行为属于其覆盖边界时，必须包含相应 E2E 用例}}": "核心逻辑 | unit | `python3 -m unittest -v` | passed",
    }
    if extra_replacements:
        replacements.update(extra_replacements)
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = re.sub(r"\{\{[^{}]+\}\}", "已根据当前证据完成并验证。", text)
    path.write_text(text, encoding="utf-8")


def fill_stage_artifacts(state_path, stage, extra_replacements=None):
    state = read_state(state_path)
    artifact_root = Path(state["artifacts_dir"])
    for relative in required_artifacts(stage, False):
        complete_template(artifact_root / relative, extra_replacements)


def run_completed_stage(state_path, stage, extra_replacements=None):
    """prepare -> start -> fill artifacts -> finish completed for one stage."""
    run_workflow("prepare", "--state", state_path, "--stage", stage)
    run_workflow("start", "--state", state_path, "--stage", stage)
    fill_stage_artifacts(state_path, stage, extra_replacements)
    run_workflow(
        "finish", "--state", state_path, "--stage", stage, "--result", "completed"
    )


def assign_stage(state_path, stage, host, executor_id, team_name=None):
    """Register the isolated stage executor with the host-specific contract."""
    args = [
        "assign", "--state", state_path, "--stage", stage,
        "--role", STAGE_ROLES[stage],
        "--executor-type", STAGE_EXECUTORS[host],
        "--executor-id", executor_id,
        "--dispatch-tool", DISPATCH_TOOLS[host],
    ]
    if TOPOLOGIES[host] == "team":
        args += ["--team-name", team_name]
    return run_workflow(*args)


def record_helper(state_path, stage, host, executor_id, purpose, team_name=None):
    """Register a bounded read-only research helper for the current/next stage."""
    args = [
        "helper", "--state", state_path, "--stage", stage,
        "--role", sorted(HELPER_ROLES)[0],
        "--executor-type", RESEARCH_EXECUTORS[host],
        "--executor-id", executor_id,
        "--purpose", purpose,
        "--dispatch-tool", DISPATCH_TOOLS[host],
    ]
    if TOPOLOGIES[host] == "team":
        args += ["--team-name", team_name]
    return run_workflow(*args)
