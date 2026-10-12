"""Command-line entry point for the security scanner (Phase 9).

Usage::

    python -m src.security [PATH ...] [--json] [--top N]
                           [--exclude NAME] [--fail-on LEVEL]

Scans each path (a file or a directory tree), prints a markdown report by
default (JSON with ``--json``) and exits non-zero when the verdict reaches the
``--fail-on`` threshold — so it drops straight into a pre-commit hook or CI.

Everything is offline and deterministic: no network, no execution, regex only.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from src.security.classifier import FindingClassifier
from src.security.orchestrator import SecurityScanner
from src.security.reporter import SecurityReporter
from src.security.types import RiskLevel

#: Verdict ranking, lowest to highest — the ``--fail-on`` comparison order.
_RANK: dict[RiskLevel, int] = {
    RiskLevel.CLEAN: 0,
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
    RiskLevel.CRITICAL: 4,
}


def build_parser() -> argparse.ArgumentParser:
    """The argument parser (kept separate so tests can introspect it)."""
    parser = argparse.ArgumentParser(
        prog="src.security",
        description="Offline static security scan of a file or directory tree.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=["."],
        help="files or directories to scan (default: current directory)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="how many top findings to show (default: 10)",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="NAME",
        help="extra directory name to skip (repeatable)",
    )
    parser.add_argument(
        "--fail-on",
        choices=[level.value for level in RiskLevel],
        default=RiskLevel.HIGH.value,
        help="exit non-zero when the verdict reaches this level (default: high)",
    )
    return parser


def main(argv: Sequence[str]) -> int:
    """Run the scan described by ``argv`` (including the program name)."""
    args = build_parser().parse_args(list(argv[1:]))
    scanner = SecurityScanner()
    classifier = FindingClassifier(top_limit=args.top)
    reporter = SecurityReporter()

    highest = RiskLevel.CLEAN
    for target in args.paths:
        result = scanner.scan_path(target, exclude=set(args.exclude))
        classification = classifier.classify(result.findings)
        if args.json:
            print(reporter.render_json(result, classification))
        else:
            print(reporter.render_markdown(result, classification, title=f"Security scan — {target}"))
        if _RANK[classification.verdict] > _RANK[highest]:
            highest = classification.verdict

    return 1 if _RANK[highest] >= _RANK[RiskLevel(args.fail_on)] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
