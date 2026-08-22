"""Workspace tools — the only way an agent touches the filesystem.

There is no shell tool, no exec, no subprocess: the allow-list is exactly
read_file / write_file / list_files, and every path goes through
:class:`WorkspaceGuard`, which fails closed. A guard whose root is the repo root,
the package directory or the data directory refuses to exist at all.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .config import PACKAGE_DIR, REPO_ROOT
from .redact import WorkspaceEscape

MAX_FILE_BYTES = 256 * 1024
MAX_FILES_PER_RUN = 50
MAX_REL_LEN = 255
MAX_DEPTH = 8


class WorkspaceRefused(Exception):
    """The requested workspace root is not a safe dedicated directory."""


def _forbidden_roots(data_dir: Path | None) -> list[Path]:
    roots = [
        Path(REPO_ROOT).resolve(),
        Path(PACKAGE_DIR).resolve(),
        Path.cwd().resolve(),
        Path.home().resolve(),
        Path(Path.cwd().anchor or "/").resolve(),
    ]
    if data_dir:
        roots.append(Path(data_dir).resolve())
    return roots


class WorkspaceGuard:
    """Confines every file operation to one dedicated directory.

    Rejected, each with error_tag WORKSPACE_ESCAPE: absolute paths, drive- and
    UNC-prefixed paths, ``..`` traversal, null bytes, symlinked components, and
    prefix collisions (``/ws_extra`` never counts as inside ``/ws``) — the
    containment test is :meth:`Path.is_relative_to` on fully resolved paths, not
    a string prefix.
    """

    def __init__(self, root: Path | str, data_dir: Path | str | None = None, create: bool = True):
        root = Path(root).expanduser()
        if root.is_symlink():
            raise WorkspaceRefused(f"workspace root is a symlink: {root}")
        resolved = root.resolve()
        forbidden = _forbidden_roots(Path(data_dir) if data_dir else None)
        for bad in forbidden:
            if resolved == bad:
                raise WorkspaceRefused(f"refusing to use {resolved} as a workspace root")
            if bad.is_relative_to(resolved):
                raise WorkspaceRefused(
                    f"refusing workspace root {resolved}: it contains {bad}"
                )
        if create:
            resolved.mkdir(parents=True, exist_ok=True)
        self.root = resolved

    # ------------------------------------------------------------- resolving
    def resolve(self, rel: str) -> Path:
        if not isinstance(rel, str):
            raise WorkspaceEscape(str(rel), "path must be a string")
        if "\x00" in rel:
            raise WorkspaceEscape(rel.replace("\x00", "?"), "null byte in path")
        candidate = rel.strip()
        if not candidate:
            raise WorkspaceEscape(rel, "empty path")
        if len(candidate) > MAX_REL_LEN:
            raise WorkspaceEscape(candidate[:80], "path too long")
        if candidate.startswith(("/", "\\", "~")) or candidate.startswith("\\\\"):
            raise WorkspaceEscape(candidate, "absolute path")
        if len(candidate) > 1 and candidate[1] == ":":
            raise WorkspaceEscape(candidate, "absolute path")
        normalised = candidate.replace("\\", "/")
        parts = [p for p in normalised.split("/") if p not in ("", ".")]
        if not parts:
            raise WorkspaceEscape(candidate, "empty path")
        if len(parts) > MAX_DEPTH:
            raise WorkspaceEscape(candidate, "path too deep")
        if any(p == ".." for p in parts):
            raise WorkspaceEscape(candidate, "parent traversal")

        cur = self.root
        for part in parts:
            cur = cur / part
            if cur.is_symlink():
                raise WorkspaceEscape(candidate, "symlinked path component")
        target = (self.root / Path(*parts)).resolve()
        if target != self.root and not target.is_relative_to(self.root):
            raise WorkspaceEscape(candidate, "resolves outside the workspace")
        return target

    # ----------------------------------------------------------------- tools
    def write_file(self, rel: str, content: str) -> dict:
        path = self.resolve(rel)
        data = (content or "").encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise WorkspaceEscape(rel, f"file exceeds {MAX_FILE_BYTES} bytes")
        if len(self.list_files()) >= MAX_FILES_PER_RUN and not path.exists():
            raise WorkspaceEscape(rel, f"more than {MAX_FILES_PER_RUN} files in one run")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {"path": self.relpath(path), "bytes": len(data)}

    def read_file(self, rel: str) -> str:
        path = self.resolve(rel)
        if not path.is_file():
            raise FileNotFoundError(rel)
        return path.read_text(encoding="utf-8", errors="replace")[:MAX_FILE_BYTES]

    def list_files(self) -> list[dict]:
        out: list[dict] = []
        for p in sorted(self.root.rglob("*")):
            if p.is_symlink() or not p.is_file():
                continue
            try:
                out.append({"path": self.relpath(p), "bytes": p.stat().st_size})
            except OSError:
                continue
        return out

    def relpath(self, path: Path) -> str:
        return str(Path(path).resolve().relative_to(self.root)).replace(os.sep, "/")


@dataclass
class ToolResult:
    ok: bool
    tool: str
    detail: dict


TOOL_NAMES = ("read_file", "write_file", "list_files")


def workspace_for_run(workspace_dir: Path | str, run_id: str, data_dir: Path | str | None = None) -> WorkspaceGuard:
    """Default per-run root: ``./workspace/runs/<run_id>/``."""
    safe_id = "".join(ch for ch in str(run_id) if ch.isalnum() or ch in "-_")[:64]
    if not safe_id:
        raise WorkspaceRefused("run id has no safe characters")
    return WorkspaceGuard(Path(workspace_dir) / "runs" / safe_id, data_dir=data_dir)
