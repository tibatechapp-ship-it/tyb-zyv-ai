"""Contract tests for the adversarial report itself and the shape of the suite.

These are *meta* tests — they carry first-class value but are deliberately **not**
attack vectors, so this module sets no ``CATEGORY`` and the report hook ignores
it. They lock two things a reviewer cares about:

1. the ``AdversarialReport`` mechanics (counts, resilience, per-category buckets,
   stable golden snapshot), and
2. the suite's *shape* — every required category is present and each holds at
   least ``MIN_PER_CATEGORY`` vectors, for at least 48 total.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from syrupy.assertion import SnapshotAssertion

import attacks as attacks_module
from report import AdversarialReport

_HERE = pathlib.Path(__file__).resolve().parent

#: Every category module that MUST exist in the suite (6 required + 2 bonus).
EXPECTED_CATEGORIES = {
    "test_injection.py",
    "test_dos.py",
    "test_manipulation.py",
    "test_security.py",
    "test_integration.py",
    "test_comprehension.py",
    "test_fuzz.py",
    "test_chaos.py",
}
#: Each category module must expose at least this many attack vectors.
MIN_PER_CATEGORY = 8
#: The whole suite must exercise at least this many vectors.
MIN_TOTAL = 48


def _category_modules() -> dict[str, pathlib.Path]:
    """Category modules (any ``test_*.py`` that defines a module ``CATEGORY``)."""
    found: dict[str, pathlib.Path] = {}
    for path in sorted(_HERE.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            is_assign = isinstance(node, ast.Assign) and any(
                getattr(target, "id", None) == "CATEGORY" for target in node.targets
            )
            is_annassign = (
                isinstance(node, ast.AnnAssign)
                and getattr(node.target, "id", None) == "CATEGORY"
            )
            if is_assign or is_annassign:
                found[path.name] = path
                break
    return found


def _parametrize_size(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """How many vectors a test expands to (product of ``parametrize`` list sizes)."""
    size = 1
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        func = decorator.func
        if not (isinstance(func, ast.Attribute) and func.attr == "parametrize"):
            continue
        if len(decorator.args) < 2:
            continue
        source = decorator.args[1]
        if isinstance(source, (ast.List, ast.Tuple)):
            size *= len(source.elts)
        elif isinstance(source, ast.Name):
            # A payload list imported from ``attacks`` (e.g. ROLE_CONFUSION).
            resolved = getattr(attacks_module, source.id, None)
            if resolved is not None:
                size *= len(resolved)
    return size


def _vector_count(path: pathlib.Path) -> int:
    """Count a module's attack vectors, expanding parametrized cases."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return sum(
        _parametrize_size(node)
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    )


def test_suite_covers_every_required_category() -> None:
    """All six required attack categories are present."""
    assert set(_category_modules()) == EXPECTED_CATEGORIES


def test_each_category_has_enough_vectors() -> None:
    """No category is a token effort — each carries its own weight."""
    counts = {name: _vector_count(path) for name, path in _category_modules().items()}
    short = {name: count for name, count in counts.items() if count < MIN_PER_CATEGORY}
    assert not short, f"categories under {MIN_PER_CATEGORY} vectors: {short}"


def test_suite_has_at_least_48_vectors_total() -> None:
    """The suite meets the commissioned minimum of 48 attack vectors."""
    total = sum(_vector_count(path) for path in _category_modules().values())
    assert total >= MIN_TOTAL, f"only {total} vectors, need >= {MIN_TOTAL}"


def test_report_counts_and_resilience() -> None:
    """A populated report tallies held/broken, resilience and per-category rows."""
    report = AdversarialReport()
    report.record("1-injection", "t_a", held=True)
    report.record("1-injection", "t_b", held=True)
    report.record("2-dos", "t_c", held=False, detail="boom")

    assert report.total == 3
    assert report.held_count == 2
    assert report.broken_count == 1
    assert report.resilience == pytest.approx(2 / 3)
    assert report.by_category() == {"1-injection": (2, 0), "2-dos": (0, 1)}
    assert report.verdict_for("t_c") == "BROKEN"
    assert report.verdict_for("does-not-exist") == "UNKNOWN"


def test_empty_report_is_safe() -> None:
    """An empty report renders without dividing by zero."""
    report = AdversarialReport()
    assert report.total == 0
    assert report.held_count == 0
    assert report.broken_count == 0
    assert report.resilience == 0.0
    assert "ADVERSARIAL REPORT" in report.summary()
    assert report.to_dict()["outcomes"] == []


def test_report_snapshot_is_stable(snapshot: SnapshotAssertion) -> None:
    """The JSON snapshot of a fixed report never drifts (golden test)."""
    report = AdversarialReport()
    report.record("1-injection", "t_sql", held=True)
    report.record("2-dos", "t_loop", held=True)
    report.record("4-security", "t_perms", held=False, detail="permission widened")

    assert report.to_dict() == snapshot
