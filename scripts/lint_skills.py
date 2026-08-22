#!/usr/bin/env python3
"""Lint every skill pack under skills/ against the pack format (see FORMAT.md
in the OmniRogue Agent Skills Library) and scan for leaked internal details.

stdlib only — no third-party dependencies. Exits 1 on any failure, prints one
line per problem found, and a summary at the end.

Usage: python3 scripts/lint_skills.py [skills_dir]
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REQUIRED_FRONT_MATTER_KEYS = ("name", "category", "summary", "works_with", "version")
REQUIRED_SECTIONS = (
    "WHEN TO USE",
    "INPUTS",
    "WORKFLOW",
    "OUTPUT SPEC",
    "EXAMPLE PROMPT",
    "QUALITY CHECKS",
)
VALID_CATEGORIES = {
    "lead-generation",
    "sales",
    "customer-support",
    "marketing-content",
    "creative-production",
    "operations-admin",
    "research-analysis",
    "development-technical",
    "finance-reporting",
    "ecommerce-retail",
}
MIN_BYTES = 900

# Deliberately permissive front-matter scalar parser (stdlib only, no PyYAML):
# supports `key: value` and `key: [a, b, c]` — the only two shapes the format uses.
_FM_LIST_RE = re.compile(r"^\[(.*)\]$")

# Leak-scan patterns: hostnames (internal-looking), emails, IPv4, and local
# filesystem paths. Deliberately broad — false positives are cheap here, a
# leaked customer name or path is not.
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# RFC 2606 reserves these domains for documentation/examples — never real,
# registrable addresses, so a fictional EXAMPLE PROMPT contact ("jane.doe@
# example.com") is not a leak the way a real customer/internal address is.
_EXAMPLE_EMAIL_DOMAINS = ("example.com", "example.org", "example.net")
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
LOCAL_PATH_RE = re.compile(r"(?:/Users/|/home/|C:\\\\Users\\\\)")
HOSTNAME_RE = re.compile(
    r"\b[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\."
    r"(?:internal|local|corp|lan|omnirogue\.com|swaybyte\.com)\b",
    re.IGNORECASE,
)
URL_HOST_RE = re.compile(r"https?://([a-zA-Z0-9.-]+)")

ALLOWED_URL_HOSTS = {
    "omnirogue.com",
    "www.omnirogue.com",
    "github.com",
    "example.com",
    "example.org",
    "example.net",
}

# Provider-key shape patterns — mirrors omniagentos_starter/redact.py's
# _SHAPE_PATTERNS (kept as a literal copy here, not an import, so this script
# stays stdlib-only with zero dependency on the app package) plus a generic
# long hex/base64 blob catch-all the redactor doesn't need (it only redacts
# strings it's told about or that match a provider shape; a lint pass can
# afford to be broader). Bare keys with no surrounding URL/email/hostname
# must still be caught — that's exactly what B5-F6 found missing.
KEY_SHAPE_PATTERNS = [
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-~+/=]{8,}"),
    re.compile(r"\b(?:sk|xai|or|gsk|pk)-[A-Za-z0-9._\-]{12,}"),
    re.compile(r"(?i)\b(?:api[_-]?key|authorization|access[_-]?token)\"?\s*[:=]\s*\"?[A-Za-z0-9._\-]{12,}"),
    re.compile(r"\b[A-Fa-f0-9]{32,}\b"),  # 32+ hex chars: raw key / hash-shaped blob
]
# 32+ base64-alphabet chars, checked separately (not folded into
# KEY_SHAPE_PATTERNS above) because plain English joined by slashes — e.g. an
# outcome-type field like "decision/update/brainstorm/approval" — matches the
# bare character class too. Real base64 secrets/tokens virtually always mix
# in a digit or an uppercase letter; slash-joined lowercase phrasing doesn't.
# So: find candidate spans, then only flag ones that actually look random.
BASE64_BLOB_RE = re.compile(r"\b[A-Za-z0-9+/]{32,}={0,2}\b")


def _looks_like_secret_blob(candidate: str) -> bool:
    has_digit = any(c.isdigit() for c in candidate)
    has_upper = any(c.isupper() for c in candidate)
    has_lower = any(c.islower() for c in candidate)
    # require digit presence, and either genuine case-mixing (upper AND
    # lower both present) or no slash separators at all (a single unbroken
    # run with a digit is still suspicious even if it's one case, e.g. an
    # api token like "abc123..." — but "ABC123/XYZ456"-shaped text with a
    # slash and only one case is more likely structured text than a secret).
    return has_digit and ((has_upper and has_lower) or "/" not in candidate)


def parse_front_matter(text: str, path: Path) -> tuple[dict, str, list[str]]:
    errors: list[str] = []
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        errors.append(f"{path}: missing opening '---' front-matter delimiter")
        return {}, text, errors
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        errors.append(f"{path}: missing closing '---' front-matter delimiter")
        return {}, text, errors

    fm: dict = {}
    for raw in lines[1:end]:
        if not raw.strip():
            continue
        if ":" not in raw:
            errors.append(f"{path}: malformed front-matter line: {raw!r}")
            continue
        key, _, value = raw.partition(":")
        key = key.strip()
        value = value.strip()
        m = _FM_LIST_RE.match(value)
        if m:
            items = [v.strip() for v in m.group(1).split(",") if v.strip()]
            fm[key] = items
        else:
            fm[key] = value

    body = "\n".join(lines[end + 1 :])
    return fm, body, errors


def check_front_matter(fm: dict, path: Path) -> list[str]:
    errors = []
    for key in REQUIRED_FRONT_MATTER_KEYS:
        if key not in fm or fm[key] in ("", [], None):
            errors.append(f"{path}: front-matter missing required key '{key}'")
    if "category" in fm and fm["category"] not in VALID_CATEGORIES:
        errors.append(
            f"{path}: front-matter category '{fm.get('category')}' is not one "
            f"of the 10 valid categories"
        )
    if "works_with" in fm and isinstance(fm["works_with"], list):
        if not (2 <= len(fm["works_with"]) <= 4):
            errors.append(
                f"{path}: works_with must list 2-4 roles, found {len(fm['works_with'])}"
            )
    if "summary" in fm and isinstance(fm["summary"], str):
        word_count = len(fm["summary"].split())
        if not (15 <= word_count <= 35):
            errors.append(
                f"{path}: summary should be 15-35 words, found {word_count}"
            )
    return errors


def check_sections(body: str, path: Path) -> list[str]:
    errors = []
    positions = []
    for section in REQUIRED_SECTIONS:
        header = f"## {section}"
        idx = body.find(header)
        if idx == -1:
            errors.append(f"{path}: missing required section '{header}'")
        else:
            positions.append((idx, section))
    # order check only over the sections that were actually found
    found_order = [s for _, s in sorted(positions)]
    expected_order = [s for s in REQUIRED_SECTIONS if s in found_order]
    if found_order != expected_order:
        errors.append(f"{path}: sections are present but out of the required order")

    # WORKFLOW: >=6 numbered steps
    wf_idx = body.find("## WORKFLOW")
    if wf_idx != -1:
        next_idx = min(
            [body.find(f"## {s}", wf_idx + 1) for s in REQUIRED_SECTIONS]
            + [len(body)],
            key=lambda x: (x if x != -1 else len(body)),
        )
        wf_block = body[wf_idx:next_idx] if next_idx != -1 else body[wf_idx:]
        steps = re.findall(r"^\d+\.\s+\S", wf_block, re.MULTILINE)
        if len(steps) < 6:
            errors.append(
                f"{path}: WORKFLOW has {len(steps)} numbered steps, needs >=6"
            )

    # QUALITY CHECKS: >=3 bullet items
    qc_idx = body.find("## QUALITY CHECKS")
    if qc_idx != -1:
        qc_block = body[qc_idx:]
        checks = re.findall(r"^-\s+\S", qc_block, re.MULTILINE)
        if len(checks) < 3:
            errors.append(
                f"{path}: QUALITY CHECKS has {len(checks)} items, needs >=3"
            )

    # EXAMPLE PROMPT: fenced block present
    ep_idx = body.find("## EXAMPLE PROMPT")
    if ep_idx != -1 and "```" not in body[ep_idx:]:
        errors.append(f"{path}: EXAMPLE PROMPT has no fenced code block")

    return errors


def check_leaks(text: str, path: Path) -> list[str]:
    errors = []
    for m in EMAIL_RE.finditer(text):
        addr = m.group(0)
        domain = addr.rsplit("@", 1)[-1].lower()
        if domain in _EXAMPLE_EMAIL_DOMAINS:
            continue
        errors.append(f"{path}: possible email address leaked: {addr!r}")
    for m in IPV4_RE.finditer(text):
        errors.append(f"{path}: possible IP address leaked: {m.group(0)!r}")
    if LOCAL_PATH_RE.search(text):
        m = LOCAL_PATH_RE.search(text)
        errors.append(f"{path}: possible local filesystem path leaked near {m.group(0)!r}")
    for m in HOSTNAME_RE.finditer(text):
        errors.append(f"{path}: possible internal hostname leaked: {m.group(0)!r}")
    for m in URL_HOST_RE.finditer(text):
        host = m.group(1).lower()
        if host not in ALLOWED_URL_HOSTS and not host.endswith(".omnirogue.com"):
            errors.append(f"{path}: URL host not on the allow-list: {host!r}")
    for pat in KEY_SHAPE_PATTERNS:
        for m in pat.finditer(text):
            errors.append(f"{path}: possible provider-key shape leaked: {m.group(0)!r}")
    for m in BASE64_BLOB_RE.finditer(text):
        if _looks_like_secret_blob(m.group(0)):
            errors.append(f"{path}: possible provider-key shape leaked: {m.group(0)!r}")
    return errors


def lint_file(path: Path) -> list[str]:
    errors = []
    size = path.stat().st_size
    if size < MIN_BYTES:
        errors.append(f"{path}: file is {size} bytes, needs >= {MIN_BYTES}")

    text = path.read_text(encoding="utf-8")
    fm, body, fm_errors = parse_front_matter(text, path)
    errors.extend(fm_errors)
    if fm:
        errors.extend(check_front_matter(fm, path))
        errors.extend(check_sections(body, path))
    errors.extend(check_leaks(text, path))
    return errors


def check_no_shell_exec() -> list[str]:
    """Belt-and-braces: this file itself must not import subprocess/os.system —
    a skill lint script has no reason to shell out."""
    errors = []
    banned = {"subprocess"}
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"), filename=str(__file__))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in banned:
                    errors.append(f"{__file__}: banned import {alias.name!r}")
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in banned:
                errors.append(f"{__file__}: banned import {node.module!r}")
    return errors


# Red-first self-test fixtures for the leak scanner (B5-F6): synthetic,
# never-real secret shapes, checked against check_leaks() on every run before
# any real pack is scanned. If this ever fails, the scanner itself has
# regressed and every downstream "0 problems" result would be a false
# negative — so a self-test failure is a distinct, louder failure than a
# normal lint finding, and it blocks the real scan from running at all.
_SELF_TEST_FIXTURES = [
    ("bearer token", "Authorization: Bearer sk-abcdefghijklmnopqrstuv"),
    ("xai- prefixed key, no surrounding url/email", "export XAI_API_KEY=xai-abcdefghijklmnopqrstuvwxyz0123456789"),
    ("sk- prefixed key alone", "token is sk-liveabcdefghijklmnopqrstuvwx, keep it secret"),
    ("api_key= assignment", 'api_key="abcdefghijklmnopqrstuvwx123456"'),
    ("32+ hex blob", "raw value: 0123456789abcdef0123456789abcdef0123"),
    ("32+ base64-ish blob", "blob: QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVowMTIzNDU2Nzg5"),
]


def self_test() -> list[str]:
    """Prove check_leaks() still catches every planted key shape. Returns a
    list of failure descriptions (empty = self-test passed)."""
    failures = []
    dummy = Path("selftest-fixture.md")
    for label, text in _SELF_TEST_FIXTURES:
        errs = check_leaks(text, dummy)
        if not errs:
            failures.append(
                f"self-test regression: check_leaks() found nothing for the "
                f"{label!r} fixture ({text!r}) — a real leak of this shape "
                f"would silently pass lint_skills"
            )
    return failures


def main() -> int:
    self_test_failures = self_test()
    if self_test_failures:
        print("lint_skills: SELF-TEST FAILED — the leak scanner itself is broken, refusing to scan:\n", file=sys.stderr)
        for f in self_test_failures:
            print(f"  FAIL  {f}", file=sys.stderr)
        return 1

    skills_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "skills"
    if not skills_dir.is_dir():
        print(f"lint_skills: skills directory not found: {skills_dir}", file=sys.stderr)
        return 1

    files = sorted(skills_dir.rglob("*.md"))
    files = [f for f in files if f.name != "README.md"]
    if not files:
        print(f"lint_skills: no skill pack .md files found under {skills_dir}", file=sys.stderr)
        return 1

    all_errors: list[str] = []
    slugs: dict[str, Path] = {}

    for f in files:
        rel = f.relative_to(skills_dir)
        # slug uniqueness across the whole library, regardless of category
        slug = f.stem
        if slug in slugs:
            all_errors.append(
                f"{rel}: duplicate slug '{slug}' also used by {slugs[slug]}"
            )
        else:
            slugs[slug] = rel
        all_errors.extend(lint_file(f))

    all_errors.extend(check_no_shell_exec())

    if all_errors:
        print(f"lint_skills: {len(all_errors)} problem(s) found in {len(files)} pack(s):\n")
        for e in all_errors:
            print(f"  FAIL  {e}")
        return 1

    print(f"lint_skills: OK — {len(files)} pack(s) checked under {skills_dir}, 0 problems, {len(slugs)} distinct slugs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
