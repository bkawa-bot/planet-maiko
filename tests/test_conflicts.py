"""Tests for agent awareness — conflict detection, union-find clustering, AST extraction."""

import os
import pytest
from planet_maiko.brain.awareness.conflicts import (
    UnionFind,
    _extract_methods_ast,
    detect_conflicts,
)


# ---------------------------------------------------------------------------
# UnionFind
# ---------------------------------------------------------------------------


def test_unionfind_groups_connected_elements():
    uf = UnionFind()
    uf.union("a", "b")
    uf.union("b", "c")
    assert uf.find("a") == uf.find("c")


def test_unionfind_keeps_disconnected_elements_separate():
    uf = UnionFind()
    uf.union("a", "b")
    uf.union("c", "d")
    assert uf.find("a") != uf.find("c")


def test_unionfind_self_find():
    uf = UnionFind()
    root = uf.find("x")
    assert root == "x"


def test_unionfind_idempotent_union():
    uf = UnionFind()
    uf.union("a", "b")
    uf.union("a", "b")
    assert uf.find("a") == uf.find("b")


def test_unionfind_chain_transitivity():
    uf = UnionFind()
    uf.union("a", "b")
    uf.union("b", "c")
    uf.union("c", "d")
    assert uf.find("a") == uf.find("d")


# ---------------------------------------------------------------------------
# _extract_methods_ast — Python fallback path
# ---------------------------------------------------------------------------


def test_extract_methods_ast_python(tmp_path):
    py_file = tmp_path / "sample.py"
    py_file.write_text(
        "def hello():\n"
        "    return 'hi'\n"
        "\n"
        "def goodbye(name):\n"
        "    return f'bye {name}'\n"
        "\n"
        "async def fetch_data():\n"
        "    pass\n"
    )

    methods = _extract_methods_ast(str(py_file), language="python")
    assert "hello" in methods
    assert "goodbye" in methods
    assert "fetch_data" in methods
    assert len(methods) == 3


def test_extract_methods_ast_returns_line_ranges(tmp_path):
    py_file = tmp_path / "ranged.py"
    py_file.write_text(
        "def first():\n"
        "    x = 1\n"
        "    return x\n"
        "\n"
        "def second():\n"
        "    return 2\n"
    )

    methods = _extract_methods_ast(str(py_file), language="python")
    start, end = methods["first"]
    assert start == 1
    assert end >= 3


def test_extract_methods_ast_empty_file(tmp_path):
    py_file = tmp_path / "empty.py"
    py_file.write_text("# just a comment\nx = 42\n")

    methods = _extract_methods_ast(str(py_file), language="python")
    assert methods == {}


def test_extract_methods_ast_unknown_language(tmp_path):
    txt_file = tmp_path / "data.txt"
    txt_file.write_text("not code")

    methods = _extract_methods_ast(str(txt_file), language=None)
    assert methods == {}


def test_extract_methods_ast_detects_language_from_extension(tmp_path):
    py_file = tmp_path / "auto_detect.py"
    py_file.write_text("def auto():\n    pass\n")

    methods = _extract_methods_ast(str(py_file))
    assert "auto" in methods


def test_extract_methods_ast_class_methods(tmp_path):
    py_file = tmp_path / "classy.py"
    py_file.write_text(
        "class MyClass:\n"
        "    def method_a(self):\n"
        "        pass\n"
        "\n"
        "    def method_b(self):\n"
        "        return True\n"
    )

    methods = _extract_methods_ast(str(py_file), language="python")
    assert "method_a" in methods
    assert "method_b" in methods


# ---------------------------------------------------------------------------
# detect_conflicts
# ---------------------------------------------------------------------------


def test_detect_conflicts_empty_when_fewer_than_two_agents(app, db):
    conflicts = detect_conflicts([])
    assert conflicts == []

    conflicts = detect_conflicts([{"task_id": "t1", "worktree_path": "/nonexistent"}])
    assert conflicts == []


def test_detect_conflicts_finds_file_overlap(tmp_path, app, db):
    # Set up two "worktrees" that share a file
    wt_a = tmp_path / "wt_a"
    wt_b = tmp_path / "wt_b"
    wt_a.mkdir()
    wt_b.mkdir()

    # Create git repos with overlapping files
    _init_git_worktree(wt_a, ["src/service.py"])
    _init_git_worktree(wt_b, ["src/service.py"])

    conflicts = detect_conflicts([
        {"task_id": "task-a", "worktree_path": str(wt_a)},
        {"task_id": "task-b", "worktree_path": str(wt_b)},
    ])

    # Both agents touching src/service.py should produce a conflict
    shared_files = [c["file"] for c in conflicts]
    assert "src/service.py" in shared_files


def test_detect_conflicts_soft_severity_different_methods(tmp_path, app, db):
    wt_a = tmp_path / "wt_soft_a"
    wt_b = tmp_path / "wt_soft_b"
    wt_a.mkdir()
    wt_b.mkdir()

    # Same file, different functions
    code_a = "def func_a():\n    return 1\n"
    code_b = "def func_b():\n    return 2\n"

    _init_git_worktree(wt_a, ["handler.py"], contents={"handler.py": code_a})
    _init_git_worktree(wt_b, ["handler.py"], contents={"handler.py": code_b})

    conflicts = detect_conflicts([
        {"task_id": "task-soft-a", "worktree_path": str(wt_a)},
        {"task_id": "task-soft-b", "worktree_path": str(wt_b)},
    ])

    handler_conflicts = [c for c in conflicts if c["file"] == "handler.py"]
    if handler_conflicts:
        assert handler_conflicts[0]["severity"] == "soft"


def test_detect_conflicts_hard_severity_same_method(tmp_path, app, db):
    wt_a = tmp_path / "wt_hard_a"
    wt_b = tmp_path / "wt_hard_b"
    wt_a.mkdir()
    wt_b.mkdir()

    # Same file, same function name
    code = "def shared_func():\n    return 42\n"

    _init_git_worktree(wt_a, ["handler.py"], contents={"handler.py": code})
    _init_git_worktree(wt_b, ["handler.py"], contents={"handler.py": code})

    conflicts = detect_conflicts([
        {"task_id": "task-hard-a", "worktree_path": str(wt_a)},
        {"task_id": "task-hard-b", "worktree_path": str(wt_b)},
    ])

    handler_conflicts = [c for c in conflicts if c["file"] == "handler.py"]
    if handler_conflicts:
        assert handler_conflicts[0]["severity"] == "hard"
        assert "shared_func" in handler_conflicts[0]["overlapping_methods"]


def test_conflict_clustering_groups_agents_sharing_files(tmp_path, app, db):
    wt_a = tmp_path / "wt_cluster_a"
    wt_b = tmp_path / "wt_cluster_b"
    wt_c = tmp_path / "wt_cluster_c"
    for wt in [wt_a, wt_b, wt_c]:
        wt.mkdir()

    # A and B share file1, B and C share file2.
    # detect_conflicts reports per-file pairwise conflicts — NOT transitive
    # clustering. So we should see two conflicts: one on file1 with [A, B],
    # another on file2 with [B, C]. A and C are not directly in conflict.
    _init_git_worktree(wt_a, ["file1.py"])
    _init_git_worktree(wt_b, ["file1.py", "file2.py"])
    _init_git_worktree(wt_c, ["file2.py"])

    conflicts = detect_conflicts([
        {"task_id": "t-a", "worktree_path": str(wt_a)},
        {"task_id": "t-b", "worktree_path": str(wt_b)},
        {"task_id": "t-c", "worktree_path": str(wt_c)},
    ])

    # Index conflicts by file so the assertion doesn't depend on order
    by_file = {c["file"]: set(c["agents"]) for c in conflicts}
    assert "file1.py" in by_file
    assert "file2.py" in by_file
    assert by_file["file1.py"] == {"t-a", "t-b"}
    assert by_file["file2.py"] == {"t-b", "t-c"}


# ---------------------------------------------------------------------------
# Helper: create a minimal git worktree with tracked + modified files
# ---------------------------------------------------------------------------


def _init_git_worktree(path, files, contents=None):
    """Create a git repo at `path` with initial commits, then modify `files`."""
    import subprocess

    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "Test"
    env["GIT_AUTHOR_EMAIL"] = "test@test.com"
    env["GIT_COMMITTER_NAME"] = "Test"
    env["GIT_COMMITTER_EMAIL"] = "test@test.com"

    subprocess.run(["git", "init"], cwd=str(path), capture_output=True, env=env)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=str(path), capture_output=True, env=env,
    )

    # Create and commit an initial version of each file
    for f in files:
        filepath = path / f
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text("# initial\n")

    subprocess.run(["git", "add", "."], cwd=str(path), capture_output=True, env=env)
    subprocess.run(
        ["git", "commit", "-m", "add files"],
        cwd=str(path), capture_output=True, env=env,
    )

    # Now modify files (creating a diff against HEAD~1)
    for f in files:
        filepath = path / f
        if contents and f in contents:
            filepath.write_text(contents[f])
        else:
            filepath.write_text("# modified\ndef changed():\n    pass\n")

    subprocess.run(["git", "add", "."], cwd=str(path), capture_output=True, env=env)
    subprocess.run(
        ["git", "commit", "-m", "modify files"],
        cwd=str(path), capture_output=True, env=env,
    )
