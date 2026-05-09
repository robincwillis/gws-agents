"""Compose agent system prompts from base skill files + gws CLI reference skills."""

import re
from pathlib import Path

_SKILLS_DIR = Path(__file__).parent / "skills"
# gws CLI skills are installed into _SKILLS_DIR by `gws generate-skills`
# (see README setup step). Both base agent prompts and gws skill packs live
# alongside each other in the same directory.
_GWS_SKILLS_DIR = _SKILLS_DIR

# Which gws skills to embed per agent — order determines section order in the prompt.
# Rule: always list gws-shared first, then the base service skill, then helper
# sub-skills. Helpers (+triage, etc.) provide concrete call patterns that
# avoid common mistakes (e.g. missing userId) in the lower-level API resources.
SKILLS_MAP: dict[str, list[str]] = {
    "sentinel":  ["gws-shared", "gws-gmail", "gws-drive"],
    "auction":   ["gws-shared", "gws-gmail", "gws-drive", "gws-sheets"],
    "gardener":  ["gws-shared", "gws-gmail", "gws-gmail-triage"],
    "architect": ["gws-shared", "gws-drive", "gws-drive-upload"],
    "organizer": ["gws-shared", "gws-drive", "gws-docs", "gws-docs-write"],
}

# Replaces the verbose discovery block in every base skill file.
_DISCOVERY_REPLACEMENT = """\
## Command Reference — pre-loaded

The embedded reference at the end of this prompt covers all common operations. \
Use it directly — do not run `--help` or `schema` for operations listed there.

**Error recovery (for unlisted operations only):** run \
`gws_cli("<service> <resource> --help")`, then \
`gws_cli("schema <service>.<resource>.<method>")`, then fall back to \
`mcp_workspace-developer_search_workspace_docs`.
"""

_DISCOVERY_RE = re.compile(
    r"## Command Discovery.*?(?=\n## )",
    re.DOTALL,
)


def _strip_frontmatter(text: str) -> str:
    """Remove YAML front matter (--- ... ---) from a skill file."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:].lstrip("\n")
    return text


def _read_gws_skill(skill_name: str) -> str:
    path = _GWS_SKILLS_DIR / skill_name / "SKILL.md"
    return _strip_frontmatter(path.read_text())


def build_skill(name: str) -> str:
    """Return the composed system prompt for an agent.

    Reads the base skill file, replaces the discovery boilerplate with a
    compact fallback note, then appends the relevant gws CLI reference sections.
    """
    base = (_SKILLS_DIR / f"{name}.md").read_text()
    composed = _DISCOVERY_RE.sub(_DISCOVERY_REPLACEMENT, base)

    gws_skills = SKILLS_MAP.get(name, [])
    if not gws_skills:
        return composed

    parts = [composed, "\n---\n\n## Embedded gws Command Reference\n"]
    for skill_name in gws_skills:
        parts.append(_read_gws_skill(skill_name))

    return "\n".join(parts)
