# Changelog

## Unreleased

- Schedule: the charger's schedule for the next 24 hours is shown as an attribute of `_limit_in_effect`, and the service `ocpp_hub.set_schedule` installs a daily recurring schedule of limits (under your ceiling; under 6 A pauses).

- Limits: the limit Home Assistant set and the one the vendor's app set (when commands are passed on) are saved and shown again after a restart, `_limit_in_effect` shows the limit that applies (asked from the charger with `GetCompositeSchedule`), and the vendor is answered with it when it asks for the composite schedule.

- New entities from the status the charger already reports: `plugged_in`, `car_charging`, `problem` and the diagnostic `error_code`.
- Raw OCPP frames are written to the log when the logger `custom_components.ocpp_hub.frames` is set to debug (off by default).

- Session information lives with the session: one `_current_session` sensor (energy so far, with start, duration, tag, peak and average power, highest current per phase, offered current and the hub's limit as attributes) replaces the three loose session sensors, and every stored charge now carries the same numbers.
- Power and phase currents read 0 instead of unknown while the socket delivers nothing, and a reading that carries only the energy register no longer erases the rest.

- Option *Commands from the backend*: pass the vendor's commands on to the charger (off by default). Reset, firmware and diagnostics stay refused; a profile above your current ceiling and configuration keys that could cut the hub off are refused too. Logged with `[forward]`.
- A button *Clear charge limit* removes the hub's profile from the charger.

- Control: `number` charge limit (0 to a ceiling you set, default 16 A; under 6 A pauses) and `switch` to start and stop a charge. The ceiling is an option; every write is logged with `[write]`.

- Charge sessions: the running charge is shown live (`_session_energy`, `_session_started`, `_session_duration`), every finished charge is kept without limit in Home Assistant's storage, `_last_session` lists the newest ones, and the service `ocpp_hub.get_sessions` returns the whole history. A ready dashboard is in `docs/dashboard.md`.

## 0.1.0

- Scaffold release: repository tooling, CI (checks, hassfest, HACS validation), a release gate that waits for green checks on the tagged commit, and an integration shell with a config flow for the port.
- The engine in `core/` starts with the OCPP-J frame helpers; it imports nothing from Home Assistant.
- There is no OCPP server yet. Setting the integration up only stores the port.
