from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
NODE_TEST_DIR = ROOT / "tests" / "node"
NODE_TEST_FILES = sorted(str(path) for path in NODE_TEST_DIR.glob("*.test.js"))


def test_node_frontend_suites() -> None:
    assert NODE_TEST_FILES, "Expected at least one node frontend test file"
    result = subprocess.run(
        ["node", "--test", *NODE_TEST_FILES],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        output = "\n".join(part for part in (result.stdout, result.stderr) if part)
        pytest.fail(f"Node frontend suite failed:\n{output}")
