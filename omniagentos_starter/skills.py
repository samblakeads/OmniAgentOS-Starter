"""Agent Skills: a directory of markdown packs your agents plug in and inherit.

A pack is `skills/<category>/<slug>.md`: YAML front-matter plus the sections
WHEN TO USE, INPUTS, WORKFLOW, OUTPUT SPEC and QUALITY CHECKS. The loader is a
pure directory scan — dropping the full Enterprise library into `skills/` raises
the count with no code change, and removing a category makes it unselectable.

QUALITY CHECKS are not decoration: they seed the run's Definition of Done, so a
skill's own standards are what the Critic holds the work to.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config import builtin_skills_dir

SECTION_KEYS = {
    "WHEN TO USE": "when_to_use",
    "INPUTS": "inputs",
    "WORKFLOW": "workflow",
    "OUTPUT SPEC": "output_spec",
    "QUALITY CHECKS": "quality_checks",
}

BUILTIN_SLUG = "general-assistant"

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9\-']*")
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "each", "for", "from", "how", "in", "is", "it",
    "of", "on", "or", "that", "the", "then", "this", "to", "under", "use", "using", "with", "why",
    "write", "make", "create", "one", "two", "three", "you", "your", "them", "their", "into", "about",
}


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall((text or "").lower()) if t not in _STOPWORDS and len(t) > 2]


@dataclass
class SkillPack:
    slug: str
    name: str
    category: str
    summary: str
    when_to_use: str = ""
    inputs: str = ""
    workflow: str = ""
    output_spec: str = ""
    quality_checks: list[str] = field(default_factory=list)
    body: str = ""
    path: str = ""
    builtin: bool = False

    @property
    def id(self) -> str:
        return self.slug

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.body.encode("utf-8")).hexdigest()

    @property
    def keywords(self) -> list[str]:
        return tokenize(" ".join([self.name, self.summary, self.category.replace("-", " "), self.when_to_use]))

    def as_dict(self) -> dict:
        return {
            "id": self.slug,
            "slug": self.slug,
            "name": self.name,
            "category": self.category,
            "summary": self.summary,
            "quality_checks": list(self.quality_checks),
            "sha256": self.sha256,
            "builtin": self.builtin,
        }

    def prompt_block(self) -> str:
        """The exact text injected into a worker prompt, sha included for the oracle."""
        checks = "\n".join(f"- {c}" for c in self.quality_checks) or "- (none declared)"
        return (
            f"<skill id=\"{self.slug}\" category=\"{self.category}\" skill-sha256:{self.sha256}>\n"
            f"NAME: {self.name}\n"
            f"WHEN TO USE: {self.when_to_use}\n"
            f"INPUTS: {self.inputs}\n"
            f"WORKFLOW:\n{self.workflow}\n"
            f"OUTPUT SPEC:\n{self.output_spec}\n"
            f"QUALITY CHECKS:\n{checks}\n"
            f"</skill>"
        )


def _split_front_matter(text: str) -> tuple[dict, str]:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                meta = yaml.safe_load(parts[1]) or {}
            except Exception:
                meta = {}
            if isinstance(meta, dict):
                return meta, parts[2]
    return {}, text


def _split_sections(body: str) -> dict:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in body.splitlines():
        m = re.match(r"^\s{0,3}#{1,4}\s+(.*?)\s*$", line)
        if m:
            heading = re.sub(r"[^A-Za-z ]", "", m.group(1)).strip().upper()
            current = SECTION_KEYS.get(heading)
            if current:
                sections[current] = []
            continue
        if current:
            sections.setdefault(current, []).append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


def parse_pack(path: Path, category: str | None = None, builtin: bool = False) -> SkillPack:
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError(f"empty skill pack: {path.name}")
    meta, body = _split_front_matter(text)
    sections = _split_sections(body)
    slug = str(meta.get("slug") or path.stem).strip()
    checks_raw = sections.get("quality_checks", "")
    checks = [
        re.sub(r"^\s*(?:[-*+]|\d+[.)])\s*", "", ln).strip()
        for ln in checks_raw.splitlines()
        if ln.strip() and re.match(r"^\s*(?:[-*+]|\d+[.)])\s+", ln)
    ]
    pack = SkillPack(
        slug=slug,
        name=str(meta.get("name") or slug.replace("-", " ").title()),
        category=str(meta.get("category") or category or path.parent.name),
        summary=str(meta.get("summary") or "").strip(),
        when_to_use=sections.get("when_to_use", ""),
        inputs=sections.get("inputs", ""),
        workflow=sections.get("workflow", ""),
        output_spec=sections.get("output_spec", ""),
        quality_checks=[c for c in checks if c][:6],
        body=text,
        path=str(path),
        builtin=builtin,
    )
    if not pack.slug:
        raise ValueError(f"skill pack has no slug: {path}")
    return pack


@dataclass
class SkillLibrary:
    packs: list[SkillPack] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    files_on_disk: int = 0
    root: str = ""
    builtin: SkillPack | None = None

    @property
    def count(self) -> int:
        return len(self.packs)

    def by_id(self, skill_id: str) -> SkillPack | None:
        for p in self.packs:
            if p.slug == skill_id:
                return p
        if self.builtin and self.builtin.slug == skill_id:
            return self.builtin
        return None

    def integrity(self) -> dict:
        slugs = [p.slug for p in self.packs]
        dupes = sorted({s for s in slugs if slugs.count(s) > 1})
        ok = not dupes and not self.errors and len(slugs) == self.files_on_disk
        return {
            "ok": ok,
            "parsed": len(slugs),
            "files_on_disk": self.files_on_disk,
            "duplicate_slugs": dupes,
            "errors": list(self.errors),
        }

    def categories(self) -> list[str]:
        return sorted({p.category for p in self.packs})

    def as_dict(self) -> dict:
        return {
            "count": self.count,
            "root": self.root,
            "categories": self.categories(),
            "integrity": self.integrity(),
            "skills": [p.as_dict() for p in self.packs],
        }

    # ---------------------------------------------------------------- select
    def score(self, goal: str) -> list[tuple[SkillPack, float]]:
        goal_tokens = tokenize(goal)
        goal_set = set(goal_tokens)
        scored: list[tuple[SkillPack, float]] = []
        for pack in self.packs:
            kws = pack.keywords
            if not kws:
                scored.append((pack, 0.0))
                continue
            hits = sum(1 for k in set(kws) if k in goal_set)
            phrase_bonus = 1.0 if pack.category.replace("-", " ") in (goal or "").lower() else 0.0
            scored.append((pack, round(hits + phrase_bonus, 3)))
        scored.sort(key=lambda sp: (-sp[1], sp[0].slug))
        return scored

    def select(self, goal: str, k: int = 2) -> tuple[list[SkillPack], list[dict], bool]:
        """Deterministic keyword selection. Returns (packs, scores, fallback_used).

        A single incidental token in common is not a match: a pack must score at
        least 1 and at least half of the best score to be offered. Observed live,
        a cold-email pack scored on one shared word against a goal about agent
        orchestration, and its "exactly one CTA per email body" check then became
        a criterion the deliverable could never satisfy.
        """
        scored = self.score(goal)
        hits = [(p, s) for p, s in scored if s > 0]
        if hits:
            floor = max(1.0, hits[0][1] / 2)
            hits = [(p, s) for p, s in hits if s >= floor]
        top = hits[:k]
        if not top:
            fallback = self.builtin or builtin_pack()
            return [fallback], [{"skill_id": fallback.slug, "score": 0.0}], True
        return (
            [p for p, _ in top],
            [{"skill_id": p.slug, "score": s} for p, s in top],
            False,
        )


_BUILTIN_CACHE: SkillPack | None = None


def builtin_pack() -> SkillPack:
    """The pack that ships inside the package so a run works with an empty skills/."""
    global _BUILTIN_CACHE
    if _BUILTIN_CACHE is None:
        _BUILTIN_CACHE = parse_pack(builtin_skills_dir() / f"{BUILTIN_SLUG}.md", "general", builtin=True)
    return _BUILTIN_CACHE


def load_skills(root: Path | str | None) -> SkillLibrary:
    """Scan `root` for `<category>/<slug>.md` packs. Missing dir → empty library."""
    lib = SkillLibrary(root=str(root or ""), builtin=builtin_pack())
    if not root:
        return lib
    root = Path(root)
    if not root.is_dir():
        return lib
    files = sorted(
        p for p in root.rglob("*.md") if p.is_file() and p.name.lower() not in {"readme.md", "license.md"}
    )
    lib.files_on_disk = len(files)
    seen: set[str] = set()
    for path in files:
        try:
            pack = parse_pack(path)
        except Exception as exc:  # a malformed pack is reported, never silently dropped
            lib.errors.append(f"{path.relative_to(root)}: {exc}")
            continue
        if pack.slug in seen:
            lib.errors.append(f"{path.relative_to(root)}: duplicate slug {pack.slug}")
        seen.add(pack.slug)
        lib.packs.append(pack)
    lib.packs.sort(key=lambda p: (p.category, p.slug))
    return lib
