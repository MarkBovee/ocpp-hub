"""Runs a write to a charger for an entity and turns its failures into errors Home Assistant can show."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .core.control import ChargerControl, ControlError
from .runtime import HubRuntime


# Run one write on the live control of a charger.
# A charger that is not connected, a refusal and a failure each become a HomeAssistantError with a translated text, so
# the owner sees why nothing happened instead of a button that did nothing.
async def run_write(runtime: HubRuntime, cp_id: str, write: Callable[[ChargerControl], Awaitable[object]]) -> None:
    control = runtime.central.control(cp_id)
    if control is None:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="control_not_connected")
    try:
        await write(control)
    except ControlError as error:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key=f"control_{error.code}",
            translation_placeholders={"detail": error.detail},
        ) from error
