#!/usr/bin/env python3
"""Fail if any function in the assistant scripts is missing type hints.

This repo's standard is Python 3.11+ with *every* function fully annotated
(arguments and return). The CI runs this script on each push.

Usage:
    python scripts/check_annotations.py [file ...]

Exit code 0 and a success line when everything is annotated, otherwise it
prints each offender as ``file:line name: missing ...`` and exits 1.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

DEFAULT_FILES: tuple[str, ...] = ("zyvo-telegram.py", "zyvo-voice.py")
SKIP_ARGS: frozenset[str] = frozenset({"self", "cls"})


def missing_hints(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Return the parameter names (and 'return') that lack an annotation."""
    args = func.args
    missing: list[str] = [
        arg.arg
        for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)
        if arg.arg not in SKIP_ARGS and arg.annotation is None
    ]
    if args.vararg is not None and args.vararg.annotation is None:
        missing.append("*" + args.vararg.arg)
    if args.kwarg is not None and args.kwarg.annotation is None:
        missing.append("**" + args.kwarg.arg)
    if func.returns is None:
        missing.append("return")
    return missing


def check_file(path: Path) -> list[str]:
    """Return a list of ``path:line name: missing ...`` problem lines."""
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            missing = missing_hints(node)
            if missing:
                problems.append(
                    f"{path}:{node.lineno} {node.name}: missing {', '.join(missing)}"
                )
    return problems


def main(argv: list[str]) -> int:
    """Entry point: check every file and report."""
    files = [Path(p) for p in (argv[1:] or DEFAULT_FILES)]
    all_problems: list[str] = []
    for path in files:
        all_problems.extend(check_file(path))

    if all_problems:
        print("\n".join(all_problems))
        print(f"\nFAIL: {len(all_problems)} function(s) not fully type-annotated.")
        return 1

    print(f"OK: all functions fully type-annotated in {', '.join(str(f) for f in files)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
