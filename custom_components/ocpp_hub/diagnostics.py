"""Diagnostics for support: the entry, the chargers and the mirror, with identities and secrets redacted."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_VENDOR_PASSWORD, CONF_VENDOR_URL, CONF_VENDOR_USER
from .core.mirror_model import MirrorStatus
from .core.model import ChargerSnapshot
from .runtime import OcppHubConfigEntry

# Everything that identifies a person's installation or opens the vendor backend. Diagnostics get pasted in public
# issues, so a charger serial number, the vendor address and any error text (which can hold addresses) are hidden too.
TO_REDACT = {CONF_VENDOR_URL, CONF_VENDOR_USER, CONF_VENDOR_PASSWORD, "serial", "last_error"}


# A snapshot as plain data that can be written as JSON.
# The charger id is left out on purpose; chargers are listed by position.
def _snapshot_data(snapshot: ChargerSnapshot) -> dict[str, Any]:
    return {
        "connected": snapshot.connected,
        "protocol": snapshot.protocol,
        "info": {
            "vendor": snapshot.info.vendor,
            "model": snapshot.info.model,
            "serial": snapshot.info.serial,
            "firmware": snapshot.info.firmware,
        },
        "features": sorted(snapshot.features),
        "configuration": dict(snapshot.configuration),
        "connectors": {
            connector_id: {"status": connector.status, "error_code": connector.error_code}
            for connector_id, connector in snapshot.connectors.items()
        },
        "last_seen": snapshot.last_seen.isoformat() if snapshot.last_seen else None,
    }


# A mirror status as plain data.
def _mirror_data(status: MirrorStatus) -> dict[str, Any]:
    return {
        "connected": status.connected,
        "queue_length": status.queue_length,
        "last_success": status.last_success.isoformat() if status.last_success else None,
        "last_error": status.last_error,
        "dropped_frames": status.dropped_frames,
        "unmapped_transactions": status.unmapped_transactions,
    }


# Describe one config entry for a diagnostics download.
async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: OcppHubConfigEntry) -> dict[str, Any]:
    runtime = entry.runtime_data
    mirror_statuses = runtime.mirror.statuses() if runtime.mirror is not None else {}
    return async_redact_data(
        {
            "entry": {"data": dict(entry.data), "options": dict(entry.options)},
            "port": runtime.central.port,
            "mirror": "on" if runtime.mirror is not None else "off",
            "chargers": [_snapshot_data(snapshot) for snapshot in runtime.listener.snapshots.values()],
            "mirror_links": [_mirror_data(status) for status in mirror_statuses.values()],
        },
        TO_REDACT,
    )
