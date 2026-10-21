# OCPP Hub

A Home Assistant integration that acts as the local OCPP 1.6 backend of your EV charger, and can pass a read-only copy of everything the charger says on to the manufacturer's own backend.

> **Status: early.** The OCPP server and the entities run against a real Raedian NEO. The vendor mirror is tested against a fake vendor backend only; it has not yet connected to the real one, because that backend's address is not public. Nothing writes to a charger apart from two metering settings: there are no limit or start/stop controls. The changelog says what each release really does.

## Why

A charger talks to one OCPP backend at a time. Point it at Home Assistant and the manufacturer's app goes dark; leave it on the manufacturer's cloud and Home Assistant can only watch. OCPP Hub is the backend that Home Assistant controls, and it keeps the manufacturer's backend informed on the side, so charging sessions still show up in the vendor app while load balancing and price-based charging run locally.

## What it does

- Accepts the charger on a port you choose and shows its status, power, per-phase current, energy and connection as entities.
- Optionally passes a read-only copy of everything the charger says to the vendor's backend, where the charger shows up as a virtual charge point. The vendor app keeps its sessions and meter values; the vendor's commands are answered by the hub and never reach the charger.
- Stays local: no cloud service of ours is involved.

Still to come: a charge limit and start/stop through `number` and `switch` entities. Writing to a charger needs the owner's explicit approval for each kind of write, so it is not built until that is given.

## What happens when a charger connects

The hub answers the charger and creates a device and its entities once the charger has sent its `BootNotification`. A charger that reconnects after Home Assistant restarted often sends neither a boot nor a status, so after fifteen seconds the hub asks for them with `TriggerMessage`. It then reads a few configuration keys, one key per request because some chargers ignore a request that names several, and makes sure the charger reports the meter values the entities need (`MeterValuesSampledData` and `MeterValueSampleInterval`; those two are the only things the hub writes to a charger). Home Assistant shows a notification with the charger's manufacturer, type, firmware, serial number and OCPP version.

When the integration stops, the hub drops the charger's connections instead of closing them. The Raedian NEO stays away for minutes after a proper close, whatever the close code, and is back within about half a minute after a connection that simply vanishes.

## Entities

A charger gets a device once it has sent its `BootNotification`. The entity ids are fixed by a key, not by the language of your instance: `sensor.ocpp_hub_<charger id>_status`, `_power`, `_energy` (kWh, total increasing), `_current_l1` to `_current_l3`, `_current_offered`, `_model`, `_firmware`, and `binary_sensor.ocpp_hub_<charger id>_connected`. The voltage sensors (`_voltage_l1` to `_voltage_l3`) exist but are switched off until you enable them. While the charger is not connected its sensors show as unavailable and the connected sensor turns off.

## Vendor mirror

Open the integration's options and fill in:

- **Backend address:** the vendor's OCPP address, starting with `ws://` or `wss://`, with host, port and path but without the charger's serial number (the hub adds that, as the charger itself does) and without credentials in it. Leave it empty to switch the mirror off.
- **Raedian (NEO and other Raedian chargers):** use `wss://cloud-protocol.raedian.com:9043/ocpp/` and leave user name and password empty. The port is 9043; the installer app hides the address of its default profile, and port 443 does not answer. Verified on a NEO 22 kW: the vendor accepts the connection and its app shows the charger as online.
- **User name and password:** only if the backend asks for them. They are sent as HTTP Basic authentication; the password field never shows what is stored, and an empty field keeps it.

Per charger there are two diagnostic sensors, `_mirror_status` (idle, connecting or connected, with the last error and the counters as attributes) and `_mirror_queue` (frames waiting).

What the mirror does and does not do:

- It forwards every request of the charger except its heartbeats, in order, one at a time, and sends heartbeats of its own at the interval the vendor asks for. When the vendor is unreachable it keeps the frames in a queue of up to 5000 (only meter values and status changes are dropped when it is full) and delivers them in order when the link is back.
- Transaction ids differ between the hub and the vendor; the mirror translates them. A stop for a transaction the vendor never confirmed is not sent.
- Every command from the vendor is refused with an answer the protocol allows, and nothing reaches the charger.
- The queue and the transaction ids live in memory. If Home Assistant restarts while a charge is running, that session stays open at the vendor, and its meter values go without a transaction id.

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
