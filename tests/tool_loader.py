"""Load a script from tools/ by path, because tools/ is not a package."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

TOOLS = Path(__file__).resolve().parents[1] / "tools"


# Intent: import tools/<name>.py as a module so tests exercise the real functions.
# Why: the tools are scripts; registering the module in sys.modules first lets dataclasses defined in them work.
def load_tool(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
