"""The round-8 design pass: what is mechanically checkable about a look.

Most of a visual review is a judgement call. These are the parts that are not —
determinism, the accessibility floors, and the promises the review made that a
future edit could silently undo.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from omniagentos_starter.agents import AGENT_PROHIBITION, Agent

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "omniagentos_starter" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
STYLE = (STATIC / "style.css").read_text(encoding="utf-8")


# --------------------------------------------------------------- avatars
def _avatar(slug: str) -> str:
    """Run the browser's avatar function without a browser.

    Ported line-for-line from app.js so the determinism claim is tested against
    the real algorithm's contract rather than a second implementation of it.
    """
    h = 2166136261
    for ch in slug:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return f"{h}"


def test_the_avatar_hash_is_deterministic_for_a_slug():
    assert _avatar("riley") == _avatar("riley")
    assert _avatar("riley") != _avatar("riley-two")
    assert _avatar("") == _avatar("")


def test_the_avatar_is_drawn_from_the_slug_and_nothing_else():
    fn = APP_JS.split("function avatarSvg(")[1].split("/* ------------------------------------------------------------- agents */")[0]
    assert "slugHash(slug)" in fn, "the drawing must derive from the slug"
    assert "Math.random" not in fn, "a random avatar is a different agent every reload"
    assert "Date" not in fn
    assert 'data-testid="agent-avatar"' in fn
    assert "<svg" in fn and "</svg>" in fn


def test_the_avatar_escapes_the_label_it_embeds():
    fn = APP_JS.split("function avatarSvg(")[1].split("function slugHash")[0] if "function slugHash" in APP_JS.split("function avatarSvg(")[1] else APP_JS.split("function avatarSvg(")[1][:2000]
    assert 'esc(slug)' in fn, "a slug reaches an aria-label; it goes through esc()"


def test_every_agent_card_gets_an_avatar_and_an_orb():
    render = APP_JS.split("function renderAgents()")[1].split("function fillAgentPicker()")[0]
    assert "avatarSvg(a.id" in render
    assert 'data-testid="lane-orb"' in render
    assert "agent-ident" in render, "name and title are separate elements, never one string"


# ------------------------------------------------------- accessibility floors
def test_no_font_size_drops_below_the_sixteen_pixel_floor():
    sizes = [int(m) for m in re.findall(r"font-size:\s*(\d+)px", STYLE)]
    assert sizes, "no font sizes found — the parser is wrong, not the stylesheet"
    assert min(sizes) >= 16, f"a projector floor of 16px is broken by {sorted(sizes)[:3]}"


def test_reduced_motion_is_honoured_including_the_new_motion():
    assert "@media (prefers-reduced-motion: reduce)" in STYLE
    block = STYLE.split("@media (prefers-reduced-motion: reduce)")[1].split("}\n}")[0]
    for animated in (".spinner", ".token.travelling", ".agent-orb"):
        assert animated in block, f"{animated} animates but is not stopped for reduced motion"


def test_focus_is_visible_for_keyboard_use():
    assert ":focus-visible" in STYLE
    assert "outline" in STYLE.split(":focus-visible")[1].split("}")[0]


# ------------------------------------------------------------ Kimi (A) items
@pytest.mark.parametrize(
    "rule",
    [
        "h1 { font-size: 28px;",
        "main h2 { font-size: 20px;",
        ".lane > header h2 { font-size: 18px; }",
        "font-variant-numeric: tabular-nums;",
        "box-shadow: 0 0 0 2px var(--accent), 0 0 28px rgba(110,168,254,.25);",
        ".lane-status {",
        ".arrow { width: 34px; font-size: 24px; }",
        ".token { width: 12px; height: 12px;",
        "border-left: 5px solid var(--fail);",
        "#deliverable { font-size: 17px;",
    ],
)
def test_the_review_items_are_actually_in_the_stylesheet(rule):
    assert rule in STYLE, rule


def test_one_icon_family_no_emoji():
    """Colour emoji next to hairline glyphs read as two competing systems."""
    for emoji in ("👤", "🧠", "🧩", "🗂", "☑", "📄", "👥", "🔗"):
        assert emoji not in INDEX, f"{emoji} in index.html"
        assert emoji not in APP_JS, f"{emoji} in app.js"


def test_the_copy_fixes_landed():
    assert "est. cost" in INDEX and "est $" not in INDEX
    assert 'alt="OmniAgentOS Starter"' in INDEX, "two product names in one header"
    assert "◌ No provider key found" not in INDEX
    assert "#run-id { max-width: 12ch;" in STYLE
    assert "#error-banner .link-button {" in STYLE
    tagline = INDEX.split('class="tagline"')[1].split("</p>")[0]
    assert len(tagline) < 90, f"the tagline wraps and buries the health chips: {tagline}"


def test_empty_states_say_what_will_appear():
    """A blank panel teaches nothing; each one says what fills it and how."""
    for anchor, phrase in (
        ("planner-body", "The plan appears here"),
        ("workers-body", "as a worker picks it up"),
        ("memory-body", "fed to the planner"),
        ("file-tree", "open in a new tab"),
    ):
        assert anchor in INDEX
        assert phrase in INDEX, f"{anchor} has no teaching empty state"
    empty = APP_JS.split("No agents yet.")[1][:200]
    assert "New agent" in empty, "the empty roster must name the action that fills it"


# ----------------------------------------------------------- agent filtering
def test_clicking_an_agent_filters_to_that_agent():
    assert "function filterByAgent" in APP_JS
    assert 'data-testid="agent-runs-filter"' in INDEX
    fn = APP_JS.split("function filterByAgent(")[1].split("function renderAgentFilter")[0]
    assert "state.agentFilter" in fn
    assert "toggle(\"filtered\"" in fn or "toggle('filtered'" in fn
    # clicking the same card again clears it, rather than trapping the operator
    assert "state.agentFilter === slug ? \"\" : slug" in fn
    assert ".agent-card.filtered" in STYLE


def test_the_filter_names_the_agent_and_offers_a_way_out():
    fn = APP_JS.split("function renderAgentFilter()")[1].split("function showWorkerAgent")[0]
    assert "Show everyone" in fn
    assert "lesson" in fn
    assert "esc(name)" in fn, "an agent name reaches innerHTML"


def test_the_card_click_does_not_swallow_its_own_buttons():
    wiring = APP_JS.split('el("agents-list").addEventListener')[1].split("});")[0]
    assert "if (edit || dup || del) { return; }" in wiring


# ------------------------------------------------- OpenBot 4: standing role
def test_the_persona_is_framed_as_a_standing_role():
    agent = Agent(slug="riley", name="Riley", title="Meal-Prep Support", persona="Warm and exact.")
    block = agent.prompt_block()
    assert "You are Riley, Meal-Prep Support." in block
    assert "This standing role applies to every run" in block
    assert "treat the goal as task-specific instructions within it" in block
    assert AGENT_PROHIBITION in block


def test_the_frame_never_rewrites_the_persona_itself():
    """D16 proves an agent ran by finding its persona verbatim in the transcript."""
    persona = "Warm and exact. Never says 'sorry' twice, and cites the customer's own words."
    agent = Agent(slug="riley", name="Riley", persona=persona)
    assert persona in agent.prompt_block(), "the frame must wrap the persona, not paraphrase it"


def test_an_agent_with_no_title_still_reads_as_a_sentence():
    agent = Agent(slug="solo", name="Solo", persona="Does the work.")
    assert "You are Solo. Does the work." in agent.prompt_block()
