"""Skill packs: directory scan, integrity, deterministic selection, fallback."""

from __future__ import annotations

from omniagentos_starter.skills import builtin_pack, load_skills, parse_pack

PACK = """---
name: Ad Copy Writer
slug: ad-copy-writer
category: marketing-content
summary: Writes short direct-response ad headlines and body copy.
---

## WHEN TO USE
When the goal asks for ad headlines, hooks or ad copy.

## INPUTS
- product name

## WORKFLOW
1. Draft
2. Cut

## OUTPUT SPEC
A numbered list.

## QUALITY CHECKS
- Every headline is under the stated character limit.
- No headline repeats another's angle.
"""


def write_pack(root, category, slug, text=PACK):
    d = root / category
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{slug}.md"
    p.write_text(text.replace("ad-copy-writer", slug).replace("marketing-content", category), encoding="utf-8")
    return p


def test_a_pack_parses_front_matter_sections_and_checks(tmp_path):
    pack = parse_pack(write_pack(tmp_path, "marketing-content", "ad-copy-writer"))
    assert pack.slug == "ad-copy-writer"
    assert pack.category == "marketing-content"
    assert pack.quality_checks == [
        "Every headline is under the stated character limit.",
        "No headline repeats another's angle.",
    ]
    assert len(pack.sha256) == 64


def test_the_loader_is_a_directory_scan_with_integrity(tmp_path):
    write_pack(tmp_path, "marketing-content", "ad-copy-writer")
    write_pack(tmp_path, "sales", "proposal-writer")
    (tmp_path / "README.md").write_text("not a pack", encoding="utf-8")
    lib = load_skills(tmp_path)
    assert lib.count == 2
    assert lib.integrity() == {
        "ok": True,
        "parsed": 2,
        "files_on_disk": 2,
        "duplicate_slugs": [],
        "errors": [],
    }
    assert lib.categories() == ["marketing-content", "sales"]


def test_an_empty_pack_is_reported_not_silently_dropped(tmp_path):
    write_pack(tmp_path, "sales", "good")
    (tmp_path / "sales" / "empty.md").write_text("", encoding="utf-8")
    lib = load_skills(tmp_path)
    assert lib.count == 1
    assert lib.integrity()["ok"] is False
    assert any("empty" in e for e in lib.integrity()["errors"])


def test_a_missing_skills_directory_is_an_empty_library_not_a_crash(tmp_path):
    lib = load_skills(tmp_path / "nope")
    assert lib.count == 0
    assert lib.integrity()["ok"] is True


def test_selection_is_deterministic_and_scores_the_right_pack(tmp_path):
    write_pack(tmp_path, "marketing-content", "ad-copy-writer")
    write_pack(tmp_path, "finance-reporting", "expense-categorizer")
    lib = load_skills(tmp_path)
    packs, scores, fallback = lib.select("Write 3 ad headlines with a character limit", k=2)
    assert fallback is False
    assert packs[0].slug == "ad-copy-writer"
    assert scores[0]["score"] > 0
    assert lib.select("Write 3 ad headlines with a character limit", k=2)[1] == scores


def test_removing_a_category_makes_its_pack_unselectable(tmp_path):
    path = write_pack(tmp_path, "marketing-content", "ad-copy-writer")
    lib = load_skills(tmp_path)
    assert lib.select("ad headlines please")[0][0].slug == "ad-copy-writer"
    path.unlink()
    lib2 = load_skills(tmp_path)
    assert lib2.count == 0
    assert lib2.select("ad headlines please")[2] is True


def test_no_match_falls_back_to_the_builtin_pack_that_ships_in_the_package(tmp_path):
    write_pack(tmp_path, "finance-reporting", "expense-categorizer")
    lib = load_skills(tmp_path)
    packs, scores, fallback = lib.select("zzzz qqqq wwww")
    assert fallback is True
    assert packs[0].slug == "general-assistant"
    assert packs[0].builtin is True
    assert scores == [{"skill_id": "general-assistant", "score": 0.0}]


def test_the_builtin_pack_is_a_valid_pack_with_quality_checks():
    pack = builtin_pack()
    assert pack.quality_checks
    assert "skill-sha256:" in pack.prompt_block()
    assert pack.category == "general"


def test_the_prompt_block_carries_the_body_hash():
    pack = builtin_pack()
    assert f"skill-sha256:{pack.sha256}" in pack.prompt_block()
