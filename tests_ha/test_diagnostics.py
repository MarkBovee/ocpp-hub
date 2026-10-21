"""The diagnostics download must be safe to paste in a public issue."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ocpp_hub.const import CONF_VENDOR_PASSWORD, CONF_VENDOR_URL, CONF_VENDOR_USER
from custom_components.ocpp_hub.diagnostics import async_get_config_entry_diagnostics
from tests.fake_charger import FakeCharger
from tests_ha.conftest import state_of, wait_for


# Intent: diagnostics list the charger and the mirror but hide the password, user, address and serial number.
# Why: users paste diagnostics in public issues, and a vendor password or a charger serial there is a leak.
async def test_diagnostics_hide_secrets_and_identities(
    hass: HomeAssistant, setup_hub: Callable[..., Awaitable[MockConfigEntry]], connect_charger: Any
) -> None:
    entry = await setup_hub(
        {CONF_VENDOR_URL: "ws://127.0.0.1:1/ocpp", CONF_VENDOR_USER: "mirror-user", CONF_VENDOR_PASSWORD: "s3cret"}
    )
    charger: FakeCharger = await connect_charger(entry)
    await charger.boot()
    await wait_for(hass, lambda: state_of(hass, "binary_sensor.ocpp_hub_test0001_connected") == "on", "no entities")
    await wait_for(hass, lambda: entry.runtime_data.mirror.status("TEST0001").last_error, "mirror did not fail")

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    dump = json.dumps(diagnostics)
    for secret in ("s3cret", "mirror-user", "127.0.0.1:1", "TEST0001", "Connect call failed"):
        assert secret not in dump, secret
    assert diagnostics["mirror"] == "on" and diagnostics["chargers"][0]["info"]["model"] == "NEO_22T_A"
    assert diagnostics["mirror_links"][0]["connected"] is False and diagnostics["mirror_links"][0]["queue_length"] == 1
