#!/usr/bin/env python3
"""Deploy custom_components/ocpp_hub to the Home Assistant server over SSH, with a key and no password.

Steps: checks -> back up the current copy on the server -> stream the tracked files as a tar -> swap the directory ->
verify every file by sha256 -> optional restart of Home Assistant.

Authentication stays with a key: HA_HOST, HA_SSH_USER and the PATH of a key (HA_SSH_KEY) come from the git-ignored
``.env``; a password is never read. With ``--batch`` nothing is ever prompted. Standard library only.

    python tools/deploy_ha.py --dry-run     # show target and files, change nothing
    python tools/deploy_ha.py --batch       # checks, deploy, verify
    python tools/deploy_ha.py --batch --restart --yes
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = Path("custom_components") / "ocpp_hub"
BUILD_FILE = "BUILD"
REMOTE_CONFIG = "/config"
KEEP_BACKUPS = 3


@dataclass(frozen=True)
class Target:
    """Where and how to reach the Home Assistant server."""

    host: str
    user: str
    config_dir: str = REMOTE_CONFIG
    key: str | None = None  # path to a private key; ssh reads it, this script never does
    sudo: bool = True  # the SSH user cannot write in /config and needs passwordless sudo

    # Intent: the folder the integration lives in on the server.
    # Why: deploy, backup and verification must agree on one path.
    @property
    def remote_dir(self) -> str:
        """Where the integration lives on the server."""
        return f"{self.config_dir}/custom_components/ocpp_hub"

    # Intent: the folder holding timestamped backups of the previous copy.
    # Why: it lives outside custom_components so Home Assistant never loads a backup.
    @property
    def backup_dir(self) -> str:
        """Where timestamped backups of the previous copy are kept, outside custom_components so HA never loads them."""
        return f"{self.config_dir}/.ocpp_hub-backup"


# Intent: read KEY=value pairs from a .env text; comments and blank lines are ignored and quotes are stripped.
# Why: deployment settings live in a git-ignored file and must never be exported to child processes.
def parse_env(text: str) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip("\"'")
    return env


# Intent: return the bare host name of a value that may be a host or a URL such as http://homeassistant.local:8123.
# Why: .env often holds the API URL while SSH needs only the host.
def _bare_host(value: str) -> str:
    return (urlparse(value).hostname or value) if "//" in value else value


# Intent: resolve host, SSH user and key path from options or .env, and refuse to guess.
# Why: a missing user must stop the run with a readable message instead of connecting as the wrong account.
def target_from_env(
    env: dict[str, str], host: str | None = None, user: str | None = None, key: str | None = None
) -> Target:
    resolved_host = host or env.get("HA_HOST") or urlparse(env.get("HA_API_URL", "")).hostname
    resolved_user = user or env.get("HA_SSH_USER")
    if not resolved_host or not resolved_user:
        raise SystemExit("No SSH target: set HA_HOST and HA_SSH_USER in .env or pass --host and --user.")
    resolved_key = key or env.get("HA_SSH_KEY")
    key_path = str(Path(resolved_key).expanduser()) if resolved_key else None
    return Target(_bare_host(resolved_host), resolved_user, key=key_path)


# Intent: list the files to deploy relative to the integration folder: tracked and untracked-but-not-ignored, no caches.
# Why: what git knows is what ships; a stray local file or a __pycache__ must never reach the server.
def relative_files(root: Path = ROOT) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-c", "-o", "--exclude-standard", "-z", "--", str(INTEGRATION)],
        cwd=root,
        capture_output=True,
        check=True,
    ).stdout.decode()
    prefix = INTEGRATION.as_posix() + "/"
    return sorted(p[len(prefix) :] for p in out.split("\0") if p.startswith(prefix) and "__pycache__" not in p)


# Intent: write the commit the deployed code comes from into the integration's BUILD file and return it.
# Why: a log line must say which code wrote it; without a stamp an old build and the current one look identical.
def write_build_stamp(root: Path = ROOT) -> str:
    head = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no", "--", str(INTEGRATION)],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    stamp = head + ("+dirty" if dirty else "")
    (root / INTEGRATION / BUILD_FILE).write_text(stamp + "\n")
    return stamp


# Intent: compute the sha256 of every local file to deploy.
# Why: the server copy is verified against these digests after the swap.
def local_digests(files: list[str], root: Path = ROOT) -> dict[str, str]:
    return {name: hashlib.sha256((root / INTEGRATION / name).read_bytes()).hexdigest() for name in files}


# Intent: turn `sha256sum` output into a {relative path: digest} mapping; paths may start with './'.
# Why: the remote listing is plain text and must be compared with the local digests.
def parse_sha256sum(output: str) -> dict[str, str]:
    digests: dict[str, str] = {}
    for line in output.splitlines():
        if line.strip():
            digest, path = line.split(None, 1)
            digests[path.strip().removeprefix("./")] = digest
    return digests


# Intent: compare local and remote digests and return (missing on the server, unexpected on the server, different).
# Why: a deploy is only done when all three lists are empty.
def compare(local: dict[str, str], remote: dict[str, str]) -> tuple[list[str], list[str], list[str]]:
    missing = sorted(set(local) - set(remote))
    extra = sorted(set(remote) - set(local))
    different = sorted(name for name in set(local) & set(remote) if local[name] != remote[name])
    return missing, extra, different


# Intent: build the POSIX sh run on the server: back up the old copy, unpack stdin next to it, swap, prune old backups.
# Why: unpacking completely before replacing means a broken transfer leaves the running integration untouched;
# the script avoids GNU-only flags because the SSH add-on runs BusyBox.
def remote_install_script(target: Target, keep: int = KEEP_BACKUPS) -> str:
    d, b, parent = target.remote_dir, target.backup_dir, f"{target.config_dir}/custom_components"
    return " && ".join(
        [
            f"mkdir -p '{b}' '{parent}'",
            f"if [ -d '{d}' ]; then tar -C '{parent}' -czf '{b}/ocpp_hub-'$(date +%Y%m%d-%H%M%S)'.tar.gz' ocpp_hub; fi",
            f"rm -rf '{parent}/.ocpp_hub-new' && mkdir '{parent}/.ocpp_hub-new'",
            f"tar -C '{parent}/.ocpp_hub-new' -xf -",
            f"rm -rf '{d}' && mv '{parent}/.ocpp_hub-new' '{d}'",
            f"ls -1t '{b}' | tail -n +{keep + 1} | while read f; do rm -f '{b}'/\"$f\"; done",
        ]
    )


# Intent: wrap a script in `sudo -n sh -c` when needed; stdin (the tar stream) passes straight through.
# Why: `-n` makes sudo fail instead of asking for a password, so nothing here can prompt for one.
def privileged(script: str, sudo: bool) -> str:
    return f"sudo -n sh -c {shlex.quote(script)}" if sudo else script


# Intent: build an ssh invocation that shares one authenticated connection (ControlMaster).
# Why: a key is passed by path only and `batch` forbids any prompt, so an unauthorised key fails instead of asking.
def ssh_command(
    target: Target, control_path: str, *remote: str, master: bool = False, batch: bool = False
) -> list[str]:
    options = ["-o", f"ControlPath={control_path}", "-o", "ControlMaster=yes" if master else "ControlMaster=no"]
    if target.key:
        options += ["-i", target.key, "-o", "IdentitiesOnly=yes"]
    if batch:
        options += ["-o", "BatchMode=yes"]
    if master:
        options += ["-o", "ControlPersist=300"]
    return ["ssh", *options, f"{target.user}@{target.host}", *remote]


# Intent: run the CI-equivalent checks and abort the deploy when they fail.
# Why: a failing tree must never reach the server; one command gives the same answer as CI.
def run_checks(root: Path = ROOT) -> None:
    result = subprocess.run([sys.executable, str(root / "tools" / "validate.py")], cwd=root)
    if result.returncode:
        raise SystemExit("Checks failed; nothing was deployed. Fix them (or use --skip-checks if you know why).")


# Intent: start tar writing the files, relative to the integration folder, to its stdout.
# Why: entries are root-owned so the server copy matches the other components regardless of local user ids.
def stream_tar(files: list[str], root: Path = ROOT) -> subprocess.Popen[bytes]:
    command = [
        "tar",
        "-C",
        str(root / INTEGRATION),
        "--owner=0",
        "--group=0",
        "--numeric-owner",
        "--null",
        "-T",
        "-",
        "-cf",
        "-",
    ]
    tar = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert tar.stdin is not None
    tar.stdin.write(("\0".join(files) + "\0").encode())
    tar.stdin.close()
    return tar


# Intent: open the shared connection, install the files, and verify every file by checksum.
# Why: the swap is only reported as done when the server copy is byte-identical to the local files.
def deploy(target: Target, files: list[str], control_path: str, batch: bool = False) -> None:
    # Intent: build an ssh command line for this target on the shared connection.
    # Why: every remote step must use the same control path and batch setting.
    def ssh(*remote: str, master: bool = False) -> list[str]:
        return ssh_command(target, control_path, *remote, master=master, batch=batch)

    subprocess.run(ssh("true", master=True), check=True)
    try:
        tar = stream_tar(files)
        subprocess.run(ssh(privileged(remote_install_script(target), target.sudo)), stdin=tar.stdout, check=True)
        assert tar.stdout is not None
        tar.stdout.close()
        if tar.wait():
            raise SystemExit("tar failed locally; verify the server copy before restarting anything.")
        listing = subprocess.run(
            ssh(f"cd '{target.remote_dir}' && find . -type f -exec sha256sum {{}} +"),
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        missing, extra, different = compare(local_digests(files), parse_sha256sum(listing))
        if missing or extra or different:
            raise SystemExit(f"Verification failed. missing={missing} unexpected={extra} different={different}")
        print(f"Deployed and verified {len(files)} files in {target.remote_dir}")
    finally:
        subprocess.run(
            ["ssh", "-O", "exit", "-o", f"ControlPath={control_path}", f"{target.user}@{target.host}"],
            capture_output=True,
        )


# Intent: poll the REST API until it answers 200 again after a restart.
# Why: the API first goes away and then comes back; a short wait first avoids mistaking the old instance for the new.
def wait_for_home_assistant(base: str, token: str, timeout_s: int = 300, poll_s: int = 5) -> bool:
    request = urllib.request.Request(base + "/", headers={"Authorization": f"Bearer {token}"})
    time.sleep(poll_s)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                if response.status == 200:
                    return True
        except OSError, http.client.HTTPException:
            pass
        time.sleep(poll_s)
    return False


# Intent: validate the Home Assistant configuration and restart it over the REST API, after a confirmation.
# Why: a new or changed integration is only loaded after a restart; the token is read from .env and never printed.
def restart_home_assistant(env: dict[str, str], assume_yes: bool) -> None:
    base, token = env.get("HA_API_URL", "").rstrip("/"), env.get("HA_API_TOKEN", "")
    if not base or not token:
        raise SystemExit("HA_API_URL and HA_API_TOKEN are needed for --restart.")
    question = "Restart Home Assistant now? Automations and chargers are offline for 1-5 minutes. [y/N] "
    if not assume_yes and input(question).strip().lower() != "y":
        print("Not restarting. A new integration is only found after a restart.")
        return

    # Intent: POST to the Home Assistant API with the bearer token and return the JSON answer.
    # Why: the config check and the restart share one authenticated call and the token is never printed.
    def call(path: str) -> dict[str, object]:
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        request = urllib.request.Request(base + path, data=b"{}", method="POST", headers=headers)
        with urllib.request.urlopen(request, timeout=60) as response:
            body: dict[str, object] = json.loads(response.read() or "{}")
            return body

    check = call("/config/core/check_config")
    if check.get("result") != "valid":
        raise SystemExit(f"HA reports an invalid configuration, not restarting: {check.get('errors')}")
    try:
        call("/services/homeassistant/restart")
    except TimeoutError, ConnectionError, http.client.HTTPException, urllib.error.URLError:
        pass  # HA drops the connection while it shuts down; that is the expected outcome
    print("Restart requested, waiting for Home Assistant...")
    print(
        "Home Assistant is back." if wait_for_home_assistant(base, token) else "No answer within 5 minutes; check it."
    )


# Intent: command line entry point.
# Why: one command covers checks, deploy, verification and the optional restart.
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", help="override HA_HOST")
    parser.add_argument("--user", help="override HA_SSH_USER")
    parser.add_argument("--key", help="path to an SSH private key (override HA_SSH_KEY)")
    parser.add_argument("--no-sudo", action="store_true", help="do not use sudo on the server")
    parser.add_argument("--batch", action="store_true", help="never prompt; fails instead of asking for a password")
    parser.add_argument("--dry-run", action="store_true", help="show the plan, change nothing")
    parser.add_argument("--skip-checks", action="store_true", help="do not run tools/validate.py first")
    parser.add_argument("--restart", action="store_true", help="validate the HA config and restart Home Assistant")
    parser.add_argument("--yes", action="store_true", help="with --restart: do not ask for confirmation")
    args = parser.parse_args()
    env = parse_env((ROOT / ".env").read_text()) if (ROOT / ".env").exists() else {}
    target = replace(target_from_env(env, args.host, args.user, args.key), sudo=not args.no_sudo)
    files = relative_files()
    if not files:
        raise SystemExit(f"No files found under {INTEGRATION}.")
    files = sorted({*files, BUILD_FILE})
    key_note = f"key {target.key}" if target.key else "no key: ssh will ask for the password"
    print(
        f"Target {target.user}@{target.host}:{target.remote_dir} "
        f"({len(files)} files, backups in {target.backup_dir}, {key_note})"
    )
    if args.dry_run:
        print("\n".join(f"  {name}" for name in files))
        print("Dry run: would run the checks, back up, deploy and verify. Nothing was changed.")
        return 0
    if not args.skip_checks:
        run_checks()
    print(f"Build {write_build_stamp()}")
    with tempfile.TemporaryDirectory() as tmp:
        deploy(target, files, os.path.join(tmp, "ssh-%C"), args.batch)
    if args.restart:
        restart_home_assistant(env, args.yes)
    else:
        print("Next: restart Home Assistant (a changed integration is loaded after a restart).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
