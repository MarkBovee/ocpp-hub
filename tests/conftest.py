"""Test setup: load the HA-free core as the package ``ocpp_hub_core`` without importing Home Assistant.

Importing ``custom_components.ocpp_hub.core`` would first run ``custom_components/ocpp_hub/__init__.py``, which imports
Home Assistant. Loading the core directory by path under its own package name keeps its relative imports working and
proves it needs nothing from Home Assistant.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

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


class Hub:
    """A running central system with a recording listener and the fake chargers connected to it."""

    # Keep the central system, its listener and a list of chargers to close at the end.
    def __init__(self, central: Any, listener: Any) -> None:
        self.central = central
        self.listener = listener
        self.chargers: list[Any] = []

    # Connect a new fake charger to the hub and register it for cleanup.
    async def charger(self, cp_id: str = "TEST0001", subprotocol: str = "ocpp1.6") -> Any:
        from tests.fake_charger import FakeCharger

        charger = FakeCharger(self.central.port, cp_id, subprotocol)
        await charger.connect()
        self.chargers.append(charger)
        return charger


# Intent: factory fixture that starts a central system on a free port with test-friendly timings.
# Why: most tests do not want the automatic post-boot setup, so it is delayed for an hour unless a test asks otherwise.
@pytest.fixture
async def start_hub() -> AsyncIterator[Callable[..., Awaitable[Hub]]]:
    from ocpp_hub_core.central import CentralSystem
    from ocpp_hub_core.session import SessionSettings

    from tests.fake_charger import RecordingListener

    hubs: list[Hub] = []

    # Intent: start one hub with the given setting overrides and any extra listeners, such as the vendor mirror.
    # Why: tests differ only in a few timings, so overrides keep each test short.
    async def _start(*extra_listeners: Any, **overrides: Any) -> Hub:
        listener = RecordingListener()
        settings = SessionSettings(**{"setup_delay_s": 3600.0, "call_timeout_s": 1.0, **overrides})
        central = CentralSystem([listener, *extra_listeners], settings)
        await central.start("127.0.0.1", 0)
        hub = Hub(central, listener)
        hubs.append(hub)
        return hub

    yield _start
    for hub in hubs:
        for charger in hub.chargers:
            await charger.close()
        await hub.central.stop()
