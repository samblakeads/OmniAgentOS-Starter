#!/usr/bin/env python3
"""Lint every agent file under agents/ against the front-matter schema in
PLAN.md's Round 6 (AGENTS) and Round 8 (TEAMS) sections and agents/README.md,
and reuse lint_skills.py's leak scan (hostnames/emails/IPs/paths/provider-key
shapes).

Team validation mirrors omniagentos_starter/agents.py::_validate_hierarchy()
exactly (self-reference, missing members, cycles, MAX_TEAM_DEPTH) so a bad
team: line is caught here before it ever reaches the loader's disable-with-
reason path — this is a stricter pre-flight, not a looser reimplementation.

stdlib only — no third-party dependencies. Exits 1 on any failure, prints one
line per problem found, and a summary at the end.

Usage: python3 scripts/lint_agents.py [agents_dir] [skills_dir]
"""
from __future__ import annotations

import re
import sys
from importlib import util as _importlib_util
from pathlib import Path

# Reuse lint_skills.py's leak scan rather than re-deriving the same regexes a
# third time (redact.py has the first copy, lint_skills.py the second) — both
# scripts live in scripts/ and are stdlib-only, so this is a sibling import,
# not a dependency on the omniagentos_starter package.
_LINT_SKILLS_PATH = Path(__file__).resolve().parent / "lint_skills.py"
_spec = _importlib_util.spec_from_file_location("lint_skills", _LINT_SKILLS_PATH)
lint_skills = _importlib_util.module_from_spec(_spec)
_spec.loader.exec_module(lint_skills)

REQUIRED_FRONT_MATTER_KEYS = (
    "name",
    "title",
    "persona",
    "skills",
    "tools",
    "memory_scope",
    "visibility",
    "version",
)
ALLOWED_TOOLS = {"read_file", "write_file", "list_files"}
ALLOWED_VISIBILITY = {"public", "private"}
MAX_BYTES = 1536  # 1.5 KB ceiling per PLAN.md Round 6
# Mirrors omniagentos_starter/agents.py's MAX_TEAM_MEMBERS / MAX_TEAM_DEPTH
# exactly (literal copy, not an import — same stdlib-only pattern as the rest
# of this script).
MAX_TEAM_MEMBERS = 8
MAX_TEAM_DEPTH = 2


def discover_skill_slugs(skills_dir: Path) -> set[str]:
    if not skills_dir.is_dir():
        return set()
    return {p.stem for p in skills_dir.rglob("*.md") if p.name.lower() != "readme.md"}


def check_front_matter(fm: dict, path: Path, skill_slugs: set[str]) -> list[str]:
    errors = []
    for key in REQUIRED_FRONT_MATTER_KEYS:
        if key not in fm:
            errors.append(f"{path}: front-matter missing required key '{key}'")
            continue
        # "skills" may legitimately be an empty list — the shipped built-in
        # generalist (agents/_builtin/general-worker.md) has no specific
        # pack and falls back to whatever the router hands it. Every other
        # required key must still have a real, non-empty value.
        if key != "skills" and fm[key] in ("", [], None):
            errors.append(f"{path}: front-matter missing required key '{key}'")

    if "skills" in fm:
        skills = fm["skills"] if isinstance(fm["skills"], list) else [fm["skills"]]
        # An empty list is valid (the built-in generalist ships with skills: []
        # and falls back to whatever the router hands it) — only a *named*
        # skill that doesn't exist is an error.
        for s in skills:
            if s not in skill_slugs:
                errors.append(
                    f"{path}: skill '{s}' does not exist under skills/ — "
                    f"a referenced skill must exist (load error, never silently ignored)"
                )

    if "tools" in fm:
        tools = fm["tools"] if isinstance(fm["tools"], list) else [fm["tools"]]
        for t in tools:
            if t not in ALLOWED_TOOLS:
                errors.append(
                    f"{path}: tool '{t}' is not on the global allow-list {sorted(ALLOWED_TOOLS)} — "
                    f"an agent's tools may only narrow it, never widen it"
                )

    if "visibility" in fm and fm["visibility"] not in ALLOWED_VISIBILITY:
        errors.append(
            f"{path}: visibility '{fm.get('visibility')}' must be one of {sorted(ALLOWED_VISIBILITY)}"
        )

    if "persona" in fm and isinstance(fm["persona"], str):
        sentence_count = len([s for s in re.split(r"(?<=[.!?])\s+", fm["persona"].strip()) if s])
        if not (2 <= sentence_count <= 4):
            errors.append(
                f"{path}: persona should be 2-4 sentences, found {sentence_count}"
            )

    # team: is optional (Round 8) — only present on a manager agent. Presence
    # and shape only here; cross-file checks (self, missing, cycle, depth)
    # need every agent's slug and team, so they happen in check_hierarchy()
    # after every file has been parsed once.
    if "team" in fm:
        team = fm["team"] if isinstance(fm["team"], list) else [fm["team"]]
        if len(team) > MAX_TEAM_MEMBERS:
            errors.append(
                f"{path}: team has {len(team)} members, must be <= {MAX_TEAM_MEMBERS}"
            )

    return errors


def parse_team(fm: dict) -> list[str]:
    team = fm.get("team") or []
    if isinstance(team, str):
        team = [team]
    return [str(t).strip() for t in team if str(t).strip()]


def check_hierarchy(team_by_slug: dict[str, list[str]], all_slugs: set[str]) -> list[str]:
    """Cross-file team validation, mirroring _validate_hierarchy() exactly:
    self-reference, missing members, cycles (including 2-agent A<->B loops),
    and MAX_TEAM_DEPTH — a manager-of-managers chain deeper than 2."""
    errors: list[str] = []

    for slug, team in team_by_slug.items():
        if not team:
            continue
        if slug in team:
            errors.append(f"agents/{slug}.md: team includes itself — an agent cannot delegate to itself")
            continue
        missing = [m for m in team if m not in all_slugs]
        if missing:
            errors.append(
                f"agents/{slug}.md: team members are not in the roster: {', '.join(sorted(missing))}"
            )

    def depth(slug: str, seen: tuple[str, ...]) -> int | str:
        if slug in seen:
            return "team contains a cycle: " + " → ".join([*seen, slug])
        team = team_by_slug.get(slug) or []
        if not team:
            return 0
        deepest = 0
        for member in team:
            below = depth(member, (*seen, slug))
            if isinstance(below, str):
                return below
            deepest = max(deepest, below)
        return deepest + 1

    for slug, team in team_by_slug.items():
        if not team or slug in team or any(m not in all_slugs for m in team):
            continue  # already reported above; do not double-count
        result = depth(slug, ())
        if isinstance(result, str):
            errors.append(f"agents/{slug}.md: {result}")
        elif result > MAX_TEAM_DEPTH:
            errors.append(
                f"agents/{slug}.md: delegation chain is {result} deep; "
                f"the limit is {MAX_TEAM_DEPTH} so a run's provenance stays explainable"
            )

    return errors


def check_body(text: str, path: Path) -> list[str]:
    """PLAN.md's schema only requires the body to BE standing instructions —
    not any specific heading. A non-empty body is what's actually binding."""
    errors = []
    if not text.strip():
        errors.append(f"{path}: body (after the front-matter) is empty")
    return errors


def lint_file(path: Path, skill_slugs: set[str]) -> list[str]:
    errors = []
    size = path.stat().st_size
    if size > MAX_BYTES:
        errors.append(f"{path}: file is {size} bytes, must be <= {MAX_BYTES} (1.5 KB)")

    text = path.read_text(encoding="utf-8")
    fm, body, fm_errors = lint_skills.parse_front_matter(text, path)
    errors.extend(fm_errors)
    if fm:
        errors.extend(check_front_matter(fm, path, skill_slugs))
        errors.extend(check_body(body, path))
    errors.extend(lint_skills.check_leaks(text, path))
    return errors


def main() -> int:
    self_test_failures = lint_skills.self_test()
    if self_test_failures:
        print("lint_agents: SELF-TEST FAILED (via lint_skills) — the leak scanner itself is broken, refusing to scan:\n", file=sys.stderr)
        for f in self_test_failures:
            print(f"  FAIL  {f}", file=sys.stderr)
        return 1

    repo_root = Path(__file__).resolve().parent.parent
    agents_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else repo_root / "agents"
    skills_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else repo_root / "skills"

    if not agents_dir.is_dir():
        print(f"lint_agents: agents directory not found: {agents_dir}", file=sys.stderr)
        return 1

    skill_slugs = discover_skill_slugs(skills_dir)

    # rglob, not glob: a roster _builtin/ subdirectory (e.g. general-worker.md)
    # is a real, checked part of the shipped roster, not just top-level files.
    files = sorted(p for p in agents_dir.rglob("*.md") if p.name.lower() != "readme.md")
    if not files:
        print(f"lint_agents: no agent .md files found under {agents_dir}", file=sys.stderr)
        return 1

    all_errors: list[str] = []
    slugs: dict[str, Path] = {}
    team_by_slug: dict[str, list[str]] = {}

    for f in files:
        slug = f.stem
        if slug in slugs:
            all_errors.append(f"{f}: duplicate slug '{slug}' also used by {slugs[slug]}")
        else:
            slugs[slug] = f
        all_errors.extend(lint_file(f, skill_slugs))
        try:
            fm, _body, _fm_errors = lint_skills.parse_front_matter(
                f.read_text(encoding="utf-8"), f
            )
        except Exception:
            fm = {}
        if fm and "team" in fm:
            team_by_slug[slug] = parse_team(fm)

    # Cross-file hierarchy checks need every slug and team known first — a
    # manager listed earlier in sorted order than its member is still valid.
    all_errors.extend(check_hierarchy(team_by_slug, set(slugs)))

    if all_errors:
        print(f"lint_agents: {len(all_errors)} problem(s) found in {len(files)} agent(s):\n")
        for e in all_errors:
            print(f"  FAIL  {e}")
        return 1

    print(
        f"lint_agents: OK — {len(files)} agent(s) checked under {agents_dir}, "
        f"0 problems, {len(slugs)} distinct slugs"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
