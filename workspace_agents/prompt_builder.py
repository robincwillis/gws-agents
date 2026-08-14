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

# Output formatting rules — prepended to every agent's system prompt.
# Responses are rendered in both Slack mrkdwn and a plain terminal. Neither
# renders standard markdown tables or `## headings`, and Slack treats `**bold**`
# as literal asterisks. The rules below produce text that looks decent in both.
_OUTPUT_FORMAT_GUIDE = """\
## Output Formatting — read carefully

Your responses are shown in **two surfaces simultaneously**: a plain terminal \
(no markdown rendering) and Slack (limited mrkdwn — no markdown tables, no \
`## headings`, and `**double asterisks**` are shown as literal characters). \
Follow these rules so your output looks decent in both:

- **Bold:** use `*bold*` (single asterisks). Never `**bold**`.
- **Headings:** use a bare `*Heading:*` line. Never `#`, `##`, or `###`.
- **Tables:** never use markdown pipe tables. Render ASCII tables (space- or \
pipe-padded columns) inside a triple-backtick fence — both surfaces render \
fences as monospace blocks.
- **JSON:** always pretty-print with 2-space indent, inside ```json fences. \
Never inline minified JSON in prose.
- **Lists:** use `-` or `•` prefixes — both render in both surfaces.
- **Code, paths, IDs, flags:** wrap in single backticks.
- **No ANSI escape codes** in response text — they show as garbage in Slack.
- **No emoji** unless the user asked for them.

Example of a good results table:

```
File                          Size    Modified
----------------------------  ------  ----------
2024-Q3-financials.xlsx       18 MB   2024-10-02
quarterly-report.pdf          12 MB   2024-09-30
```
"""


def _wrap_with_format_guide(prompt: str) -> str:
    """Prepend the universal output-format guide to an agent prompt."""
    return f"{_OUTPUT_FORMAT_GUIDE}\n---\n\n{prompt}"

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
        return _wrap_with_format_guide(composed)

    parts = [composed, "\n---\n\n## Embedded gws Command Reference\n"]
    for skill_name in gws_skills:
        parts.append(_read_gws_skill(skill_name))

    return _wrap_with_format_guide("\n".join(parts))
