<p align="center">
  <img src="https://raw.githubusercontent.com/MarkBovee/ocpp-hub/main/docs/images/logo.svg" alt="OCPP Hub" width="420">
</p>

<p align="center">
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg" alt="HACS custom repository"></a>
  <a href="https://github.com/MarkBovee/ocpp-hub/releases"><img src="https://img.shields.io/github/v/release/MarkBovee/ocpp-hub?include_prereleases" alt="Latest release"></a>
  <a href="https://github.com/MarkBovee/ocpp-hub/actions/workflows/ci.yml"><img src="https://github.com/MarkBovee/ocpp-hub/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/MarkBovee/ocpp-hub" alt="License"></a>
</p>

# OCPP Hub

OCPP Hub turns Home Assistant into the local OCPP 1.6 backend of your EV charger. Home Assistant sees and controls the
charger directly, and the manufacturer's cloud can still get a copy of everything the charger says, so the vendor app
keeps working next to Home Assistant.

A charger talks to one OCPP backend at a time. Point it at Home Assistant and the manufacturer's app goes dark; leave
it on the manufacturer's cloud and Home Assistant can only watch. OCPP Hub sits in between: the charger connects to
Home Assistant, and the hub keeps the vendor's backend informed on the side.

> **Status: early (0.x).** Built and tested against a Raedian NEO 22 kW. Other OCPP 1.6J chargers should work for the
> standard parts (status, metering, limits, start and stop), but have not been tried. The [changelog](CHANGELOG.md)
> says what each release really does, and [docs/roadmap.md](docs/roadmap.md) what is proven and what is not yet.

## Features

- **Local OCPP 1.6J server** on a port you choose. No cloud service of ours is involved.
- **Live entities** for status, power, energy, current and voltage per phase, offered current, plugged in, charging,
  problem and connection.
- **Control** with a charge limit (A per phase) under a ceiling you set, and a switch to start or stop a charge.
- **Charge history** kept without limit in Home Assistant, with the running charge shown live.
- **Schedules and a charging plan**: windows per weekday in which the charger may charge, set from Home Assistant, from
  a schedule helper, or from the Raedian app, and shown as a Home Assistant **calendar**.
- **Vendor mirror**: a copy of the charger's traffic goes to the manufacturer's backend, so the vendor app shows the
  charger online with its sessions. Commands from the vendor are refused unless you allow them, and then always under
  your ceiling.

## Supported chargers

| Charger | Status |
|---------|--------|
| Raedian NEO (22 kW) | Tested: entities, limits, start and stop, vendor mirror, the Raedian app's planning |
| Other OCPP 1.6J chargers | Expected to work for the standard OCPP parts; not tested. Reports are welcome. |

OCPP 2.0.1 is not supported.

## Install

### HACS (recommended)

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=MarkBovee&repository=ocpp-hub&category=integration)

1. In HACS, add `https://github.com/MarkBovee/ocpp-hub` as a custom repository of type *Integration* (or use the
   button above).
2. Download **OCPP Hub** and restart Home Assistant.
3. Add the integration under *Settings, Devices & services* and choose the port (default 9000).

[![Open your Home Assistant instance and start setting up OCPP Hub.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=ocpp_hub)

### Manual

Copy `custom_components/ocpp_hub` into the `custom_components` folder of your Home Assistant configuration and restart.

Home Assistant 2026.10 or newer is required.

## Connect a charger

In the charger's OCPP settings, set the backend address to `ws://<Home Assistant IP>:<port>`. Most chargers add their
own id to that address. The port has no authentication, so keep it on a trusted network and never forward it to the
internet.

Raedian NEO: the OCPP profile (address and `ChargePointSerialNumber`) is set in the RAEDIAN Install app, which is meant
for certified installers.

When the charger connects, Home Assistant creates a device with its entities and shows a notification with the
manufacturer, model, firmware and serial number.

## Entities

Entity ids are fixed by a key, not by the language of your instance. `<id>` is the charger's id in lower case.

| Entity | What it shows or does |
|--------|-----------------------|
| `sensor.ocpp_hub_<id>_status` | The connector status (available, preparing, charging, ...) |
| `sensor.ocpp_hub_<id>_power`, `_energy` | Power (W) and the energy meter (kWh, total increasing) |
| `sensor.ocpp_hub_<id>_current_l1` ... `_l3`, `_current_offered` | Current per phase and the current the charger offers |
| `sensor.ocpp_hub_<id>_voltage_l1` ... `_l3` | Voltage per phase (disabled by default) |
| `sensor.ocpp_hub_<id>_current_session`, `_last_session` | The running charge and the latest finished one, with details as attributes |
| `sensor.ocpp_hub_<id>_limit_in_effect` | The limit that applies now and the charger's schedule for the next day |
| `sensor.ocpp_hub_<id>_app_plan` | The charging plan: number of windows, the windows, and whether the planning is on |
| `calendar.ocpp_hub_<id>_charging_plan` | The charging plan as repeating calendar events |
| `binary_sensor.ocpp_hub_<id>_connected`, `_plugged_in`, `_car_charging`, `_problem` | Connection and socket state |
| `number.ocpp_hub_<id>_charge_limit` | Sets the current per phase (0 to your ceiling; under 6 A pauses) |
| `switch.ocpp_hub_<id>_charging` | Starts or stops a charge |
| `button.ocpp_hub_<id>_clear_charge_limit`, `_apply_schedule` | Removes the hub's limit; writes the schedule helper to the charger |
| `sensor.ocpp_hub_<id>_model`, `_firmware`, `_error_code`, `_mirror_status`, `_mirror_queue` | Diagnostics |

While the socket is not delivering, power and currents read 0 instead of unknown. While the charger is not connected,
its sensors are unavailable and `_connected` is off.

A ready dashboard is in [docs/dashboard.md](docs/dashboard.md).

## Control and safety

- **Ceiling.** The options hold a current ceiling (default 16 A). Nothing the hub or the vendor sends goes above it.
  Set it at or below what your installer configured; the hub never raises the charger's own hardware limit.
- **Charge limit.** The number sends a charging profile. The hub tries a station-wide `ChargePointMaxProfile` first,
  then `TxProfile` while a charge runs, then `TxDefaultProfile`, and remembers which one the charger accepted. A value
  under 6 A is sent as 0 A: a car does not charge below 6 A, so it pauses.
- **Start and stop.** The switch sends `RemoteStartTransaction` (tag `OCPPHUB`) or `RemoteStopTransaction`. A start
  needs a car connected.
- **Limit in effect.** After every change the hub asks the charger what it really applies (`GetCompositeSchedule`) and
  shows that. Limits survive a restart of Home Assistant and are marked `restored_unconfirmed` until the charger
  confirms them.

Every write to the charger is logged with the prefix `[write]`.

## Charge sessions

A charge runs from the charger's `StartTransaction` to its `StopTransaction`. `_current_session` shows the energy of
the running charge with its start, duration, tag, peak and average power and limits as attributes. `_last_session`
shows the latest finished charge and the 20 newest in `recent_sessions`.

Every finished charge is kept in Home Assistant's storage without limit (about 300 bytes each). The action
`ocpp_hub.get_sessions` returns the history, newest first, optionally for one `charger_id` and up to `limit`. A charge
that was running while Home Assistant restarted is not recorded.

## Schedules and the charging plan

There are three ways to tell the charger when it may charge. All of them write one recurring OCPP profile that the
charger keeps on its own, so the plan works even while Home Assistant is down.

### The charging plan

The plan is a list of windows per weekday. Inside a window the charger gets *Limit inside the blocks* (your ceiling
unless set), outside them *Limit outside the blocks* (a pause unless set). Both are in the options.

```yaml
action: ocpp_hub.set_plan
data:
  windows:
    - {day: monday, from: "12:00", to: "14:00"}
    - {day: friday, from: "22:00", to: "06:00"}   # runs overnight
```

An empty list removes the hub's profile. The plan shows in `_app_plan` and in the calendar
`calendar.ocpp_hub_<id>_charging_plan`, which you can open from the Calendar panel or use in automations. The calendar
is read-only.

### With the Raedian app

With *Commands from the backend* set to *Pass them on to the charger*, the Raedian app and Home Assistant share the same
plan:

- When the app shows its planning, the hub answers with the plan.
- When the app changes the planning, the hub takes it over, writes it to the charger and tells the app it worked.
- When you switch the planning off in the app, the hub removes its profile so the charger charges freely, and keeps the
  windows for later. Switching it on writes them again.

The app talks to the charger through its own binary protocol inside OCPP `DataTransfer`. The hub decodes the planning
part of it; the layout was worked out from captured traffic and is documented in
[docs/raedian-protocol.md](docs/raedian-protocol.md). A planning message that does not parse completely is refused.
This part is **experimental**: the charger also has a planning of its own that the hub does not write to, and whether
the firmware enforces it next to the hub's profile is not known yet.

### A daily schedule, or a schedule helper

- `ocpp_hub.set_schedule` sets a daily schedule as a list of a time and the limit from then on, for example
  `[{time: "00:00", limit: 6}, {time: "07:00", limit: 16}]`.
- A **schedule helper** (Settings, Devices & services, Helpers, Schedule) can be chosen in the options. Its blocks are
  the windows in which the charger may charge. Nothing is written when the helper changes: press
  `button.ocpp_hub_<id>_apply_schedule` to send it.

The plan, the schedule, the schedule helper and the charge limit share one profile on the charger, so the last one
written wins. When a limit or schedule replaces the plan, the plan is emptied instead of showing windows the charger no
longer follows. The charger counts recurring profiles from a fixed point in UTC, so the change between summer and
winter time moves a schedule by an hour until it is written again.

## Vendor mirror

Open the integration's options and fill in:

- **Backend address**: the vendor's OCPP address (`ws://` or `wss://`, host, port and path) without the charger id; the
  hub adds it, as the charger does. Leave it empty to switch the mirror off.
- **User name and password**: only if the backend asks for them (HTTP Basic authentication).

For Raedian chargers use `wss://cloud-protocol.raedian.com:9043/ocpp/` with no user name or password.

What the mirror does:

- It forwards every message of the charger except heartbeats, in order, and keeps up to 5000 frames while the vendor is
  unreachable. Transaction ids are translated between the hub and the vendor.
- By default every command from the vendor is refused with an answer the protocol allows, and nothing reaches the
  charger.
- With *Commands from the backend* set to *Pass them on to the charger*, the vendor's remote start and stop, charging
  profiles, a short list of harmless configuration keys, availability, unlock and `DataTransfer` reach the charger.
  Charging profiles stay under your ceiling. `Reset`, `UpdateFirmware` and `GetDiagnostics` are always refused. Every
  passed-on or refused command is logged with the prefix `[forward]`; `DataTransfer` is logged in full both ways
  because its content cannot be checked.

Two diagnostic sensors per charger, `_mirror_status` and `_mirror_queue`, show the state of the link.

## Troubleshooting

- **The charger does not connect.** Check the address in the charger (`ws://`, not `wss://`, and the port you chose)
  and that nothing else listens on that port.
- **Entities stay unknown after a restart.** A charger that reconnects often sends no boot or status; after fifteen
  seconds the hub asks for them with `TriggerMessage`.
- **See every OCPP message.** Set the logger `custom_components.ocpp_hub.frames` to `debug` (Developer tools, Actions,
  `logger.set_level`). The log then holds every frame both ways, including id tags and serial numbers, so turn it off
  again afterwards.
- **Read the Raedian app's messages.** `python tools/raedian_frames.py home-assistant.log` lists the protocol frames in
  a log and marks the commands the hub does not decode yet.

## Contributing

Issues and pull requests are welcome. See [docs/developer.md](docs/developer.md) for the layout of the code and how to
run the checks:

```bash
python3 -m venv .venv
.venv/bin/pip install pytest pytest-asyncio pyyaml voluptuous websockets ruff==0.16.4 mypy==1.15.0
.venv/bin/python tools/validate.py
```

The engine in `custom_components/ocpp_hub/core/` imports nothing from Home Assistant and is tested on its own against
a fake charger over a real websocket.

Do not post logs with id tags, serial numbers, addresses or credentials in issues. Security problems go through
[SECURITY.md](SECURITY.md).

## Disclaimer

OCPP Hub is an independent project. It is not made, endorsed or supported by Raedian or by any charger manufacturer,
and not by Home Assistant. Product names are used only to say what the integration works with. Writing to a charger
changes how it charges; use it within the limits of your installation, at your own risk.

## License

Apache-2.0. Parts of the design follow [ha-ocpp-relay](https://github.com/michael-adler/ha-ocpp-relay) (MIT) and the
behaviour of [lbbrhzn/ocpp](https://github.com/lbbrhzn/ocpp) (MIT); see [NOTICE](NOTICE).
