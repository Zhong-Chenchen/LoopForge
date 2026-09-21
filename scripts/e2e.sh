#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "[1/3] Workflow behavior E2E (repository copy)"
python3 -m unittest discover \
  -s "$ROOT_DIR/skills/devflow/tests/e2e" \
  -p 'test_workflow_e2e.py' -v

echo "[2/3] Build and install the published wheel"
WHEEL_DIR="$TMP_DIR/wheel"
VENV_DIR="$TMP_DIR/venv"
mkdir -p "$WHEEL_DIR"
python3 -m pip wheel "$ROOT_DIR" --no-deps -w "$WHEEL_DIR" >/dev/null
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install "$WHEEL_DIR"/*.whl >/dev/null

echo "[3/3] Installed Portable workflow E2E"
PROJECT="$TMP_DIR/installed-project"
mkdir -p "$PROJECT"
"$VENV_DIR/bin/loopforge" skills install codebuddy --project-root "$PROJECT" >/dev/null
INSTALLED_WORKFLOW="$PROJECT/.codebuddy/skills/devflow/scripts/workflow_state.py"
test -f "$INSTALLED_WORKFLOW"
DEVFLOW_E2E_WORKFLOW="$INSTALLED_WORKFLOW" \
  python3 -m unittest discover \
    -s "$ROOT_DIR/skills/devflow/tests/e2e" \
    -p 'test_workflow_e2e.py' -k InstalledWorkflowEndToEndTests -v

echo "End-to-end suite passed."
