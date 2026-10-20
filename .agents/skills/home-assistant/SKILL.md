---
name: home-assistant
description: "Use when testing, deploying or debugging the integration on a Home Assistant instance: HA-MCP inspection, deploy over SSH with a key, restart, logs, config entries and the HA test environment."
---

# Home Assistant workflow for this repository

## Inspect first, with HA-MCP

- Start with `ha_get_overview`, `ha_get_integration` (domain `ocpp_hub`), `ha_get_logs(source="error_log", search="ocpp_hub")` and `ha_get_system_health(include="config_check")`.
- Prefer read-only tools. Use a write or delete tool only when the owner asked for that specific change.
- Do not read the Supervisor token or `.storage` directly when HA-MCP can answer.

## Deploy

- Always deploy with `python tools/deploy_ha.py`. Never write ad-hoc SSH or copy commands.
- It reads `HA_HOST`, `HA_SSH_USER` and the path of an SSH key (`HA_SSH_KEY`) from the git-ignored `.env`, never a password. It runs the checks, backs up the current copy outside `custom_components`, unpacks the new copy next to it, swaps, and verifies every file by sha256.
- `--dry-run` first; it changes nothing. A restart is a separate step: `ha_restart` through HA-MCP, or `--restart --yes` when the owner approved it.
- Deploy, restart, push, tag and GitHub posts are separate outward actions; one approval does not cover the next.

## Config entries and reloads

- A code change in `custom_components/ocpp_hub/` is loaded after a restart. Reloading the config entry reloads the integration code only when the Python modules were not imported before; when in doubt, restart.
- Reloading or restarting drops the charger connection. The charger redials on its own schedule; measure how long, do not guess.
- Set `logger.logs.custom_components.ocpp_hub: debug` (or `ha_set_integration(domain="ocpp_hub", log_level="DEBUG")`) while testing, and set it back afterwards.

## Test environment

- Engine tests need only the repository `.venv` (`python tools/validate.py`).
- Tests of the Home Assistant layer need a virtualenv with `pytest-homeassistant-custom-component` pinned to the plugin release that matches the server's Home Assistant version; find it from the PyPI JSON (`requires_dist` shows the pinned `homeassistant`). Request the `recorder_mock` fixture before `hass` when the recorder is involved.
- Entity ids are set explicitly from the entity key (`sensor.ocpp_hub_<key>`), because Home Assistant otherwise derives them from the instance language.

## Avoid

- Hard-coded hosts, ports of a private network or credentials in code, docs, tests or skills (`tests/test_skills_hygiene.py` enforces it for skills).
- Calling a device service from the integration without the owner's approval for that specific write.
