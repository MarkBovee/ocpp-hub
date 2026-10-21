# Developer guide

## Layout

| Path | What it is |
| --- | --- |
| `custom_components/ocpp_hub/core/` | The OCPP engine. Pure Python, no Home Assistant imports, `mypy --strict` clean. |
| `custom_components/ocpp_hub/` | The Home Assistant layer: config flow, setup, entities, services. Thin; it holds no protocol logic. |
| `tests/` | Engine tests (plain pytest, no Home Assistant). `conftest.py` loads the core by path as `ocpp_hub_core`; `fake_charger.py` and `fake_vendor.py` are real websocket peers. |
| `tests_ha/` | Tests of the Home Assistant layer: config flow, setup, entities, diagnostics, the mirror options. They need the Home Assistant test plugin, so they run in `.venv-ha`. |
| `tools/` | `validate.py` (CI parity in one command), `version.py`, `check_translations.py`, `deploy_ha.py`, `release_gate.py`, `mutation_check.py`. |
| `.agents/skills/` | Skills for coding agents: `ocpp-expert` and `home-assistant`. |

## Setup and checks

```bash
python3 -m venv .venv
.venv/bin/pip install pytest pytest-asyncio pyyaml voluptuous websockets ruff==0.16.4 mypy==1.15.0
.venv/bin/python tools/validate.py          # everything CI runs, then pytest
.venv/bin/python tools/validate.py --quick  # skip pytest
```

`validate.py` runs ruff, the format check, `mypy --strict` on `core/`, the version and translation checks, a YAML check, `compileall`, `git diff --check` and pytest. The line limit is 120 characters, comments included.

The Home Assistant layer tests need their own virtualenv. Pin the plugin to the Home Assistant version of the server you deploy to (the plugin release pins it):

```bash
python3 -m venv .venv-ha
.venv-ha/bin/pip install pytest-homeassistant-custom-component==0.13.373 "websockets>=15.0.1" ruff==0.16.4
.venv-ha/bin/python -m pytest tests_ha
```

`validate.py` runs those tests too when `.venv-ha` exists and says "skip" in its summary when it does not; CI always runs them.

## Rules for code

- Every function in code has a short plain comment directly above it saying what it is for (add why when it is not obvious).
- Every test has an `# Intent:` and a `# Why:` comment directly above it.
- Put protocol logic in `core/`, and keep `core/` free of Home Assistant imports. If a function needs `hass`, it belongs in the layer above.
- Prefer small pure modules: they are cheap to test and to mutation-check (`tools/mutation_check.py <module> <test file>`; a surviving mutant is a missing test).
- Name data shapes (dataclasses) instead of passing loose dictionaries around.
- Never write to a charger from tests against a real device. The engine is tested with a fake charger over a real websocket.

## Reference implementation

When you build or change how the hub handles a charger, read how [lbbrhzn/ocpp](https://github.com/lbbrhzn/ocpp) (MIT) does it first and reuse what it proved on real chargers; `.agents/skills/ocpp-expert/SKILL.md` lists the files and functions to read. Compare the result with what the Raedian NEO really does, and credit adapted code in `NOTICE`.

## Fixtures

Recorded charger sessions go in `tests/fixtures/` byte for byte, with a note of where and when they were captured. Raw local captures (`capture_*.jsonl`) can hold card ids and serial numbers, so they stay out of git. Do not trim a capture to the frames that look relevant; the frames you leave out are where bugs hide.

## Deploying to a Home Assistant server

```bash
python tools/deploy_ha.py --dry-run     # shows the target and the files, changes nothing
python tools/deploy_ha.py --batch       # checks, back up, deploy, verify by sha256
```

It reads `HA_HOST`, `HA_SSH_USER` and `HA_SSH_KEY` (a path) from the git-ignored `.env`, never a password. Restarting Home Assistant is a separate step.
