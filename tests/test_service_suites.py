from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SERVICE_SUITES = [
    ("agent-backend", ROOT / "services" / "agent-backend" / "tests"),
    ("voice-runtime", ROOT / "services" / "voice-runtime" / "tests"),
    ("supervisor-panel", ROOT / "services" / "supervisor-panel" / "tests"),
]


@pytest.mark.parametrize(("service_name", "test_path"), SERVICE_SUITES)
def test_service_suite(service_name: str, test_path: Path) -> None:
    env = os.environ.copy()
    service_root = str(test_path.parent)
    pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        service_root if not pythonpath else f"{service_root}{os.pathsep}{pythonpath}"
    )

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(test_path)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        output = "\n".join(part for part in (result.stdout, result.stderr) if part)
        pytest.fail(f"{service_name} suite failed:\n{output}")
