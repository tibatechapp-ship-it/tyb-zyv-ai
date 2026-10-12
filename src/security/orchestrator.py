"""The security scanner — runs the eight agents over a tree (Phase 9)."""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any, Sequence

from src.security.agents import SECURITY_AGENTS, SecurityAgent
from src.security.classifier import Classification, FindingClassifier
from src.security.profiles import ScanProfile, get_profile
from src.security.scoring import SecurityScore
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


class SecurityReport:
    """A full scan result: the raw scan plus its classification and score."""

    __slots__ = ("target", "profile", "result", "classification", "score")

    def __init__(
        self,
        *,
        target: str,
        profile: str,
        result: ScanResult,
        classification: Classification,
        score: SecurityScore,
    ) -> None:
        self.target = target
        self.profile = profile
        self.result = result
        self.classification = classification
        self.score = score

    @property
    def findings(self) -> tuple[Finding, ...]:
        """The deduplicated findings, worst-first."""
        return self.result.findings

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serialisable view of the whole report."""
        return {
            "target": self.target,
            "profile": self.profile,
            "scan": self.result.to_dict(),
            "classification": self.classification.to_dict(),
            "score": self.score.to_dict(),
        }


class SecurityOrchestrator:
    """The front door used by the TUI/CLI: pick a profile, scan, classify, score.

    Unlike the bare :class:`SecurityScanner`, this ties together the four moves
    a caller actually wants: choose which agents to run (a :class:`ScanProfile`),
    run them over a path — including inside an ``.apk``/``.zip`` archive — then
    classify the findings and compute a 0–100 :class:`SecurityScore`.
    """

    #: File suffixes scanned as ZIP archives rather than as plain text.
    ARCHIVE_SUFFIXES: tuple[str, ...] = (".apk", ".zip")
    #: Largest single entry we will ever decompress (bytes). The size is read
    #: from the ZIP central directory *before* decompression, so a compression
    #: bomb (tiny on disk, huge when expanded) is skipped without ever being
    #: unpacked — the only safe way to scan untrusted archives.
    _MAX_ENTRY_BYTES: int = 2_000_000
    #: Hard cap on the number of entries inspected (defeats entry-count floods).
    _MAX_ENTRIES: int = 5_000
    #: Hard cap on the total decompressed bytes across the whole archive.
    _MAX_TOTAL_BYTES: int = 20_000_000

    def __init__(self, *, top_limit: int = 10) -> None:
        self._classifier = FindingClassifier(top_limit=top_limit)

    # -- scanning ----------------------------------------------------------
    def _scanner_for(self, profile: ScanProfile) -> SecurityScanner:
        """A scanner holding only the agents the profile selects (in scan order)."""
        agents = [agent_class() for agent_class in SECURITY_AGENTS if agent_class.name in profile.agents]
        return SecurityScanner(agents=agents)

    def scan(
        self,
        target: str | Path,
        *,
        profile: str = "full",
        exclude: set[str] | frozenset[str] | None = None,
    ) -> SecurityReport:
        """Scan ``target`` (a file, tree or ``.apk``/``.zip``) under ``profile``."""
        chosen = get_profile(profile)
        scanner = self._scanner_for(chosen)
        path = Path(target)
        if path.is_file() and path.suffix.lower() in self.ARCHIVE_SUFFIXES:
            result = self._scan_archive(scanner, path)
        else:
            result = scanner.scan_path(target, exclude=exclude)
        return self._build(target=str(target), profile=chosen.name, result=result)

    def scan_text(self, text: str, path: str = "<string>", *, profile: str = "full") -> SecurityReport:
        """Scan one in-memory text blob under ``profile``."""
        chosen = get_profile(profile)
        scanner = self._scanner_for(chosen)
        result = ScanResult(scanner.scan_text(text, path), 1, path)
        return self._build(target=path, profile=chosen.name, result=result)

    def _scan_archive(self, scanner: SecurityScanner, path: Path) -> ScanResult:
        """Scan the UTF-8 entries inside a ZIP/APK archive (binary entries skipped).

        Safe against decompression bombs: an entry's uncompressed size is read
        from the archive's central directory *before* it is decompressed, so any
        entry bigger than :attr:`_MAX_ENTRY_BYTES` is skipped without ever being
        unpacked. The number of entries and the total decompressed bytes are also
        hard-capped, and a corrupt/encrypted entry never aborts the whole scan.
        """
        findings: list[Finding] = []
        files = 0
        total_bytes = 0
        try:
            with zipfile.ZipFile(path) as archive:
                for index, info in enumerate(archive.infolist()):
                    if index >= self._MAX_ENTRIES or total_bytes >= self._MAX_TOTAL_BYTES:
                        break
                    if info.is_dir():
                        continue
                    # Bomb guard: the central directory carries the uncompressed
                    # size, so we can refuse an oversized entry *before* reading it.
                    if info.file_size > self._MAX_ENTRY_BYTES:
                        continue
                    if total_bytes + info.file_size > self._MAX_TOTAL_BYTES:
                        break
                    try:
                        data = archive.read(info)
                    except (zipfile.BadZipFile, RuntimeError, EOFError, OSError):
                        continue  # corrupt, truncated or password-protected entry
                    total_bytes += len(data)
                    try:
                        text: str = data.decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                    files += 1
                    findings.extend(scanner.scan_text(text, f"{path}!{info.filename}"))
        except (zipfile.BadZipFile, OSError):
            return ScanResult([], 0, str(path))
        return ScanResult(findings, files, str(path))

    def _build(self, *, target: str, profile: str, result: ScanResult) -> SecurityReport:
        classification = self._classifier.classify(result.findings)
        score = SecurityScore.from_counts(classification.counts)
        return SecurityReport(
            target=target,
            profile=profile,
            result=result,
            classification=classification,
            score=score,
        )

    # -- rendering (thin wrappers so the TUI holds one object) -------------
    def render_markdown(self, report: SecurityReport, *, title: str = "Security scan report") -> str:
        """A markdown report for ``report`` (delegates to :class:`SecurityReporter`)."""
        from src.security.reporter import SecurityReporter

        return SecurityReporter().render_markdown(report.result, report.classification, title=title)

    def render_json(self, report: SecurityReport) -> str:
        """A JSON report for ``report`` (delegates to :class:`SecurityReporter`)."""
        from src.security.reporter import SecurityReporter

        return SecurityReporter().render_json(report.result, report.classification)