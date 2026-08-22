"""How this could pass while broken: a hardcoded skills count, always-emitted marketing category, and no skill body in the worker prompt; now parsed-valid==disk==API, ablation removes a category from a temp root, skill-sha256 matches the file body, and select() is a deterministic keyword shortlist."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from pathlib import Path

import httpx
import yaml
from _harness import (
    REPO_ROOT,
    collect_sse,
    event_payload,
    events_of,
    load_prompts,
    spawn_serve,
    start_run,
    write_json,
)

REQUIRED_SECTIONS = (
    "WHEN TO USE",
    "INPUTS",
    "WORKFLOW",
    "OUTPUT SPEC",
    "QUALITY CHECKS",
)


def _skills_root() -> Path:
    env = os.environ.get("OMNIAGENTOS_SKILLS_ROOT")
    if env:
        return Path(env)
    p = REPO_ROOT / "skills"
    if p.is_dir():
        return p
    raise AssertionError("skills/ directory missing at repo root")


def _iter_skill_files(root: Path) -> list[Path]:
    files = sorted(p for p in root.rglob("*.md") if p.name.lower() != "readme.md")
    return files


def _parse_valid(path: Path) -> dict | None:
    """Oracle definition of parsed-valid (BINDING on the loader)."""
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return None
    fm: dict = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                loaded = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                return None
            if not isinstance(loaded, dict):
                return None
            fm = loaded
            body = parts[2]
    heads = {m.group(1).strip().upper() for m in re.finditer(r"^#{1,6}\s+(.+?)\s*$", body, re.M)}
    # Also accept ALL-CAPS section titles as paragraphs.
    for sec in REQUIRED_SECTIONS:
        if sec not in heads and not re.search(rf"^{re.escape(sec)}\s*$", body, re.M):
            return None
    qc = re.search(
        r"(?:QUALITY CHECKS)(.*?)(?:\n#{1,6}\s|\Z)",
        body,
        re.S | re.I,
    )
    if qc and not re.search(r"^[\s]*[-*]\s+\S", qc.group(1), re.M):
        # allow numbered lists
        if not re.search(r"^[\s]*\d+\.\s+\S", qc.group(1), re.M):
            return None
    slug = path.stem
    if not slug:
        return None
    category = path.parent.name
    return {
        "slug": slug,
        "category": category,
        "path": path,
        "body": text,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "front_matter": fm,
        "id": fm.get("id") or f"{category}/{slug}",
    }


def test_d06_count_ablation_sha_deterministic():
    root = _skills_root()
    files = _iter_skill_files(root)
    assert files, f"no skill markdown files under {root}"
    parsed = []
    slugs = []
    for f in files:
        item = _parse_valid(f)
        assert item is not None, f"skill file not parsed-valid: {f}"
        slugs.append(item["slug"])
        parsed.append(item)
    assert len(set(slugs)) == len(slugs), f"duplicate slugs: {slugs}"
    parsed_count = len(parsed)
    disk_count = len(files)
    assert parsed_count == disk_count

    srv = spawn_serve()
    try:
        resp = httpx.get(srv.base_url + "/api/skills", timeout=15.0)
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        api_count = payload.get("count")
        items = payload.get("items") or []
        if api_count is None:
            api_count = len(items)
        assert api_count == parsed_count == disk_count, (
            f"parsed-valid={parsed_count} disk={disk_count} API={api_count} "
            "(all three must agree; two-of-three is a fail)"
        )
        assert len(items) == api_count

        # Deterministic select: import skills.select, same goal twice, same ids.
        from omniagentos_starter.skills import select  # type: ignore

        goal = "Write three paid ad headlines under a tight character limit for a landing page."
        a = select(goal, k=2)
        b = select(goal, k=2)
        assert a == b, "select() is not deterministic (must be keyword score, not a live LLM pick)"
        # score==0 path uses general-assistant
        zero = select("zzzzqxqxqxqx not-a-real-skill-token-zzz", k=2)
        ids_zero = _ids_from_select(zero)
        assert any("general-assistant" in str(x) for x in ids_zero), (
            "score==0 must select general-assistant (and emit skill.selection_fallback at runtime)"
        )

        rid = start_run(srv.base_url, goal)
        events = collect_sse(srv.base_url, rid, timeout_s=180.0)
        selected = events_of(events, "skill.selected")
        assert selected, "missing skill.selected"
        sel = event_payload(selected[0])
        skill_ids = sel.get("skill_ids") or []
        assert skill_ids, "skill.selected.skill_ids empty"
        scores = sel.get("scores")
        assert scores is not None, "skill.selected.scores missing"

        # Worker system prompt contains skill-sha256:<hex> of injected body.
        transcript = load_prompts(srv.data_dir, rid)
        sha_hits = re.findall(r"skill-sha256:([0-9a-f]{64})", transcript)
        assert sha_hits, (
            "worker system prompt missing skill-sha256:<hex> "
            "(this is how injection is proven, not the event alone)"
        )
        body_shas = {p["sha256"] for p in parsed}
        assert any(h in body_shas for h in sha_hits), (
            f"skill-sha256 values {sha_hits} do not match any on-disk skill body"
        )
    finally:
        srv.stop()

    # Ablation: copy skills to temp, remove one category, serve with OMNIAGENTOS_SKILLS_ROOT.
    categories = sorted({p["category"] for p in parsed if p["category"] != "_builtin"})
    assert categories, "no non-builtin skill categories to ablate"
    victim = None
    for c in categories:
        if c not in {"_builtin"}:
            victim = c
            break
    assert victim
    tmp = Path(tempfile.mkdtemp(prefix="omniagentos-skills-"))
    dest = tmp / "skills"
    shutil.copytree(root, dest)
    victim_dir = dest / victim
    if victim_dir.is_dir():
        shutil.rmtree(victim_dir)
    # BINDING env: OMNIAGENTOS_SKILLS_ROOT points the loader at an alternate tree.
    srv2 = spawn_serve(extra_env={"OMNIAGENTOS_SKILLS_ROOT": str(dest)})
    try:
        goal2 = f"Use the {victim} playbook. " + goal
        rid2 = start_run(srv2.base_url, goal2)
        ev2 = collect_sse(srv2.base_url, rid2, timeout_s=180.0)
        sel2 = events_of(ev2, "skill.selected")
        assert sel2, "ablation run missing skill.selected"
        ids2 = [str(x) for x in (event_payload(sel2[0]).get("skill_ids") or [])]
        for sid in ids2:
            assert victim not in sid, (
                f"ablation failed: removed category {victim!r} still selected {ids2}"
            )
        write_json(
            "d6-skills.json",
            {
                "parsed": parsed_count,
                "disk": disk_count,
                "api": api_count,
                "ablated": victim,
                "selected_after_ablation": ids2,
            },
        )
    finally:
        srv2.stop()


def _ids_from_select(result) -> list[str]:
    out = []
    if result is None:
        return out
    for item in result:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            out.append(str(item.get("id") or item.get("skill_id") or item.get("slug")))
        elif isinstance(item, (tuple, list)) and item:
            out.append(str(item[0]))
        else:
            out.append(str(getattr(item, "id", item)))
    return out
