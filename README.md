# OCPP Hub

A Home Assistant integration that acts as the local OCPP 1.6 backend of your EV charger, and can pass a read-only copy of everything the charger says on to the manufacturer's own backend.

> **Status: early.** Version 0.1.0 is a scaffold. Setting it up stores a port and nothing listens yet. The sections below describe where it is heading, and the changelog says what each release really does.

## Why

A charger talks to one OCPP backend at a time. Point it at Home Assistant and the manufacturer's app goes dark; leave it on the manufacturer's cloud and Home Assistant can only watch. OCPP Hub is the backend that Home Assistant controls, and it keeps the manufacturer's backend informed on the side, so charging sessions still show up in the vendor app while load balancing and price-based charging run locally.

## What it will do

- Accept the charger on a port you choose and expose its status, power, per-phase current and session energy as entities.
- Let Home Assistant set a charge limit and start or stop charging, through normal `number` and `switch` entities.
- Mirror the charger's sessions to the vendor backend as a virtual charge point, with that backend treated as read-only.
- Stay local: no cloud service is needed for any of this.

## Install

1. In HACS, add `https://github.com/MarkBovee/ocpp-hub` as a custom repository of type *Integration*.
2. Download **OCPP Hub** and restart Home Assistant.
3. Add the integration under *Settings, Devices & services* and pick the port (default 9000).

## Connect a charger

In the charger's OCPP settings, set the backend URL to `ws://<Home Assistant IP>:<port>`. Most chargers append their own identity to that URL. The port has no authentication, so keep it on a trusted network.

Raedian NEO: the OCPP profile (URL and `ChargePointSerialNumber`) is set in the RAEDIAN Install app, which is meant for certified installers.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install pytest pytest-asyncio pyyaml voluptuous websockets ruff==0.16.4 mypy==1.15.0
.venv/bin/python tools/validate.py
```

The engine in `custom_components/ocpp_hub/core/` imports nothing from Home Assistant, so it is tested and type-checked on its own. See [docs/developer.md](docs/developer.md) and [docs/release-process.md](docs/release-process.md).

## License

Apache-2.0. Parts of the design follow [ha-ocpp-relay](https://github.com/michael-adler/ha-ocpp-relay) (MIT); see `NOTICE`.
