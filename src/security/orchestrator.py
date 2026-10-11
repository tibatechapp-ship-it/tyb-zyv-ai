"""The security scanner — runs the eight agents over a tree (Phase 9)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from src.security.agents import SECURITY_AGENTS, SecurityAgent
from src.security.types import Finding

#: Directories never scanned, whatever the target.
DEFAULT_EXCLUDE: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".tox",
        ".venv",
        "venv",
        "env",
        ".env",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        "htmlcov",
    }
)


class ScanResult:
    """Everything one scan pass produced."""

    __slots__ = ("findings", "scanned_files", "root")

    def __init__(self, findings: Sequence[Finding], scanned_files: int, root: str) -> None:
        self.findings: tuple[Finding, ...] = tuple(findings)
        self.scanned_files = scanned_files
        self.root = root

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serialisable view of the scan."""
        return {
            "root": self.root,
            "scanned_files": self.scanned_files,
            "findings": [finding.to_dict() for finding in self.findings],
        }


class SecurityScanner:
    """The orchestrator: walks a path, feeds every file to every agent."""

    def __init__(self, agents: Sequence[SecurityAgent] | None = None) -> None:
        instantiated: list[SecurityAgent] = []
        if agents is None:
            instantiated.extend(agent_class() for agent_class in SECURITY_AGENTS)
        else:
            instantiated.extend(agents)
        self.agents: tuple[SecurityAgent, ...] = tuple(instantiated)

    def scan_text(self, text: str, path: str = "<string>") -> list[Finding]:
        """Scan one text blob with every agent (dependency agents opt out)."""
        findings: list[Finding] = []
        for agent in self.agents:
            if not agent.matches(Path(path)):
                continue
            findings.extend(agent.scan(text, path))
        return findings

    def scan_file(self, file_path: Path, *, max_bytes: int = 2_000_000) -> list[Finding]:
        """Scan one file; binary or oversized files are skipped."""
        try:
            data = file_path.read_bytes()
        except OSError:
            return []
        if len(data) > max_bytes:
            return []
        try:
            text: str = data.decode("utf-8")
        except UnicodeDecodeError:
            return []
        return self.scan_text(text, str(file_path))

    def scan_path(
        self,
        path: str | Path,
        *,
        exclude: set[str] | frozenset[str] | None = None,
        max_bytes: int = 2_000_000,
    ) -> ScanResult:
        """Scan a whole tree (recursively) and return a :class:`ScanResult`."""
        root = Path(path)
        if not root.exists():
            raise FileNotFoundError(f"scan target does not exist: {root}")
        skipped = DEFAULT_EXCLUDE | frozenset(exclude or ())
        findings: list[Finding] = []
        files = 0
        for candidate in sorted(root.rglob("*")):
            if not candidate.is_file():
                continue
            if any(part in skipped for part in candidate.parts):
                continue
            files += 1
            findings.extend(self.scan_file(candidate, max_bytes=max_bytes))
        return ScanResult(findings, files, str(root))