"""Regression tests for the proof scripts and the packaging (Grok B5 + wheel gap).

These are the tools that decide whether the demo "worked". Every finding here is
the same failure mode: the tool reports success it did not measure — a receipt
with a live token in it, a timing of 0 that means "nothing ever arrived", a
recording that names a provider it never spoke to, a wheel with no skills in it.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import drill  # noqa: E402
import record_replay  # noqa: E402

from omniagentos_starter.api import sse_data  # noqa: E402
from omniagentos_starter.config import PACKAGE_DIR, assets_dir, skills_dir  # noqa: E402


# -------------------------------------------------------------- B5-F4 (REQUIRED)
def test_a_receipt_never_carries_a_token_supplied_on_the_command_line():
    argv = ["scripts/drill.py", "--token", "stage-demo-token-ABCDEFGH123456", "--goal", "x"]
    scrubbed = drill.scrub_argv(argv)
    assert "stage-demo-token-ABCDEFGH123456" not in " ".join(scrubbed)
    assert scrubbed[:2] == ["scripts/drill.py", "--token"]
    assert drill.scrub_argv(["d.py", "--token=abc123"]) == ["d.py", "--token=[REDACTED]"]


def test_a_receipt_never_carries_an_absolute_local_path():
    receipt = {
        "argv": ["/Users/someone/.venv/bin/python", "scripts/drill.py"],
        "nested": {"out": "/home/ci/work/evidence/drill.json"},
        "windows": r"C:\Users\someone\evidence.json",
    }
    scrubbed = drill.scrub_paths(receipt)
    blob = repr(scrubbed)
    assert "/Users/someone" not in blob
    assert "/home/ci" not in blob
    assert r"C:\Users" not in blob


# -------------------------------------------------------------- B5-F5 (REQUIRED)
def test_a_missing_timing_stays_null_rather_than_becoming_zero(tmp_path, capsys):
    out = tmp_path / "receipt.json"
    receipt = {"magic": drill.RECEIPT_MAGIC, "argv": ["d.py"], "status": "failed"}
    code = drill.finish(receipt, str(out), 2)
    assert code == 2
    written = out.read_text(encoding="utf-8")
    assert '"t_first_event_ms": null' in written
    assert '"t_done_ms": null' in written


def test_the_drill_timeout_is_documented_as_wall_clock():
    args = drill.parse_args(["--goal", "x", "--out", "/dev/null"])
    assert args.timeout == 300.0
    help_text = drill.parse_args.__doc__ or ""
    assert help_text is not None  # the flag's own help carries the wording
    import argparse
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), pytest.raises(SystemExit):
        drill.parse_args(["--help"])
    assert "WALL-CLOCK" in buf.getvalue()
    assert isinstance(args, argparse.Namespace)


# -------------------------------------------------------------- B5-F3 (REQUIRED)
def test_record_replay_reads_the_sequence_number_the_wire_actually_carries():
    wire = sse_data({"id": 7, "run_id": "r1", "ts": 1.0, "type": "run.started", "payload": {"goal": "g"}})
    assert "id" not in wire, "the wire has never carried a top-level `id`"
    assert wire["event_id"] == 7
    assert record_replay.sequence_of(wire) == 7


def test_record_replay_refuses_to_guess_a_missing_sequence_number():
    with pytest.raises(SystemExit):
        record_replay.sequence_of({"type": "run.started", "payload": {}})


# -------------------------------------------------------------- B5-F7 (REQUIRED)
def test_a_stored_recording_never_invents_the_provider_it_spoke_to(tmp_path):
    from omniagentos_starter.memory import Memory

    mem = Memory(tmp_path / "var")
    mem.create_run("r1", "g")
    mem.append_event("r1", {"id": 1, "ts": 1.0, "type": "run.started", "payload": {"goal": "g"}})
    mem.append_event("r1", {"id": 2, "ts": 1.1, "type": "run.done", "payload": {"deliverable": "d"}})
    mem.close()

    args = record_replay.parse_args(["--from-run", "r1", "--data-dir", str(tmp_path / "var")])
    with pytest.raises(SystemExit) as exc:
        record_replay.capture_stored(args)
    assert "provider" in str(exc.value)

    # ...but it will read the truth off the run's own llm.call events.
    mem = Memory(tmp_path / "var")
    mem.append_event(
        "r1",
        {"id": 3, "ts": 1.2, "type": "llm.call", "payload": {"provider": "openai", "model": "gpt-4.1-mini"}},
    )
    mem.close()
    _events, _run_id, health = record_replay.capture_stored(args)
    assert health["provider"] == "openai"
    assert health["model"] == "gpt-4.1-mini"


# ---------------------------------------------------- packaging (U2 handoff gap)
def test_the_wheel_carries_the_skills_and_the_assets():
    """package-data must reach the trees setup.py snapshots into the build."""
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = config["tool"]["setuptools"]["package-data"]["omniagentos_starter"]
    for needed in ("skills/*/*.md", "assets/*", "static/*", "data/*.json", "builtin_skills/*.md"):
        assert needed in patterns, f"package-data is missing {needed}"
    # The build hook itself, without which the patterns above match nothing.
    setup_py = (REPO_ROOT / "setup.py").read_text(encoding="utf-8")
    assert "build_py" in setup_py and "skills" in setup_py and "assets" in setup_py


def test_the_directory_lookups_fall_back_to_the_packaged_copies(monkeypatch, tmp_path):
    """A wheel install has no repo root, so the package copy has to be the floor."""
    import omniagentos_starter.config as config

    monkeypatch.setattr(config, "REPO_ROOT", tmp_path / "nowhere")
    monkeypatch.chdir(tmp_path)
    assert config.skills_dir() == PACKAGE_DIR / "skills"
    assert config.assets_dir() == PACKAGE_DIR / "assets"


def test_a_checkout_still_wins_over_the_packaged_snapshot():
    """The copy you edit is the copy that loads — that is why they live at the root."""
    assert skills_dir() == REPO_ROOT / "skills"
    assert assets_dir() == REPO_ROOT / "assets"


def _bundled_trees() -> list[tuple[str, str]]:
    """Read setup.py's BUNDLED_TREES without importing it.

    setup.py imports setuptools at module scope, and setuptools is a BUILD
    dependency — pip builds this project in an isolated environment, so it is not
    importable from the test venv. Reading the constant with `ast` binds the test
    to the same literal the hook uses, with nothing to install.
    """
    import ast

    tree = ast.parse((REPO_ROOT / "setup.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", "") == "BUNDLED_TREES" for t in node.targets
        ):
            return [tuple(pair) for pair in ast.literal_eval(node.value)]
    raise AssertionError("setup.py no longer defines BUNDLED_TREES")


def test_the_trees_the_build_hook_snapshots_exist_and_land_where_package_data_looks(tmp_path):
    """The packaging contract, end to end, with no wheel and no network.

    This replaces a test that skipped unless a wheel happened to be sitting in
    dist/ — which is to say, one that ran approximately never. It does not invoke
    setuptools (a build-isolated dependency); it asserts the three things that
    have to line up for `pip install` from a wheel to work: the source trees
    exist, copying them the way the hook copies them produces the package layout,
    and pyproject's package-data globs match that layout.
    """
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = config["tool"]["setuptools"]["package-data"]["omniagentos_starter"]
    build_lib = tmp_path / "build" / "omniagentos_starter"
    build_lib.mkdir(parents=True)

    trees = _bundled_trees()
    assert {name for name, _ in trees} == {"skills", "assets", "agents"}, trees
    for source_name, dest_name in trees:
        source = REPO_ROOT / source_name
        if not source.is_dir():
            # The hook skips a tree that is not in the checkout, so the test does
            # too — but only for trees that are genuinely optional. The two below
            # are asserted unconditionally.
            assert source_name == "agents", f"{source_name}/ is missing from the checkout"
            continue
        shutil.copytree(
            source,
            build_lib / dest_name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
            symlinks=False,
        )

    packs = sorted(build_lib.glob("skills/*/*.md"))
    assert len(packs) >= 10, f"a wheel would ship {len(packs)} skill packs: {packs}"
    assert (build_lib / "assets" / "omnirogue-logo.png").is_file(), "a wheel would ship no logo"
    assert not any(p.is_symlink() for p in build_lib.rglob("*")), "a snapshot must not contain symlinks"

    if (REPO_ROOT / "agents").is_dir():
        assert sorted(build_lib.glob("agents/*.md")), "the agent roster would not ship in a wheel"

    # ...and every file above is actually matched by a package-data glob.
    for relative in (
        "skills/marketing-content/ad-copy-framework-writer.md",
        "assets/omnirogue-logo.png",
        "agents/support-rep.md",
    ):
        assert any(pathlib.PurePath(relative).match(pat) for pat in patterns), (
            f"{relative} is snapshotted into the package but no package-data glob matches it"
        )


# ===================================================================== Q3-R2
# The auditor could not see scripts/drill.py and so could not tell whether the
# --agent value, the agent_id and the agent object were redacted. These are the
# tests that answer it. Named for findability: Q3-R2 is about redaction, Q3-R3
# about the structured agent.assigned check.


def test_q3_r2_a_receipt_never_carries_a_key_shaped_agent_value(tmp_path, monkeypatch):
    """Plant a key as --agent and prove it cannot reach the file.

    Nobody sensibly passes a provider key to --agent, which is exactly why it is
    worth testing: the receipt is a published artifact and the gate has to hold
    for values nobody anticipated, not only for the ones named in an env var.
    """
    from omniagentos_starter.redact import clear_registered_secrets, register_secret

    clear_registered_secrets()
    planted = "xai-RECEIPTKEY-ABCDEFGHIJKLMNOP"
    monkeypatch.setenv("XAI_API_KEY", planted)
    register_secret(planted)

    out = tmp_path / "receipt.json"
    code = drill.main(
        [
            "--in-process",
            "--agent", planted,
            "--goal", "hello",
            "--out", str(out),
            "--data-dir", str(tmp_path / "var"),
        ]
    )
    assert code != 0, "an agent that does not exist must never exit 0"
    assert out.is_file(), "a refusal still writes a receipt — a missing file reads as 'never ran'"
    written = out.read_text(encoding="utf-8")
    assert planted not in written, "the planted key reached the receipt"
    assert "RECEIPTKEY" not in written
    receipt = json.loads(written)
    assert receipt["ok"] is False
    # It is redacted, not merely absent — the field is still there, so a reader
    # can see that an agent WAS requested.
    assert "[REDACTED]" in json.dumps(receipt["argv"]) or "[REDACTED]" in json.dumps(receipt)


def test_q3_r2_the_agent_id_and_agent_object_go_through_redact(tmp_path, monkeypatch):
    """The whole receipt is redacted on the way out, agent fields included."""
    from omniagentos_starter.redact import clear_registered_secrets, register_secret

    clear_registered_secrets()
    planted = "xai-AGENTFIELD-ABCDEFGHIJKL"
    monkeypatch.setenv("XAI_API_KEY", planted)
    register_secret(planted)

    out = tmp_path / "receipt.json"
    receipt = {
        "magic": drill.RECEIPT_MAGIC,
        "argv": ["drill.py", "--agent", planted],
        "agent_id": planted,
        "agent": {"id": planted, "name": planted},
        "status": "failed",
    }
    code = drill.finish(receipt, str(out), 1)
    # finish() refuses outright if a secret survives; either way it must not land.
    if code == 3:
        assert not out.is_file() or planted not in out.read_text(encoding="utf-8")
        return
    written = out.read_text(encoding="utf-8")
    assert planted not in written
    assert "AGENTFIELD" not in written


def test_q3_r2_a_persona_never_travels_in_a_receipt():
    """An operator wrote the persona; a receipt is published. Only {id,name}."""
    narrowed = drill._agent_summary(
        {
            "id": "riley",
            "slug": "riley",
            "name": "Riley",
            "persona": "PERSONA SHOULD NOT TRAVEL",
            "body": "BODY SHOULD NOT TRAVEL",
            "tools": ["read_file"],
        }
    )
    assert narrowed == {"id": "riley", "name": "Riley"}
    assert "SHOULD NOT TRAVEL" not in json.dumps(narrowed)


# ===================================================================== Q3-R3
def test_q3_r3_the_assigned_check_is_structured_not_a_string_match():
    """It reads agent_id off the event, both shapes, and canonicalises slugs."""
    source = (REPO_ROOT / "scripts" / "drill.py").read_text(encoding="utf-8")
    assert 'e.get("type") == "agent.assigned"' in source, "must select the event by type"
    assert 'first.get("agent_id")' in source, "flattened shape"
    assert '(first.get("payload") or {}).get("agent_id")' in source, "nested shape"
    assert "safe_agent_slug(named) != wanted" in source, "compare canonical slugs, not raw strings"
    assert drill.safe_agent_slug("Riley") == "riley"


def test_q3_r3_a_run_that_ignored_the_agent_is_a_problem_not_a_pass(tmp_path):
    """A verified run that silently fell through to the router must still fail."""
    source = (REPO_ROOT / "scripts" / "drill.py").read_text(encoding="utf-8")
    # the CHECKS block, not the request-building one earlier in the file
    block = source.rsplit("if args.agent:", 1)[1].split("if not bool(summary.get(")[0]
    assert "fell through to the router" in block
    assert "problems.append" in block
    # and the mismatch branch is separate from the missing branch
    assert block.count("problems.append") >= 2
