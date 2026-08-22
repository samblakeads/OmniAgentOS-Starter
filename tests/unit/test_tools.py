"""WorkspaceGuard — red-first escape attempts. Every one must be refused."""

from __future__ import annotations

import pytest

from omniagentos_starter.config import PACKAGE_DIR, REPO_ROOT
from omniagentos_starter.redact import WorkspaceEscape
from omniagentos_starter.tools import WorkspaceGuard, WorkspaceRefused, workspace_for_run


@pytest.fixture
def guard(tmp_path):
    return WorkspaceGuard(tmp_path / "ws", data_dir=tmp_path / "var")


def test_a_normal_relative_write_is_allowed(guard):
    result = guard.write_file("notes/one.md", "hello")
    assert result["path"] == "notes/one.md"
    assert guard.read_file("notes/one.md") == "hello"
    assert [f["path"] for f in guard.list_files()] == ["notes/one.md"]


def test_absolute_path_is_refused(guard):
    with pytest.raises(WorkspaceEscape) as exc:
        guard.write_file("/etc/passwd", "x")
    assert exc.value.error_tag == "WORKSPACE_ESCAPE"
    assert exc.value.reason == "absolute path"


def test_windows_drive_and_unc_paths_are_refused(guard):
    for bad in ("C:\\Windows\\system32\\x.txt", "\\\\server\\share\\x.txt"):
        with pytest.raises(WorkspaceEscape):
            guard.write_file(bad, "x")


def test_home_expansion_is_refused(guard):
    with pytest.raises(WorkspaceEscape):
        guard.write_file("~/secrets.txt", "x")


def test_parent_traversal_is_refused(guard):
    for bad in ("../escape.txt", "a/../../escape.txt", "..\\escape.txt"):
        with pytest.raises(WorkspaceEscape) as exc:
            guard.write_file(bad, "x")
        assert exc.value.error_tag == "WORKSPACE_ESCAPE"


def test_null_byte_is_refused(guard):
    with pytest.raises(WorkspaceEscape) as exc:
        guard.write_file("ok.txt\x00/../../etc/passwd", "x")
    assert exc.value.reason == "null byte in path"


def test_symlinked_component_is_refused(tmp_path, guard):
    outside = tmp_path / "outside"
    outside.mkdir()
    (guard.root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(WorkspaceEscape) as exc:
        guard.write_file("link/pwned.txt", "x")
    assert exc.value.reason == "symlinked path component"
    assert not (outside / "pwned.txt").exists()


def test_symlinked_file_is_refused_on_read(tmp_path, guard):
    secret = tmp_path / "secret.txt"
    secret.write_text("classified")
    (guard.root / "peek.txt").symlink_to(secret)
    with pytest.raises(WorkspaceEscape):
        guard.read_file("peek.txt")


def test_prefix_collision_is_not_containment(tmp_path):
    """/ws_extra must never count as inside /ws — string prefixes are not paths."""
    (tmp_path / "ws_extra").mkdir()
    guard = WorkspaceGuard(tmp_path / "ws", data_dir=tmp_path / "var")
    with pytest.raises(WorkspaceEscape):
        guard.write_file("../ws_extra/pwned.txt", "x")
    assert not (tmp_path / "ws_extra" / "pwned.txt").exists()


def test_repo_root_as_a_workspace_is_refused():
    with pytest.raises(WorkspaceRefused):
        WorkspaceGuard(REPO_ROOT)


def test_package_dir_as_a_workspace_is_refused():
    with pytest.raises(WorkspaceRefused):
        WorkspaceGuard(PACKAGE_DIR)


def test_data_dir_as_a_workspace_is_refused(tmp_path):
    data = tmp_path / "var"
    data.mkdir()
    with pytest.raises(WorkspaceRefused):
        WorkspaceGuard(data, data_dir=data)


def test_an_ancestor_of_the_repo_is_refused(tmp_path):
    with pytest.raises(WorkspaceRefused):
        WorkspaceGuard(REPO_ROOT.parent)


def test_a_symlinked_root_is_refused(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(WorkspaceRefused):
        WorkspaceGuard(link)


def test_oversize_writes_are_refused(guard):
    with pytest.raises(WorkspaceEscape):
        guard.write_file("big.txt", "x" * (300 * 1024))


def test_per_run_workspace_is_scoped_to_the_run_id(tmp_path):
    guard = workspace_for_run(tmp_path / "workspace", "abc123", data_dir=tmp_path / "var")
    assert guard.root.parts[-3:] == ("workspace", "runs", "abc123")


def test_run_id_cannot_traverse_into_another_directory(tmp_path):
    guard = workspace_for_run(tmp_path / "workspace", "../../etc", data_dir=tmp_path / "var")
    assert guard.root.name == "etc"
    assert guard.root.parent.name == "runs"
