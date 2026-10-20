"""The pure parts of tools/deploy_ha.py: settings, digests, the remote script and the ssh command line."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "deploy_ha.py"


# Intent: load the deploy tool by path, because tools/ is not a package.
# Why: the tests exercise the real functions, not a copy.
def _load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("deploy_ha", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses looks the module up in sys.modules while it builds a class, so register it first.
    sys.modules["deploy_ha"] = module
    spec.loader.exec_module(module)
    return module


deploy = _load_tool()


# Intent: .env parsing ignores comments and blank lines and strips quotes.
# Why: the real .env mixes all three, and a quote left on a value breaks the host name.
def test_parse_env_handles_comments_and_quotes() -> None:
    env = deploy.parse_env("# note\n\nHA_HOST=\"ha.local\"\nHA_SSH_USER = admin \nHA_SSH_KEY='~/.ssh/key'\n")

    assert env == {"HA_HOST": "ha.local", "HA_SSH_USER": "admin", "HA_SSH_KEY": "~/.ssh/key"}


# Intent: the target falls back to the host of HA_API_URL and never reads a password.
# Why: most .env files only carry the API URL; a missing user must stop the run.
def test_target_resolution_uses_api_url_host_and_requires_a_user() -> None:
    target = deploy.target_from_env({"HA_API_URL": "http://ha.local:8123/api", "HA_SSH_USER": "admin"})

    assert (target.host, target.user, target.key) == ("ha.local", "admin", None)
    with pytest.raises(SystemExit):
        deploy.target_from_env({"HA_HOST": "ha.local"})


# Intent: the comparison reports missing, unexpected and different files separately.
# Why: a verified deploy needs all three lists empty, and each kind points to a different problem.
def test_compare_separates_missing_extra_and_different() -> None:
    local = {"a.py": "1", "b.py": "2", "c.py": "3"}
    remote = deploy.parse_sha256sum("1  ./a.py\n9  ./b.py\n4  ./d.py\n")

    assert deploy.compare(local, remote) == (["c.py"], ["d.py"], ["b.py"])


# Intent: the install script unpacks the new copy completely before it replaces the old one, and backs up first.
# Why: a broken transfer must leave the running integration untouched.
def test_remote_script_backs_up_unpacks_then_swaps() -> None:
    script = deploy.remote_install_script(deploy.Target("h", "u"))

    assert script.index("tar -C '/config/custom_components' -czf") < script.index("-xf -") < script.index("mv '/config")
    assert "ocpp_hub" in script and "tail -n +4" in script


# Intent: privileged commands use `sudo -n`, which fails rather than prompting for a password.
# Why: nothing in this tool may ever wait on or type a password.
def test_privileged_never_prompts() -> None:
    assert deploy.privileged("echo hi", True).startswith("sudo -n sh -c ")
    assert deploy.privileged("echo hi", False) == "echo hi"


# Intent: batch mode adds BatchMode=yes and a key is passed by path with IdentitiesOnly.
# Why: an unauthorised key must fail at once, and only the named key may be offered.
def test_ssh_command_is_batch_and_key_only() -> None:
    command = deploy.ssh_command(deploy.Target("h", "u", key="/k"), "/tmp/cp", "true", batch=True)

    assert "BatchMode=yes" in command and "IdentitiesOnly=yes" in command
    assert command[command.index("-i") + 1] == "/k"
    assert command[-2:] == ["u@h", "true"]
