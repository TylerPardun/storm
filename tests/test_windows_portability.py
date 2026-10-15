"""STORM runs on Windows field laptops: nothing may import a POSIX-only
module unconditionally (core/mem_probe.py once imported `resource` at the
top, which would have stopped radar rendering on Windows)."""
import ast
from pathlib import Path

POSIX_ONLY = {"resource", "fcntl", "termios", "pwd", "grp", "posix"}
ROOT = Path(__file__).resolve().parents[1]


def _unconditional_imports(tree):
    """Imports at module level that aren't inside an if/try block."""
    for node in tree.body:
        if isinstance(node, ast.Import):
            yield from (alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            yield node.module.split(".")[0]


def test_no_unconditional_posix_only_imports():
    offenders = []
    for path in ROOT.rglob("*.py"):
        if any(part in {"tests", "envs", ".git"} or part.startswith(".") for part in path.relative_to(ROOT).parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        bad = POSIX_ONLY.intersection(_unconditional_imports(tree))
        if bad:
            offenders.append(f"{path.relative_to(ROOT)}: {sorted(bad)}")
    assert not offenders, offenders
