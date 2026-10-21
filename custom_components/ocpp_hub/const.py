"""Constants shared by the Home Assistant layer of OCPP Hub."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "ocpp_hub"

CONF_PORT = "port"
# 9000 is the port the OCPP integration of the HACS community uses, so a charger configured for it needs no change.
DEFAULT_PORT = 9000
# The server listens on every interface: the charger is somewhere else on the network.
BIND_HOST = "0.0.0.0"

# Options of the vendor mirror. The URL holds no credentials; user and password are separate so the password can be
# redacted everywhere.
CONF_VENDOR_URL = "vendor_url"
CONF_VENDOR_USER = "vendor_user"
CONF_VENDOR_PASSWORD = "vendor_password"
# What happens to the vendor's commands: "reject" (default) or "forward" (see core/mirror_forward.py).
CONF_VENDOR_COMMANDS = "vendor_commands"

# The highest current per phase the hub may ask a charger for. The owner sets it below the installer's own limit.
CONF_MAX_CURRENT = "max_current"
DEFAULT_MAX_CURRENT = 16

# The service that returns the charge history.
SERVICE_GET_SESSIONS = "get_sessions"
# The service that installs a daily schedule of charge limits.
SERVICE_SET_SCHEDULE = "set_schedule"

# The logger of this package. Home Assistant often runs with warnings only, which would hide the connection and boot
# lines this integration is judged by, so setup raises this one logger to INFO.
PACKAGE_LOGGER = "custom_components.ocpp_hub"

# Written next to the code by tools/deploy_ha.py: the commit the running code comes from.
BUILD_FILE = "BUILD"

# The mirror reports no events, so its status entities ask for the current status at this interval.
MIRROR_POLL_INTERVAL = timedelta(seconds=10)


# The id of the notification that says a charger connected. One per charger, so a charger that reconnects updates its
# own notification instead of adding a new one every time.
def charger_notification_id(cp_id: str) -> str:
    return f"{DOMAIN}_{cp_id}_connected"


# The dispatcher signal that announces a charger whose entities should now exist (it has sent its BootNotification).
# One signal per config entry, so a second entry could never receive another one's chargers.
def new_charger_signal(entry_id: str) -> str:
    return f"{DOMAIN}_new_charger_{entry_id}"


# The dispatcher signal that carries every new snapshot of one charger to its entities.
def snapshot_signal(entry_id: str, cp_id: str) -> str:
    return f"{DOMAIN}_snapshot_{entry_id}_{cp_id}"


# The dispatcher signal that says a charge of one charger finished and the history changed.
def session_signal(entry_id: str, cp_id: str) -> str:
    return f"{DOMAIN}_session_{entry_id}_{cp_id}"


# Entity keys that existed once and were dropped: the three loose session sensors became one current-session sensor.
# Their registry entries are removed at setup.
RETIRED_ENTITY_KEYS = ("session_energy", "session_started", "session_duration")
