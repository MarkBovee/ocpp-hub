# Roadmap and status

The one place that says how far the project is. Update it in the same change that moves an item. `[x]` is done and verified, `[~]` is built but not proven live, `[ ]` is open. Dates are when it was last confirmed.

## Goal

Home Assistant is the real local OCPP 1.6 backend of a Raedian NEO 22 kW, shows it as entities, and keeps the vendor backend informed through a read-only mirror, replacing the `lbbrhzn/ocpp` integration.

## Done

- [x] Engine in `core/`: frames, charger session, central system, commands, transaction-id map. No Home Assistant imports, `mypy --strict`, tested over a real websocket with a fake charger (PR 2).
- [x] Vendor mirror in `core/`: store-and-forward queue, replay of boot and status, transaction-id pairing, vendor commands refused, Basic auth only when configured (PR 2).
- [x] Home Assistant layer: setup and unload, config flow, options flow, sensors and binary sensor, diagnostics with redaction, translations (English, Dutch) (PR 3).
- [x] Notification when a charger connects, with vendor, model, firmware, serial, OCPP version and mirror state (2026-10-20).
- [x] Deployed with `tools/deploy_ha.py`; `lbbrhzn/ocpp` removed; OCPP Hub listens on port 9000 and the NEO connects (2026-10-20).
- [x] Reconnect after a reload of the integration: the NEO is back in 14 seconds, because the hub drops connections instead of closing them (2026-10-20).
- [x] Vendor mirror against the real Raedian backend: `wss://cloud-protocol.raedian.com:9043/ocpp/`, no credentials; the mirror shows connected and the Raedian app shows the charger online (2026-10-21).
- [x] Independent review and audit of the engine and of the Home Assistant layer, P0 and P1 findings fixed with a test each.

- [x] First real charge with a car (2026-10-21): the NEO obeyed a 6 A station profile (5.9 A per phase, about 4.05 kW), reported preparing, charging, suspended by the car, finishing and available, and the charge was stored with its numbers (5.0 kWh in 84 minutes, stopped by the car being unplugged).

## In progress

- [~] Charge sessions: live session sensors, an unbounded stored history, `_last_session` with the newest charges, the service `ocpp_hub.get_sessions`, and a ready dashboard (`docs/dashboard.md`). Tested with a fake charger; not yet seen with a real charge.
- [~] Control: a charge limit `number` (0 to the ceiling, default 16 A, under 6 A pauses) and a start/stop `switch`, through one guarded write path in `core/control.py`. Tested with a fake charger; whether the NEO obeys a charging profile is unproven.
- [~] Passing the vendor's commands on to the charger (option, off by default): everything except Reset, firmware and diagnostics, under the current ceiling, with transaction ids translated. Tested against a fake vendor and charger; not yet tried with the real vendor.

## Open

- [ ] Check the vendor side of a real charge: that the status changes and the charge show up in the Raedian app, and what the vendor sends back.
- [ ] Log raw frames at debug level, to see every measurand and field the NEO sends, and decide from that which further entities are worth making.
- [ ] Measure a full restart of Home Assistant (a reload is measured: 14 seconds).
- [ ] Remember the charge limit the hub set across a restart of Home Assistant (today it survives a reconnect only; the clear button removes the hub's profile).
- [ ] Persist the mirror queue and a charge that is running across a restart of Home Assistant (today both are lost; documented in the README).
- [ ] Optional username default for the vendor: use the charger's serial when only a password is given. Build only if the vendor answers HTTP 401.
- [ ] Make the repository public, switch on HACS validation, tag a release. Version and changelog are still `0.1.0` / unreleased.
- [ ] Remove the unused add-on copy on the Home Assistant server (needs the owner's yes).
- [ ] Option to switch off the connect notification.
- [ ] Further entities from data the hub already has: plugged in, car charging, problem and the connector error code.

## Known small follow-ups (P2)

- A vendor can send many `TriggerMessage` requests; the answers are not rate limited.
- A vendor transaction id the mirror cannot map is logged on every frame.
- Two charger ids that slugify the same would share entity ids.
- The lock around the hub's own requests has no acquisition time-out.
- Reading configuration key by key is slow with a charger that stays silent.

## Decisions that differ from the first plan

- One integration, no add-on and no MQTT; the hub listens on port 9000 directly, without the trial run on 9001.
- `ClearChargingProfile` from the vendor is answered `Unknown`, not `Rejected`.
- The mirror sensors are per charger, not one for the whole hub.
- Configuration is read one key per request, and the hub asks for a boot and a status with `TriggerMessage`, because the NEO answers only one key at a time and sends neither after a hub restart.
