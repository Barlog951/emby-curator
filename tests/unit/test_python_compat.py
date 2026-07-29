"""Guards that the package actually works on every Python we claim to support.

Regression for 2026-07-30: production (Python 3.12) could not import 7 of 47
modules while the whole suite was green locally on 3.14. Cause: ``9024a70``
un-quoted self-referencing annotations such as::

    class ExistingQuality:
        @classmethod
        def from_emby_item(cls, item: dict[str, Any]) -> ExistingQuality: ...

Python 3.14 defers annotation evaluation (PEP 649), so the name resolves lazily
and the definition is accepted. On 3.12/3.13 the annotation is evaluated while
the class body is still executing, so the class name does not exist yet and the
import dies with ``NameError``. ``pyproject.toml`` declares
``requires-python = ">=3.12"``, so this shipped to PyPI broken for 3.12/3.13.

Two complementary guards:

* :func:`test_no_unquoted_self_referencing_annotations` is a static AST check, so
  it fails on *any* interpreter — including 3.14, where an import test cannot
  reproduce the bug.
* :func:`test_every_module_imports` is the end-to-end check that catches this and
  any other import-time breakage on whichever interpreter CI happens to run.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

import pytest

import emby_dedupe

PACKAGE_ROOT = Path(emby_dedupe.__file__).parent


def _module_paths() -> list[Path]:
    return sorted(PACKAGE_ROOT.rglob("*.py"))


def _has_future_annotations(tree: ast.Module) -> bool:
    """True if the module opts into PEP 563 deferred annotations."""
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in tree.body
    )


def _annotation_nodes(class_node: ast.ClassDef) -> list[ast.expr]:
    """Every annotation in `class_node` that Python evaluates eagerly.

    That means function signatures (return type + parameters) and class-level
    variable annotations. Annotations on locals inside a function body are never
    evaluated at runtime, so they cannot trigger this failure and are ignored.
    """
    annotations: list[ast.expr] = []
    for node in ast.walk(class_node):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if node.returns is not None:
                annotations.append(node.returns)
            args = node.args
            for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]:
                if arg is not None and arg.annotation is not None:
                    annotations.append(arg.annotation)
        elif isinstance(node, ast.AnnAssign) and node.annotation is not None:
            annotations.append(node.annotation)
    return annotations


@pytest.mark.parametrize("path", _module_paths(), ids=lambda p: p.name)
def test_no_unquoted_self_referencing_annotations(path: Path) -> None:
    """A class must not name itself in an eagerly-evaluated annotation.

    Fix either by adding ``from __future__ import annotations`` to the module or
    by quoting the reference (``-> "ExistingQuality"``).
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    if _has_future_annotations(tree):
        return  # annotations are strings; self-reference is safe

    offenders: list[str] = []
    for class_node in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        for annotation in _annotation_nodes(class_node):
            for name in (n for n in ast.walk(annotation) if isinstance(n, ast.Name)):
                if name.id == class_node.name:
                    offenders.append(f"{path.name}:{name.lineno} -> {class_node.name}")

    assert not offenders, (
        "Class refers to itself in an eagerly-evaluated annotation; this raises "
        "NameError on Python < 3.14 (PEP 649 only defers evaluation from 3.14 on). "
        "Add 'from __future__ import annotations' or quote the reference. "
        f"Offenders: {offenders}"
    )


def test_every_module_imports() -> None:
    """Every module in the package must import cleanly on the running Python."""
    failures: list[str] = []
    for module in pkgutil.walk_packages(emby_dedupe.__path__, f"{emby_dedupe.__name__}."):
        try:
            importlib.import_module(module.name)
        except Exception as exc:  # noqa: BLE001 - report every failure, not just the first
            failures.append(f"{module.name}: {type(exc).__name__}: {exc}")

    assert not failures, "Modules failed to import:\n" + "\n".join(failures)
