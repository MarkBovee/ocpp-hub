# OCPP Hub

A Home Assistant integration that acts as the local OCPP 1.6 backend of your EV charger, and can pass a read-only copy of everything the charger says on to the manufacturer's own backend.

> **Status: early.** The OCPP server, the entities and the vendor mirror run against a real Raedian NEO, and the Raedian app shows the charger online through the mirror. Charging with a car has not been observed yet, so what the charger sends during a charge is untested. Nothing writes to a charger apart from two metering settings: there are no limit or start/stop controls. The changelog says what each release really does.

## Why

A charger talks to one OCPP backend at a time. Point it at Home Assistant and the manufacturer's app goes dark; leave it on the manufacturer's cloud and Home Assistant can only watch. OCPP Hub is the backend that Home Assistant controls, and it keeps the manufacturer's backend informed on the side, so charging sessions still show up in the vendor app while load balancing and price-based charging run locally.

## What it does

- Accepts the charger on a port you choose and shows its status, power, per-phase current, energy and connection as entities.
- Optionally passes a read-only copy of everything the charger says to the vendor's backend, where the charger shows up as a virtual charge point. The vendor app keeps its sessions and meter values; by default the vendor's commands are refused by the hub and never reach the charger; you can choose to pass them on (see below).
- Keeps the history of every charge (energy, peak power, duration, tag, stop reason) without limit, and shows the running charge live. See [docs/dashboard.md](docs/dashboard.md) for a ready dashboard.
- Stays local: no cloud service of ours is involved.

## Control

Two entities write to the charger, both only when you use them:

- `number.ocpp_hub_<charger id>_charge_limit` sets the current per phase, from 0 to the ceiling in the options (default 16 A, never above what you set there). A value under 6 A is sent as 0 A, because a car does not charge under 6 A: it pauses the charger. The hub tries a station-wide `ChargePointMaxProfile` first, then `TxProfile` while a charge runs, then `TxDefaultProfile`, and remembers which one the charger accepted. The number shows what the charger last accepted on this connection; the charger is not asked what it holds, so after a reconnect it shows nothing until you set the limit again.
- `switch.ocpp_hub_<charger id>_charging` starts a charge (`RemoteStartTransaction`, tag `OCPPHUB`) or stops the running one (`RemoteStopTransaction`). A start works only with a car connected; the charger refuses otherwise and Home Assistant says so.

Whether a charger really obeys a charging profile is proven only by watching a charge; the NEO has not been seen doing it yet. Every write is logged with the prefix `[write]`.

## What happens when a charger connects

The hub answers the charger and creates a device and its entities once the charger has sent its `BootNotification`. A charger that reconnects after Home Assistant restarted often sends neither a boot nor a status, so after fifteen seconds the hub asks for them with `TriggerMessage`. It then reads a few configuration keys, one key per request because some chargers ignore a request that names several, and makes sure the charger reports the meter values the entities need (`MeterValuesSampledData` and `MeterValueSampleInterval`; those two are the only things the hub writes to a charger). Home Assistant shows a notification with the charger's manufacturer, type, firmware, serial number and OCPP version.

When the integration stops, the hub drops the charger's connections instead of closing them. The Raedian NEO stays away for minutes after a proper close, whatever the close code, and is back within about half a minute after a connection that simply vanishes.

## Entities

A charger gets a device once it has sent its `BootNotification`. The entity ids are fixed by a key, not by the language of your instance: `sensor.ocpp_hub_<charger id>_status`, `_power`, `_energy` (kWh, total increasing), `_current_l1` to `_current_l3`, `_current_offered`, `_model`, `_firmware`, the diagnostic `_error_code`, and `binary_sensor.ocpp_hub_<charger id>_connected`, `_plugged_in` (a car is connected), `_car_charging` (the socket is delivering) and `_problem` (a faulted status or an error code other than no error). The last three come from the status and error code the charger already reports; they are unknown until the first status. The voltage sensors (`_voltage_l1` to `_voltage_l3`) exist but are switched off until you enable them. While the socket is known not to deliver (any status except charging), power and the phase currents read 0 instead of unknown, because nothing flows; a phase that the meter leaves out while it reports others also reads 0. While the charger is not connected its sensors show as unavailable and the connected sensor turns off.

The NEO sends a reading with only the energy register on the clock (at 12:00, 12:30 and so on). The hub keeps what such a partial reading does not carry, so power and currents do not vanish for a moment.

To see exactly what a charger sends, set the logger `custom_components.ocpp_hub.frames` to `debug` (Developer tools, Actions, `logger.set_level`). Every OCPP frame, in both directions, is then written to the log; it can hold id tags and serial numbers, so leave it off otherwise.

## Charge sessions

A charge starts at the charger's `StartTransaction` and ends at its `StopTransaction`. All information about a charge belongs to the charge, not to loose sensors. While it runs, `_current_session` shows the energy since the start (kWh), with the start, duration, tag, transaction, peak and average power, the highest current per phase, the offered current and the limit the hub had set as attributes; it is empty when no charge runs. When the charge ends, the same numbers are stored with it. `_last_session` holds the end time of the latest finished charge with the same attributes plus the stop reason, and the 20 newest charges in `recent_sessions`.

Every finished charge is kept in Home Assistant's storage, with no limit (about 300 bytes each), and survives restarts. The service `ocpp_hub.get_sessions` returns the whole history, newest first, optionally for one `charger_id` and limited by `limit`. A charge that was running while Home Assistant restarted is not recorded: the hub does not know its start. The energy of a charge is the meter at the stop minus the meter at the start; if the charger sends no stop reading, the last meter value is used.

## Vendor mirror

Open the integration's options and fill in:

- **Backend address:** the vendor's OCPP address, starting with `ws://` or `wss://`, with host, port and path but without the charger's serial number (the hub adds that, as the charger itself does) and without credentials in it. Leave it empty to switch the mirror off.
- **Raedian (NEO and other Raedian chargers):** use `wss://cloud-protocol.raedian.com:9043/ocpp/` and leave user name and password empty. The port is 9043; the installer app hides the address of its default profile, and port 443 does not answer. Verified on a NEO 22 kW: the vendor accepts the connection and its app shows the charger as online.
- **User name and password:** only if the backend asks for them. They are sent as HTTP Basic authentication; the password field never shows what is stored, and an empty field keeps it.

Per charger there are two diagnostic sensors, `_mirror_status` (idle, connecting or connected, with the last error, the number of frames waiting and the counters as attributes) and `_mirror_queue` (frames waiting).

What the mirror does and does not do:

- It forwards every request of the charger except its heartbeats, in order, one at a time, and sends heartbeats of its own at the interval the vendor asks for. When the vendor is unreachable it keeps the frames in a queue of up to 5000 (only meter values and status changes are dropped when it is full) and delivers them in order when the link is back.
- Transaction ids differ between the hub and the vendor; the mirror translates them. A stop for a transaction the vendor never confirmed is not sent.
- By default every command from the vendor is refused with an answer the protocol allows, and nothing reaches the charger.
- With *Commands from the backend* set to *Pass them on to the charger*, the vendor's `RemoteStartTransaction`, `RemoteStopTransaction`, `SetChargingProfile`, `ClearChargingProfile`, `ChangeConfiguration`, `ChangeAvailability` and `UnlockConnector` go to the charger, and its answer goes back to the vendor. Limits stay: a charging profile (also one inside a remote start) must name its unit (A or W) and may not ask for more than your current ceiling, a profile in watts is converted with its number of phases, and a profile that reuses the hub's profile id is refused; so is a stop of a transaction the hub does not know. Only a list of standard OCPP settings that touch neither the connection, the authentication, the metering nor the reading of profiles can be changed (for example `LightIntensity` and `ConnectionTimeOut`; keys that set what or how often the charger reports, such as `HeartbeatInterval`, are not on the list); any other key is refused and logged, so you can see what the vendor tried. `Reset`, `UpdateFirmware`, `GetDiagnostics` and `DataTransfer` are refused in every setting. Note that the vendor can still pause the charger with a profile of 0 A or set the connector inoperative, and can unlock the cable: you chose to allow that by turning this on. At most four vendor commands wait for the charger at once. Every passed-on or refused command is logged with the prefix `[forward]`. A profile from the vendor sits next to the one the hub sets, and the charger applies the lower limit, so the charge limit number does not show what the vendor set.
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
