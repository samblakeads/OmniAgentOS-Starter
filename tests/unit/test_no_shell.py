"""There is no shell in this system.

The agents' only capability is the workspace tool allow-list. This test reads the
package source as an AST and fails if anything can spawn a process or evaluate a
string — a claim in the README is worth nothing without a mechanical check.
"""

from __future__ import annotations

import ast

import pytest

from omniagentos_starter.config import PACKAGE_DIR

FORBIDDEN_MODULES = {"subprocess", "pty", "shlex", "commands", "popen2"}
FORBIDDEN_CALLS = {"system", "popen", "execv", "execve", "execvp", "spawnl", "spawnv", "fork", "forkpty"}
FORBIDDEN_BUILTINS = {"eval", "exec", "compile", "__import__"}

SOURCES = sorted(PACKAGE_DIR.rglob("*.py"))


def test_the_package_has_source_to_check():
    assert len(SOURCES) >= 8


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_no_module_can_spawn_a_process_or_evaluate_a_string(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in FORBIDDEN_MODULES, f"{path.name} imports {alias.name}"
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in FORBIDDEN_MODULES, f"{path.name} imports {node.module}"
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                assert func.id not in FORBIDDEN_BUILTINS, f"{path.name} calls {func.id}()"
            if isinstance(func, ast.Attribute):
                assert func.attr not in FORBIDDEN_CALLS, f"{path.name} calls .{func.attr}()"


def test_no_shell_tool_is_exposed_to_agents():
    from omniagentos_starter import tools

    assert tools.TOOL_NAMES == ("read_file", "write_file", "list_files")
    assert not any(name in dir(tools.WorkspaceGuard) for name in ("run", "shell", "exec", "execute"))
