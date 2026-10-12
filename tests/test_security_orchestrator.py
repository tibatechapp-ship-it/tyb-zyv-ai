"""Tests for the security report layer: metadata, scoring, profiles and
the SecurityOrchestrator / SecurityReport front door (Phase 9 TUI support)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from src.security.agents import SECURITY_AGENTS
from src.security.metadata import DEFAULT_META, RULE_METADATA, metadata_for
from src.security.orchestrator import SecurityOrchestrator, SecurityReport
from src.security.profiles import PROFILE_ORDER, PROFILES, get_profile
from src.security.scoring import SCORE_PENALTY, SecurityScore, compute_score, grade_for
from src.security.types import Severity

# A small, deliberately-vulnerable source blob. The credential literal is a
# fake and is assembled so it never looks like a real token to the secret scan.
_BAD_SOURCE = (
    "password = 'hunter2xyz'\n"
    "eval(input())\n"
    "import pickle\n"
    "pickle.loads(payload)\n"
    "hashlib.md5(data)\n"
)


def _write_tree(root: Path) -> None:
    (root / "app.py").write_text(_BAD_SOURCE, encoding="utf-8")
    (root / "clean.py").write_text("x = 1 + 1\n", encoding="utf-8")


def _write_zip(root: Path, name: str) -> Path:
    target = root / name
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("src/app.py", _BAD_SOURCE)
        archive.writestr("res/logo.png", b"\x89PNG\x00\xff")  # binary, skipped
    return target


# ── metadata ─────────────────────────────────────────────────────────────
def test_metadata_for_known_rule() -> None:
    meta = metadata_for("eval-input")
    assert meta.cwe == "CWE-95"
    assert meta.cvss > 0
    assert meta.remediation


def test_metadata_for_unknown_rule_falls_back() -> None:
    assert metadata_for("not-a-real-rule") is DEFAULT_META


def test_every_rule_has_metadata() -> None:
    for meta in RULE_METADATA.values():
        assert meta.cwe.startswith("CWE-")
        assert 0.0 <= meta.cvss <= 10.0
        assert meta.remediation


# ── scoring ──────────────────────────────────────────────────────────────
def test_compute_score_and_grade_bands() -> None:
    assert compute_score({}) == 100
    assert compute_score({Severity.CRITICAL: 1}) == 70
    assert compute_score({Severity.HIGH: 2}) == 70
    assert grade_for(100) == "A"
    assert grade_for(90) == "A"
    assert grade_for(80) == "B"
    assert grade_for(70) == "C"
    assert grade_for(60) == "D"
    assert grade_for(40) == "E"
    assert grade_for(0) == "F"


def test_compute_score_never_negative() -> None:
    assert compute_score({Severity.CRITICAL: 10}) == 0


def test_score_from_counts_and_to_dict() -> None:
    score = SecurityScore.from_counts({Severity.HIGH: 1, Severity.LOW: 1})
    assert score.value == 100 - SCORE_PENALTY[Severity.HIGH] - SCORE_PENALTY[Severity.LOW]
    assert score.to_dict() == {"score": score.value, "grade": score.grade}


# ── profiles ─────────────────────────────────────────────────────────────
def test_profile_order_matches_registry() -> None:
    assert set(PROFILE_ORDER) == set(PROFILES)


def test_profiles_only_reference_real_agents() -> None:
    names = {agent.name for agent in SECURITY_AGENTS}
    for profile in PROFILES.values():
        assert profile.agents <= names
        assert profile.description


def test_full_profile_covers_every_agent() -> None:
    assert PROFILES["full"].agents == {agent.name for agent in SECURITY_AGENTS}


def test_get_profile_unknown_raises() -> None:
    with pytest.raises(KeyError):
        get_profile("nope")


# ── orchestrator: text scans ─────────────────────────────────────────────
def test_scan_text_full_finds_issues() -> None:
    report = SecurityOrchestrator().scan_text(_BAD_SOURCE, "app.py", profile="full")
    assert isinstance(report, SecurityReport)
    assert report.findings
    total = sum(report.classification.counts.values())
    assert total == len(report.findings)
    assert report.score.value < 100


def test_profile_secrets_excludes_injection() -> None:
    report = SecurityOrchestrator().scan_text(_BAD_SOURCE, "app.py", profile="secrets")
    rules = {finding.rule for finding in report.findings}
    assert "eval-input" not in rules


def test_scan_text_clean_is_grade_a() -> None:
    report = SecurityOrchestrator().scan_text("x = 1\n", "ok.py")
    assert report.findings == ()
    assert report.score.grade == "A"


def test_default_scan_text_profile_is_full() -> None:
    assert SecurityOrchestrator().scan_text(_BAD_SOURCE, "app.py").profile == "full"


# ── orchestrator: path + archive scans ───────────────────────────────────
def test_scan_directory(tmp_path: Path) -> None:
    _write_tree(tmp_path)
    report = SecurityOrchestrator().scan(tmp_path, profile="full")
    assert report.result.scanned_files == 2
    assert report.findings
    assert report.profile == "full"


def test_scan_missing_target_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        SecurityOrchestrator().scan(tmp_path / "nope")


def test_scan_zip_archive(tmp_path: Path) -> None:
    archive = _write_zip(tmp_path, "bundle.zip")
    report = SecurityOrchestrator().scan(archive, profile="full")
    assert report.findings  # the text entry is scanned, the png skipped


def test_scan_apk_archive(tmp_path: Path) -> None:
    archive = _write_zip(tmp_path, "app.apk")
    report = SecurityOrchestrator().scan(archive)
    assert report.target.endswith("app.apk")
    assert report.findings


def test_scan_bad_zip_is_empty(tmp_path: Path) -> None:
    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"not really a zip")
    report = SecurityOrchestrator().scan(broken)
    assert report.findings == ()
    assert report.result.scanned_files == 0


def test_zip_bomb_entry_is_skipped_without_decompression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A compression bomb must never be decompressed into memory.

    The oversized entry is refused from the archive's central directory before
    ``read`` is called — proved by spying on every ``ZipFile.read`` and asserting
    none of them was ever asked for a payload above the entry cap.
    """
    bomb = b"\x00" * 6_000_000  # tiny on disk under DEFLATE, huge when expanded
    archive_path = tmp_path / "bomb.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("huge.txt", bomb)
        archive.writestr("small.py", "x = 1\n")
    assert archive_path.stat().st_size < 100_000  # it really is a bomb

    read_sizes: list[int] = []
    original_read = zipfile.ZipFile.read

    def spy_read(self: zipfile.ZipFile, name: object, pwd: object = None) -> bytes:
        info = name if isinstance(name, zipfile.ZipInfo) else self.getinfo(str(name))
        read_sizes.append(info.file_size)
        return original_read(self, name)  # type: ignore[arg-type]

    monkeypatch.setattr(zipfile.ZipFile, "read", spy_read)

    report = SecurityOrchestrator().scan(archive_path)

    assert report.result.scanned_files == 1  # only the small text entry
    assert all(size <= SecurityOrchestrator._MAX_ENTRY_BYTES for size in read_sizes)
    assert 6_000_000 not in read_sizes  # the bomb was never read


def test_archive_entry_count_is_capped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The number of entries inspected is bounded, whatever the archive holds."""
    monkeypatch.setattr(SecurityOrchestrator, "_MAX_ENTRIES", 3)
    archive_path = tmp_path / "many.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for index in range(10):
            archive.writestr(f"f{index}.py", "x = 1\n")
    report = SecurityOrchestrator().scan(archive_path)
    assert report.result.scanned_files == 3


# ── orchestrator: rendering ──────────────────────────────────────────────
def test_report_to_dict_is_jsonable(tmp_path: Path) -> None:
    _write_tree(tmp_path)
    report = SecurityOrchestrator().scan(tmp_path)
    payload = json.loads(json.dumps(report.to_dict()))
    assert payload["target"] == str(tmp_path)
    assert payload["scan"]["scanned_files"] == 2
    assert "score" in payload and "classification" in payload


def test_render_markdown_and_json(tmp_path: Path) -> None:
    _write_tree(tmp_path)
    orchestrator = SecurityOrchestrator()
    report = orchestrator.scan(tmp_path)
    markdown = orchestrator.render_markdown(report, title="My Report")
    assert "My Report" in markdown
    payload = json.loads(orchestrator.render_json(report))
    assert payload["classification"]["verdict"] == report.classification.verdict.value
