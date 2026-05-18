#!/usr/bin/env bash
# Verify a meta-workflow-produced subsystem against the goal's Phase 1
# acceptance criteria:
#
#   1. `python -c "from <namespace>.<subsystem> import *"` succeeds
#   2. The generated `test_<subsystem>.py` passes under pytest
#
# Usage:
#   ./scripts/verify-subsystem.sh <subsystem> [namespace]
#
# Defaults namespace to `smithers_py` (the canonical hand-coded path).
# For comparison demo subsystems, pass `smithers_py_meta`.
#
# Examples:
#   ./scripts/verify-subsystem.sh serve
#   ./scripts/verify-subsystem.sh memory smithers_py_meta
#
# Exits 0 on success, non-zero on any failure.
set -euo pipefail

SUBSYSTEM="${1:-}"
NAMESPACE="${2:-smithers_py}"
if [[ -z "$SUBSYSTEM" ]]; then
  echo "usage: $0 <subsystem> [namespace]" >&2
  exit 2
fi

FORK="/Users/luis/smithers"
VENV_PY="$FORK/smithers_py/.venv/bin/python"
SUBSYS_DIR="$FORK/$NAMESPACE/$SUBSYSTEM"
TEST_FILE="$SUBSYS_DIR/test_${SUBSYSTEM}.py"

if [[ ! -d "$SUBSYS_DIR" ]]; then
  echo "FAIL: $SUBSYS_DIR does not exist" >&2
  exit 1
fi

echo "=== Phase 1 acceptance for ${NAMESPACE}.${SUBSYSTEM} ==="

# Criterion 1: import surface
cd "$FORK"
echo -n "[1/2] import ${NAMESPACE}.${SUBSYSTEM} ... "
if PYTHONPATH=. "$VENV_PY" -c "from ${NAMESPACE}.${SUBSYSTEM} import *" 2>&1; then
  echo "PASS"
else
  echo "FAIL"
  exit 1
fi

# Criterion 2: tests
echo -n "[2/2] pytest test_${SUBSYSTEM}.py ... "
if [[ ! -f "$TEST_FILE" ]]; then
  echo "FAIL: $TEST_FILE missing"
  exit 1
fi
cd "$FORK"
if PYTHONPATH=. "$VENV_PY" -m pytest "$NAMESPACE/$SUBSYSTEM/test_${SUBSYSTEM}.py" -q 2>&1 | tail -5; then
  echo "PASS"
else
  echo "FAIL"
  exit 1
fi

echo
echo "=== both acceptance criteria pass for ${NAMESPACE}.${SUBSYSTEM} ==="
