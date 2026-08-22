"""How this could pass while broken: LICENSE first line 'MIT' on an empty file, skills count hardcoded, or runtime paths committed; now gitleaks (or equivalent) is clean, git ls-files has no var/workspace/.env/*.sqlite3, LICENSE matches OSI MIT (trademark paragraph allowed after), and shipped skills <= 12."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from _harness import REPO_ROOT, write_json

OSI_MIT_BODY = """Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE."""

SECRET_RE = re.compile(
    r"(?i)(sk-[A-Za-z0-9]{16,}|xai-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|"
    r"-----BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY-----|"
    r"ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"
)
INTERNAL_RE = re.compile(
    r"(?i)(\b(?:10|127)\.\d+\.\d+\.\d+\b|\b192\.168\.\d+\.\d+\b|"
    r"[A-Za-z0-9._%+-]+@omnirogue\.internal\b|"
    r"\b(?:macstudio|mw2556|mw2586)\b)"
)


def test_d14_hygiene():
    notes = []

    # gitleaks or equivalent
    gitleaks = shutil.which("gitleaks")
    if gitleaks:
        proc = subprocess.run(
            [gitleaks, "detect", "--source", str(REPO_ROOT), "--no-banner", "--redact"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        notes.append({"gitleaks_code": proc.returncode, "tail": (proc.stdout + proc.stderr)[-2000:]})
        assert proc.returncode == 0, f"gitleaks not clean:\n{(proc.stdout + proc.stderr)[-2000:]}"
    else:
        hits = []
        skip_parts = {".git", ".venv", "node_modules", "__pycache__", "devtasks"}
        for p in REPO_ROOT.rglob("*"):
            if any(part in skip_parts for part in p.parts):
                continue
            if not p.is_file():
                continue
            if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".sqlite3", ".pyc"}:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if SECRET_RE.search(text):
                hits.append(str(p.relative_to(REPO_ROOT)))
        notes.append({"equivalent_scan_hits": hits})
        assert hits == [], f"secret scan hits: {hits}"

    lint = REPO_ROOT / "scripts" / "lint_skills.py"
    if lint.is_file():
        py = REPO_ROOT / ".venv" / "bin" / "python"
        exe = str(py) if py.is_file() else "python3"
        proc = subprocess.run(
            [str(exe) if Path(exe).exists() else exe, str(lint)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        notes.append({"lint_skills_code": proc.returncode, "out": (proc.stdout + proc.stderr)[-2000:]})
        assert proc.returncode == 0, f"lint_skills not clean:\n{(proc.stdout + proc.stderr)[-1500:]}"
    else:
        skills = REPO_ROOT / "skills"
        hits = []
        if skills.is_dir():
            for p in skills.rglob("*"):
                if not p.is_file():
                    continue
                try:
                    text = p.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                if INTERNAL_RE.search(text):
                    hits.append(str(p.relative_to(REPO_ROOT)))
        notes.append({"skills_internal_hits": hits})
        assert hits == [], f"internal hostnames/emails/IPs under skills/: {hits}"

    listed = subprocess.check_output(
        ["git", "-C", str(REPO_ROOT), "ls-files"],
        text=True,
    ).splitlines()
    forbidden = []
    for rel in listed:
        if rel.startswith("var/") or "/var/" in rel:
            forbidden.append(rel)
        if rel.startswith("workspace/") or rel.startswith("workspace\\"):
            forbidden.append(rel)
        if rel == ".env" or rel.endswith("/.env"):
            forbidden.append(rel)
        if rel.endswith(".sqlite3") or rel.endswith(".sqlite3-wal") or rel.endswith(".sqlite3-shm"):
            forbidden.append(rel)
    assert forbidden == [], f"git ls-files contains runtime paths: {forbidden}"

    license_path = REPO_ROOT / "LICENSE"
    assert license_path.is_file(), "LICENSE missing"
    lic = license_path.read_text(encoding="utf-8")
    assert lic.lstrip().lower().startswith("mit license"), (
        "LICENSE first bytes must be the OSI MIT title 'MIT License'"
    )
    # Normalize: skip title + copyright line(s), compare grant body.
    body = re.sub(r"^MIT License\s*", "", lic.lstrip(), flags=re.I).lstrip()
    body = re.sub(r"^Copyright[^\n]*\n+", "", body, flags=re.I)
    # Allow additional copyright lines
    while re.match(r"^Copyright[^\n]*\n+", body, flags=re.I):
        body = re.sub(r"^Copyright[^\n]*\n+", "", body, flags=re.I)
    mit = " ".join(OSI_MIT_BODY.split())
    have = " ".join(body.split())
    assert have.startswith(mit) or mit in have, (
        "LICENSE body does not match OSI MIT text "
        "(a trademark paragraph is allowed to FOLLOW the MIT text)"
    )

    skills_root = REPO_ROOT / "skills"
    count = 0
    if skills_root.is_dir():
        count = sum(
            1
            for p in skills_root.rglob("*.md")
            if p.name.lower() != "readme.md"
        )
    assert count <= 12, f"shipped skills count {count} > 12"
    notes.append({"skills_count": count, "ls_files": len(listed)})
    write_json("d14-hygiene.json", notes)
