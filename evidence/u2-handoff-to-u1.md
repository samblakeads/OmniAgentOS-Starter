# U2 → U1 handoff: `pip wheel` cannot ship `skills/` or `assets/` as pyproject.toml stands (B5-F9)

**From:** U2 (scripts/docs owner) · **To:** U1 (`pyproject.toml`, `omniagentos_starter/`)
**Re:** Grok review B5-F9 — package-data does not include `skills/` or `assets/`, and structurally cannot without a layout change.

## Finding, confirmed mechanically

`skills/` and `assets/` live at the **repo root**, outside `omniagentos_starter/`
(the package's own source tree). `[tool.setuptools.package-data]` only bundles
files that already live *inside* the package directory it's declared for — it
cannot reach sibling repo-root directories. So no package-data glob fixes this;
the files have to physically live inside `omniagentos_starter/` to ship in a wheel.

Proof — built a real wheel and inspected its contents:

```
$ .venv/bin/pip wheel . --no-deps -w /tmp/wheel-test
Successfully built omniagentos-starter
$ python3 -m zipfile -l omniagentos_starter-0.1.0-py3-none-any.whl | grep -i "skills\|assets"
omniagentos_starter/skills.py
omniagentos_starter/builtin_skills/general-assistant.md
```

Zero of the 10 sample skill packs and neither asset file (`omnirogue-logo.png`,
`TRADEMARK.md`) are in the wheel — only the package-internal `builtin_skills/`
fallback and `skills.py` itself (matched because "skills" is a substring). A
`pip install .` (non-editable) from that wheel boots with 0 loaded skill packs
(the engine still works — `skills.py::builtin_pack()` covers that — but
`GET /api/skills` reports an empty library) and a missing/broken dashboard logo.

Half of the fix is already anticipated in code but not finished:
`config.py::assets_dir()`'s docstring literally says *"Repo checkout first,
package copy second"* and its fallback chain already includes
`PACKAGE_DIR / "assets"` — but that directory doesn't exist in the package and
pyproject doesn't ship it, so the second half of that fallback never resolves.
`config.py::skills_dir()` has **no** package-copy fallback at all — only
`cwd/skills` then `REPO_ROOT/skills`, both of which are wrong once `REPO_ROOT`
resolves into `site-packages` after a non-editable install.

## Two options — not mine to pick between, since they're your files

**Option A (recommended) — bundle a static copy inside the package**, keeping
`skills/` and `assets/` at repo root as the human-editable source of truth for
git checkouts / editable installs, while non-editable installs get a
committed fallback copy:

1. Mirror `skills/*.md` → `omniagentos_starter/bundled_skills/*.md` and
   `assets/omnirogue-logo.png` + `assets/TRADEMARK.md` →
   `omniagentos_starter/assets/*` (a build step, or a one-time committed copy
   kept in sync — your call on mechanism).
2. Add to `pyproject.toml`:
   ```toml
   [tool.setuptools.package-data]
   omniagentos_starter = [
     "static/*",
     "data/*.json",
     "builtin_skills/*.md",
     "bundled_skills/*.md",
     "assets/*",
   ]
   ```
3. `config.py::skills_dir()` gains a package-copy fallback in its
   `_first_existing(...)` chain, e.g. `PACKAGE_DIR / "bundled_skills"` —
   mirroring what `assets_dir()` already (half-)does.
4. `config.py::assets_dir()`'s existing `PACKAGE_DIR / "assets"` fallback
   starts actually resolving once the files exist there — no change needed
   to that function itself.

**Option B — document the limitation instead of fixing the layout**: state
plainly (README/pyproject) that only `pip install -e .` (editable, from a git
checkout) ships skills/assets, and a wheel install requires
`OMNIAGENTOS_SKILLS_ROOT=/path/to/skills` + `OMNIAGENTOS_ASSETS_DIR=/path/to/assets`
(both env overrides already work today — zero code change for this option).
Weaker "pip install and go" story, zero build-step risk before the webinar.

## What I did NOT touch

`pyproject.toml`, `config.py`, and anything under `omniagentos_starter/` are
untouched by me — that's your ownership. Ping U2 (me) once you've picked A or
B and I'll do the `skills/*.md` → `omniagentos_starter/bundled_skills/*.md`
mirror commit myself (I own `skills/**`).

## What I already fixed in my own files for the rest of B5-F9

`start.sh` / `start.ps1` (both mine):
- Refuse to proceed on Python < 3.11 with a one-line message before creating
  a venv at all (was previously silent until pip's wall of `requires-python`
  text mid-install).
- Skip `pip install -e .` on a second run when the installed package already
  matches the current `pyproject.toml` (SHA-256 stamp written to
  `.venv/.omniagentos-install-stamp` after a successful install; override
  with `OMNIAGENTOS_FORCE_INSTALL=1`) — closes the "second start < 5s"
  DEMO.md claim, which previously hit the network and `pip install` on every
  single launch.
- Header comment's `--host 0.0.0.0` example now names `OMNIAGENTOS_TOKEN` as
  a requirement instead of showing the flag with no warning.

Both verified locally: version-gate refusal tested against a stubbed old-Python
interpreter (exits 1 with a one-line message, no venv/pip touched); install-skip
tested across 4 sequential runs (install → skip → reinstall-on-pyproject-change
→ force-reinstall), all behaving as intended, using an isolated dummy package
(not the shared repo `.venv`, to avoid disturbing concurrent unit work).
