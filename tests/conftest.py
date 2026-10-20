"""Test setup: load the HA-free core as the package ``ocpp_hub_core`` without importing Home Assistant.

Importing ``custom_components.ocpp_hub.core`` would first run ``custom_components/ocpp_hub/__init__.py``, which imports
Home Assistant. Loading the core directory by path under its own package name keeps its relative imports working and
proves it needs nothing from Home Assistant.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "ocpp_hub" / "core"


# Intent: register the core package in sys.modules under the name ``ocpp_hub_core``.
# Why: tests import ``ocpp_hub_core.frames`` the same way the integration imports ``.core.frames``.
def _load_core() -> None:
    if "ocpp_hub_core" in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(
        "ocpp_hub_core", CORE_DIR / "__init__.py", submodule_search_locations=[str(CORE_DIR)]
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["ocpp_hub_core"] = module
    spec.loader.exec_module(module)


_load_core()
