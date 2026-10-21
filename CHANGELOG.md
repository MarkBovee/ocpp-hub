# Changelog

## Unreleased

- Charge sessions: the running charge is shown live (`_session_energy`, `_session_started`, `_session_duration`), every finished charge is kept without limit in Home Assistant's storage, `_last_session` lists the newest ones, and the service `ocpp_hub.get_sessions` returns the whole history. A ready dashboard is in `docs/dashboard.md`.

## 0.1.0

- Scaffold release: repository tooling, CI (checks, hassfest, HACS validation), a release gate that waits for green checks on the tagged commit, and an integration shell with a config flow for the port.
- The engine in `core/` starts with the OCPP-J frame helpers; it imports nothing from Home Assistant.
- There is no OCPP server yet. Setting the integration up only stores the port.
