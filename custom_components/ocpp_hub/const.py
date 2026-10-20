"""Constants shared by the Home Assistant layer of OCPP Hub."""

from __future__ import annotations

DOMAIN = "ocpp_hub"

CONF_PORT = "port"
# 9000 is the port the OCPP integration of the HACS community uses, so a charger configured for it needs no change.
DEFAULT_PORT = 9000
