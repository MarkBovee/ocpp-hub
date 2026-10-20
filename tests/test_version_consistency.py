"""The release version must be identical in pyproject.toml, manifest.json and the top CHANGELOG heading."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


# Intent: run the version check tool and require that it reports no drift.
# Why: CI fails on a mismatch, so a release can never carry two different versions.
def test_version_is_in_sync() -> None:
    completed = subprocess.run(
        [sys.executable, "tools/version.py", "check"], cwd=ROOT, capture_output=True, text=True, check=False
    )

    assert completed.returncode == 0, completed.stderr
