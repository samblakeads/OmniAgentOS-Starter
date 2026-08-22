"""Skill packs: directory scan, integrity, deterministic selection, fallback."""

from __future__ import annotations

from omniagentos_starter.skills import builtin_pack, load_skills, parse_pack

PACK = """---
name: Ad Copy Writer
slug: ad-copy-writer
category: marketing-content
summary: Writes short direct-response ad headlines and body copy for paid campaigns.
---

## WHEN TO USE
When the goal asks for ad headlines, hooks or ad copy.

## INPUTS
- product name

## WORKFLOW
1. Draft
2. Cut

## OUTPUT SPEC
A numbered list of headline variants.

## QUALITY CHECKS
- Every headline is under the stated character limit.
- No headline repeats another's angle.
"""

# A second pack that shares no vocabulary with the first: a library of clones
# would tell us nothing about routing.
OTHER_PACK = """---
name: Expense Categorizer
slug: expense-categorizer
category: finance-reporting
summary: Sorts bank transactions into ledger categories for monthly bookkeeping.
---

## WHEN TO USE
When the goal asks to categorise expenses, invoices or bank transactions.

## INPUTS
- transaction export

## WORKFLOW
1. Group
2. Reconcile

## OUTPUT SPEC
A table of transactions with a ledger category each.

## QUALITY CHECKS
- Every transaction receives exactly one ledger category.
- Uncategorised rows are listed separately for review.
"""


def write_pack(root, category, slug, text=None):
    text = text if text is not None else (OTHER_PACK if "expense" in slug or "finance" in category else PACK)
    d = root / category
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{slug}.md"
    body = text.replace("ad-copy-writer", slug).replace("expense-categorizer", slug)
    body = body.replace("marketing-content", category).replace("finance-reporting", category)
    p.write_text(body, encoding="utf-8")
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


def test_best_match_uses_the_same_floor_as_select_and_keeps_a_below_floor_score(tmp_path):
    """Per-task routing needs the losing score, not a silent zeroed generalist."""
    write_pack(tmp_path, "marketing-content", "ad-copy-writer")
    write_pack(tmp_path, "finance-reporting", "expense-categorizer")
    lib = load_skills(tmp_path)
    pack, score, below = lib.best_match("Write 3 ad headlines with a character limit")
    assert pack is not None and pack.slug == "ad-copy-writer"
    assert below is False and score > 0
    _, select_scores, fallback = lib.select("Write 3 ad headlines with a character limit", k=1)
    assert fallback is False
    assert select_scores[0]["score"] == score

    miss, miss_score, miss_below = lib.best_match("zzzz qqqq wwww")
    assert miss_below is True
    assert miss_score == 0.0
    # empty shelf: allowed set that names nobody
    empty, empty_score, empty_below = lib.best_match("Write 3 ad headlines", allowed=set())
    assert empty is None and empty_score == 0.0 and empty_below is True


def test_removing_a_category_makes_its_pack_unselectable(tmp_path):
    goal = "Write ad headlines and body copy with a character limit for a paid campaign"
    path = write_pack(tmp_path, "marketing-content", "ad-copy-writer")
    lib = load_skills(tmp_path)
    assert lib.select(goal)[0][0].slug == "ad-copy-writer"
    path.unlink()
    lib2 = load_skills(tmp_path)
    assert lib2.count == 0
    assert lib2.select(goal)[2] is True


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


def test_a_single_incidental_token_is_not_a_match(tmp_path):
    """A pack must clear a floor, not merely score above zero.

    Regression from a live run: a cold-email pack matched a goal about agent
    orchestration on one shared word, and its "exactly one CTA per email body"
    check then became a criterion the deliverable could never satisfy.
    """
    write_pack(tmp_path, "marketing-content", "ad-copy-writer")
    (tmp_path / "lead-generation").mkdir(parents=True, exist_ok=True)
    (tmp_path / "lead-generation" / "cold-email.md").write_text(
        "---\nname: Cold Email Sequence\nslug: cold-email\ncategory: lead-generation\n"
        "summary: writes multi-step cold outreach email sequences for headlines lists\n---\n\n"
        "## WHEN TO USE\ncold outreach\n\n## QUALITY CHECKS\n- Exactly one CTA per email body.\n",
        encoding="utf-8",
    )
    lib = load_skills(tmp_path)
    packs, scores, fallback = lib.select("Write 3 ad headlines with a character limit for a marketing content push")
    assert [p.slug for p in packs] == ["ad-copy-writer"]
    assert all(s["skill_id"] != "cold-email" for s in scores)
    assert fallback is False
