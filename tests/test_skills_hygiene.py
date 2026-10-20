"""Agent skills in .agents/skills must be well-formed and must not leak private details."""

from __future__ import annotations

import re
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[1] / ".agents" / "skills"
# A private-range IPv4 address or a literal password/token assignment must never be committed in a skill.
PRIVATE_IP = re.compile(r"\b(?:192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b")
SECRET_ASSIGNMENT = re.compile(r"(?i)\b(?:password|token|secret)\s*[:=]\s*(?!<|\$|\"?\{)[^\s`<]{6,}")


# Intent: list every skill file so each test sees the same set.
# Why: a skill directory without SKILL.md is a mistake that should fail loudly, not be skipped.
def _skill_files() -> list[Path]:
    return sorted(path / "SKILL.md" for path in SKILLS.iterdir() if path.is_dir())


# Intent: require frontmatter whose name equals the directory name and that has a description.
# Why: the agent loads a skill by name; a mismatch makes it silently unfindable.
def test_frontmatter_name_matches_directory() -> None:
    for skill in _skill_files():
        assert skill.exists(), f"{skill.parent.name} has no SKILL.md"
        header = skill.read_text(encoding="utf-8").split("---")[1]
        name = re.search(r"^name:\s*(\S+)", header, re.MULTILINE)
        assert name is not None and name.group(1) == skill.parent.name, skill
        assert re.search(r"^description:\s*\S+", header, re.MULTILINE), f"{skill} has no description"


# Intent: reject private IP addresses and literal secrets in skills.
# Why: the repository may become public; a skill should use placeholders such as <ha-host>.
def test_skills_contain_no_private_addresses_or_secrets() -> None:
    for skill in _skill_files():
        text = skill.read_text(encoding="utf-8")
        assert not PRIVATE_IP.search(text), f"{skill} contains a private IP address"
        assert not SECRET_ASSIGNMENT.search(text), f"{skill} contains what looks like a secret"
