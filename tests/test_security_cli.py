"""Tests for the security scanner CLI (``python -m src.security``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.security.__main__ import _RANK, build_parser, main
from src.security.types import RiskLevel


def test_build_parser_defaults() -> None:
    args = build_parser().parse_args([])
    assert args.paths == ["."]
    assert args.json is False
    assert args.top == 10
    assert args.exclude == []
    assert args.fail_on == "high"


def test_rank_is_total_order() -> None:
    assert _RANK[RiskLevel.CLEAN] < _RANK[RiskLevel.LOW] < _RANK[RiskLevel.MEDIUM]
    assert _RANK[RiskLevel.MEDIUM] < _RANK[RiskLevel.HIGH] < _RANK[RiskLevel.CRITICAL]


def test_clean_tree_exits_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "ok.py").write_text("value = 1\n", encoding="utf-8")
    code = main(["src.security", str(tmp_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "Verdict" in out
    assert "No findings" in out


def test_critical_finding_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "bad.py").write_text("answer = eval(input())\n", encoding="utf-8")
    code = main(["src.security", str(tmp_path), "--fail-on", "high"])
    assert code == 1
    out = capsys.readouterr().out
    assert "CRITICAL" in out


def test_fail_on_threshold_respected(tmp_path: Path) -> None:
    # a single HIGH finding, but we only fail on CRITICAL
    (tmp_path / "bad.py").write_text("payload = pickle.loads(buf)\n", encoding="utf-8")
    assert main(["src.security", str(tmp_path), "--fail-on", "critical"]) == 0
    assert main(["src.security", str(tmp_path), "--fail-on", "high"]) == 1


def test_json_output_is_valid(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "bad.py").write_text("password = 'hunter2secret'\n", encoding="utf-8")
    code = main(["src.security", str(tmp_path), "--json", "--top", "1"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["scan"]["scanned_files"] == 1
    assert payload["classification"]["verdict"] in {level.value for level in RiskLevel}
    assert len(payload["classification"]["top_findings"]) <= 1


def test_exclude_skips_named_dir(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "x.py").write_text("answer = eval(input())\n", encoding="utf-8")
    assert main(["src.security", str(tmp_path), "--exclude", "vendor"]) == 0
    assert "No findings" in capsys.readouterr().out


def test_multiple_paths_aggregate_worst_verdict(tmp_path: Path) -> None:
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "ok.py").write_text("value = 1\n", encoding="utf-8")
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "bad.py").write_text("x = pickle.loads(buf)\n", encoding="utf-8")
    assert main(["src.security", str(clean), str(bad), "--fail-on", "high"]) == 1


def test_missing_path_propagates(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        main(["src.security", str(tmp_path / "does-not-exist")])
