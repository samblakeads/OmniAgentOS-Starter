"""How this could pass while broken: pytest -q exit 0 on an empty or fully-skipped suite while a live key exists; now collect-only on tests/dod is >0 and live-key tests must not skip when XAI_API_KEY is present (GitHub CI state is out of this unit)."""

from __future__ import annotations

import subprocess
import sys

from _harness import REPO_ROOT, write_json, xai_key


def test_d08_collect_only_nonzero_and_no_silent_skip():
    py = REPO_ROOT / ".venv" / "bin" / "python"
    exe = str(py) if py.is_file() else sys.executable
    proc = subprocess.run(
        [exe, "-m", "pytest", "--collect-only", "-q", "tests/dod"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    write_json("d8-collect.json", {"code": proc.returncode, "out_tail": out[-4000:]})
    assert proc.returncode == 0, f"pytest --collect-only failed:\n{out[-2000:]}"

    collected = 0
    for line in (proc.stdout or "").splitlines():
        # pytest -q collect-only: "123 tests collected"
        if "test" in line and "collected" in line:
            for tok in line.replace(",", "").split():
                if tok.isdigit():
                    collected = int(tok)
                    break
    if collected == 0:
        # fallback: count collected node lines
        collected = sum(
            1
            for ln in (proc.stdout or "").splitlines()
            if "test_d" in ln and "::" in ln
        )
    assert collected > 0, f"pytest --collect-only collected=0 on tests/dod\n{out[-1500:]}"

    key = xai_key()
    if key:
        # Fail if live-boundary files skip while a key exists.
        dod = REPO_ROOT / "tests" / "dod"
        live_files = [
            "test_d02_live_boundary.py",
            "test_d04_loop_until_done.py",
            "test_d05_self_learning_memory.py",
            "test_d09_demo_goals.py",
        ]
        for name in live_files:
            text = (dod / name).read_text(encoding="utf-8")
            # Unconditional skip / importorskip of the product would hide ImportError.
            assert "pytest.importorskip" not in text, (
                f"{name} uses pytest.importorskip — ImportError must be a FAIL when a key exists"
            )
            assert "@pytest.mark.skip" not in text, f"{name} is unconditionally skipped"
            # require_live() may skip ONLY when the key is unset; with a key it proceeds.
        from _harness import require_live

        got = require_live()
        assert got == key
    else:
        # Without a key, skip is allowed for live tests; collect-only still >0.
        pass
