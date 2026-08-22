"""Build hook: carry `skills/` and `assets/` into the wheel.

Everything else about this project is configured in pyproject.toml. This file
exists for one reason: setuptools' `package-data` can only reach files that live
*inside* the package directory, and the two directories a user is most likely to
edit — the skill library and the brand assets — deliberately live at the repo
root, where they are obvious and editable.

Duplicating them into `omniagentos_starter/` in git would create a second source
of truth that silently drifts from the one you are editing. Copying them at BUILD
time does not: the checkout keeps exactly one copy, and the wheel gets a snapshot.

The result is that both installs work:

* `pip install -e .` (and `./start.sh`) — `skills_dir()` finds the repo-root copy
  first, so the packs you edit are the packs that load.
* `pip install omniagentos-starter` from a wheel — no repo root exists, so
  `skills_dir()` / `assets_dir()` fall back to the packaged snapshot, and the
  ten sample packs and the logo are there.

Without this, a wheel install had zero skills and a 404 for the header logo.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py

HERE = Path(__file__).resolve().parent
PACKAGE = "omniagentos_starter"
# (source at the repo root, destination inside the built package)
BUNDLED_TREES = (("skills", "skills"), ("assets", "assets"))


class build_py(_build_py):
    """Copy the repo-root trees into the build directory before packaging."""

    def run(self) -> None:
        super().run()
        if self.editable_mode:
            # An editable install reads straight from the checkout; copying would
            # be the drift this file exists to avoid.
            return
        for source_name, dest_name in BUNDLED_TREES:
            source = HERE / source_name
            if not source.is_dir():
                continue
            dest = Path(self.build_lib) / PACKAGE / dest_name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(
                source,
                dest,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
                symlinks=False,
            )


setup(cmdclass={"build_py": build_py})
