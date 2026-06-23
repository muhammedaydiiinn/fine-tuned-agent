#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

echo "[agent-backend] running tests"
PYTHONPATH=services/agent-backend python3 -m pytest -q services/agent-backend/tests

echo "[voice-runtime] running tests"
PYTHONPATH=services/voice-runtime python3 -m pytest -q services/voice-runtime/tests

echo "[supervisor-panel] running tests"
PYTHONPATH=services/supervisor-panel python3 -m pytest -q services/supervisor-panel/tests
