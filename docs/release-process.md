# Release process

## Versioning

One version, in three places: `pyproject.toml`, `custom_components/ocpp_hub/manifest.json` and the top `## <version>` heading of `CHANGELOG.md`. `python tools/version.py bump X.Y.Z` updates the first two; write the changelog heading yourself, because release notes are written by a person. `tools/version.py check` and `tests/test_version_consistency.py` fail on drift.

Pre-releases use `X.Y.Z-rcN` (HACS shows it as a pre-release).

## Steps

1. Work on a branch (`feature/<name>`, `fix/<name>`), commit with Conventional Commits, open a pull request. CI runs the checks, hassfest and the HACS validation.
2. Bump the version and write the changelog entry on the release branch.
3. Run `python tools/validate.py` locally and deploy the release candidate to the owner's Home Assistant with `tools/deploy_ha.py`.
4. Smoke test on the real charger: connect, read status and meter values, then (with the owner's approval and a stated current ceiling) set a limit and clear it. Record what was observed in the changelog.
5. Merge to `main` once all pull request checks are green.
6. Push the `vX.Y.Z` tag on the merged commit. The release job waits for hassfest and HACS on that exact commit (`tools/release_gate.py`) and then builds `ocpp_hub.zip` and the release notes from the changelog.

## Rules that always apply

- Push the tag only after every check on the commit is green; the release job enforces it.
- Deploying, restarting Home Assistant, pushing, tagging and posting on GitHub are separate outward actions. An approval for one does not cover the next.
- A release candidate that has not run against the real charger is not ready, however green CI is.
