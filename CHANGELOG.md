# Changelog

## 0.1.0

- Scaffold release: repository tooling, CI (checks, hassfest, HACS validation), a release gate that waits for green checks on the tagged commit, and an integration shell with a config flow for the port.
- The engine in `core/` starts with the OCPP-J frame helpers; it imports nothing from Home Assistant.
- There is no OCPP server yet. Setting the integration up only stores the port.
