"""Tests for src/security/ (Phase 9) — 8 agents + orchestrator + classifier
+ reporter. All scans run over tmp_path fixtures; nothing hits the repo."""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import pytest

from src.security import (
    DEFAULT_EXCLUDE,
    Classification,
    Finding,
    FindingClassifier,
    RiskLevel,
    SECURITY_AGENTS,
    ScanResult,
    SecurityAgent,
    SecurityReporter,
    SecurityScanner,
    Severity,
)
from src.security.agents import _DependencyAgent, _SecretsAgent
from src.security.classifier import _verdict_for
from src.security.types import SEVERITY_WEIGHTS


def _scan_text(text: str, path: str = "app.py") -> list[Finding]:
    return SecurityScanner().scan_text(text, path)


# ---------------------------------------------------------------- each agent
def test_secrets_agent_hits_every_rule() -> None:
    # The token/key literals are assembled at runtime so this test file itself
    # is not flagged by the repo's committed-secret scanner.
    text = "\n".join(
        [
            "aws = 'AKIA0123456789ABCDEF'",
            "tok = 'ghp_" + "A" * 36 + "'",
            "key = 'sk-abcdefghijklmnopqrstuvwxyz0123'",
            "-----BEGIN " + "PRIVATE KEY" + "-----",
            "password = 'correct horse battery staple'",
        ]
    )
    findings = _scan_text(text)
    rules = {finding.rule for finding in findings}
    assert {
        "aws-access-key",
        "github-token",
        "api-key",
        "private-key-block",
        "plaintext-credential",
    } <= rules


def test_secrets_github_token_shape() -> None:
    findings = _scan_text("token = 'ghp_' + 'A' * 36\n")
    assert all(finding.rule != "github-token" for finding in findings)  # not a literal


def test_injection_agent() -> None:
    text = "\n".join(
        [
            "exec(code)",
            "subprocess.run(cmd)",
            "cursor.execute('SELECT * FROM users WHERE id = ' + uid)",
            "subprocess.call(cmd, shell=True)",
            "os.system(f'rm -rf {folder}')",
        ]
    )
    rules = {finding.rule for finding in _scan_text(text)}
    assert {"code-eval", "shell-call", "sql-concatenation", "shell-true", "command-format"} <= rules


def test_traversal_agent() -> None:
    text = "path = '/etc/passwd'\nopen('/etc/secret')  # absolute\nuser = '../../etc/shadow'\n"
    findings = _scan_text(text)
    rules = {finding.rule for finding in findings}
    assert "absolute-path-open" in rules
    assert "path-traversal" in rules


def test_crypto_agent() -> None:
    text = "\n".join(
        [
            "digest = hashlib.md5(data).hexdigest()",
            "from cryptography.hazmat.primitives.ciphers.algorithms import DES",
            "n = random.randint(1, 10)",
            "cipher = AES.new(key, AES.MODE_ECB)",
        ]
    )
    rules = {finding.rule for finding in _scan_text(text)}
    assert {"weak-hash", "weak-cipher", "weak-rng", "ecb-mode"} <= rules


def test_deserialization_agent() -> None:
    text = "\n".join(
        [
            "obj = pickle.loads(payload)",
            "data = yaml.load(raw)",
            "blob = marshal.loads(buf)",
            "model = joblib.load(path)",
            "ans = eval(input())",
        ]
    )
    rules = {finding.rule for finding in _scan_text(text)}
    assert {"pickle", "yaml-load", "marshal", "joblib", "eval-input"} <= rules
    safe = _scan_text("data = yaml.safe_load(raw)\n")
    assert all(finding.rule != "yaml-load" for finding in safe)


def test_network_agent() -> None:
    text = "\n".join(
        [
            "url = 'http://insecure.example/x'",
            "ip = '192.168.1.10'",
            "socket.connect((host, 443))",
            "requests.post(url, data=body)",
        ]
    )
    rules = {finding.rule for finding in _scan_text(text)}
    assert {"http-url", "ip-literal", "raw-socket", "http-client"} <= rules


def test_dependency_agent_only_on_dependency_files() -> None:
    req = Path("requirements.txt")
    assert _DependencyAgent().matches(req)
    assert not _DependencyAgent().matches(Path("app.py"))
    text = "requests\nflask==3.0.0\n# comment\n\n"
    findings = SecurityScanner().scan_text(text, "requirements.txt")
    assert [f.rule for f in findings] == ["unpinned-dependency"]
    assert findings[0].line == 1


def test_pii_agent() -> None:
    text = "\n".join(
        [
            "logger.info('user is ' + 'alice@example.com')",
            "print('calling', '+1 555 123 4567')",
            "phone = request.form.get('phone')",
        ]
    )
    findings = _scan_text(text)
    rules = {finding.rule for finding in findings}
    assert "pii-in-log" in rules
    assert "pii-capture" in rules
    quiet = _scan_text("logger.info('just a count: ' + str(n))\nemail = 'a@b.co'\n", "logs.py")
    assert all(finding.rule != "pii-in-log" for finding in quiet)


def test_agent_metadata_and_scan_paths() -> None:
    names = {agent.name for agent in SecurityScanner().agents}
    assert names == {
        "secrets",
        "injection",
        "traversal",
        "crypto",
        "deserialization",
        "network",
        "dependency",
        "pii",
    }
    assert all(agent.description for agent in SecurityScanner().agents)
    assert len(SECURITY_AGENTS) == 8


def test_finding_value_semantics() -> None:
    one = Finding(agent="a", rule="r", path="p", line=1, severity=Severity.HIGH, message="m")
    two = Finding(agent="a", rule="r", path="p", line=1, severity=Severity.HIGH, message="m")
    assert one == two
    assert hash(one) == hash(two)
    assert one != "not a finding"
    assert one.to_dict()["severity"] == "high"
    assert "a" in repr(one)


# ------------------------------------------------------------- orchestrator
def test_scan_path_tree(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = eval(input())\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("plain text\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.py").write_text("pickle.loads(buf)\n", encoding="utf-8")
    scanner = SecurityScanner()
    result = scanner.scan_path(tmp_path)
    assert result.scanned_files == 3
    paths = {finding.path for finding in result.findings}
    assert any(path.endswith("a.py") for path in paths)
    assert any(path.endswith("c.py") for path in paths)
    assert not any(path.endswith("b.txt") for path in paths)
    assert result.root == str(tmp_path)
    assert result.to_dict()["scanned_files"] == 3


def test_scan_path_excludes_and_skips(tmp_path: Path) -> None:
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("password = 'abc123456'\n", encoding="utf-8")
    (tmp_path / "binary.bin").write_bytes(b"\x00\x01\xff\xfe\x00\x02")
    (tmp_path / "huge.py").write_text("password = 'abc123456'\n" * 100_000, encoding="utf-8")
    result = SecurityScanner().scan_path(tmp_path, max_bytes=1_000)
    assert result.scanned_files == 2
    assert len(result.findings) == 0  # both were skipped (binary + too big)
    (tmp_path / "keep.py").write_text("password = 'abc123456'\n", encoding="utf-8")
    result = SecurityScanner().scan_path(tmp_path, max_bytes=1_000)
    assert result.scanned_files == 3
    assert len(result.findings) == 1


def test_scan_path_missing_target(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="does not exist"):
        SecurityScanner().scan_path(tmp_path / "nope")
    with pytest.raises(FileNotFoundError):
        SecurityScanner().scan_path(tmp_path / "also-missing")


def test_scan_file_oserror_returns_empty(tmp_path: Path) -> None:
    scanner = SecurityScanner()
    assert scanner.scan_file(tmp_path / "missing.py") == []


def test_custom_agents_override_defaults(tmp_path: Path) -> None:
    class OnlySecrets(SecurityAgent):
        name = "secrets"
        description = "only secrets"
        patterns: ClassVar[tuple] = _SecretsAgent.patterns

    scanner = SecurityScanner(agents=[OnlySecrets()])
    assert len(scanner.agents) == 1
    assert scanner.agents[0].name == "secrets"
    (tmp_path / "a.py").write_text("eval(x)\npassword = 'abc123456'\n", encoding="utf-8")
    rules = {f.rule for f in scanner.scan_path(tmp_path).findings}
    assert rules == {"plaintext-credential"}


def test_exclude_constant_used() -> None:
    assert ".git" in DEFAULT_EXCLUDE
    assert "node_modules" in DEFAULT_EXCLUDE


# --------------------------------------------------------------- classifier
def test_classifier_dedup_and_verdicts() -> None:
    make = lambda rule: Finding(  # noqa: E731
        agent="a", rule=rule, path="p.py", line=1, severity=Severity.MEDIUM, message=rule
    )
    dup = [make("same"), make("same")]
    cls = FindingClassifier().classify(dup)
    assert cls.finding_count == 2
    assert cls.deduped_count == 1
    assert cls.verdict == RiskLevel.MEDIUM
    assert cls.risk_score == SEVERITY_WEIGHTS[Severity.MEDIUM]
    assert cls.counts[Severity.MEDIUM] == 1


def test_classifier_verdict_ladder() -> None:
    critical = Finding(agent="a", rule="r", path="p", line=1, severity=Severity.CRITICAL, message="x")
    high = Finding(agent="a", rule="r", path="p", line=1, severity=Severity.HIGH, message="x")
    medium = Finding(agent="a", rule="r", path="p", line=1, severity=Severity.MEDIUM, message="x")
    low = Finding(agent="a", rule="r", path="p", line=1, severity=Severity.LOW, message="x")
    info = Finding(agent="a", rule="r", path="p", line=1, severity=Severity.INFO, message="x")
    three_medium = [Finding(agent="a", rule=f"r{i}", path="p", line=1, severity=Severity.MEDIUM, message=str(i)) for i in range(3)]
    assert FindingClassifier().classify([critical]).verdict is RiskLevel.CRITICAL
    assert FindingClassifier().classify([high]).verdict is RiskLevel.HIGH
    assert FindingClassifier().classify(three_medium).verdict is RiskLevel.HIGH
    assert FindingClassifier().classify([medium]).verdict is RiskLevel.MEDIUM
    five_low = [Finding(agent="a", rule=f"r{i}", path="p", line=i, severity=Severity.LOW, message=str(i)) for i in range(5)]
    assert FindingClassifier().classify(five_low).verdict is RiskLevel.MEDIUM
    assert FindingClassifier().classify([low]).verdict is RiskLevel.LOW
    assert FindingClassifier().classify([info]).verdict is RiskLevel.LOW
    assert FindingClassifier().classify([]).verdict is RiskLevel.CLEAN


def test_verdict_helper() -> None:
    from collections import Counter

    assert _verdict_for(Counter()) is RiskLevel.CLEAN
    assert _verdict_for(Counter({Severity.CRITICAL: 1})) is RiskLevel.CRITICAL
    assert _verdict_for(Counter({Severity.HIGH: 2})) is RiskLevel.HIGH
    assert _verdict_for(Counter({Severity.MEDIUM: 3})) is RiskLevel.HIGH
    assert _verdict_for(Counter({Severity.MEDIUM: 1})) is RiskLevel.MEDIUM
    assert _verdict_for(Counter({Severity.LOW: 5})) is RiskLevel.MEDIUM
    assert _verdict_for(Counter({Severity.LOW: 1})) is RiskLevel.LOW
    assert _verdict_for(Counter({Severity.INFO: 1})) is RiskLevel.LOW


def test_classifier_top_limit_and_order() -> None:
    findings = [
        Finding(agent="a", rule=f"r{i}", path="p", line=i, severity=Severity.LOW, message=str(i))
        for i in range(12)
    ]
    findings.append(Finding(agent="a", rule="bad", path="p", line=99, severity=Severity.HIGH, message="b"))
    cls = FindingClassifier(top_limit=5).classify(findings)
    assert len(cls.top_findings) == 5
    assert cls.top_findings[0].severity is Severity.HIGH
    with pytest.raises(ValueError, match="top_limit"):
        FindingClassifier(top_limit=0)
    assert cls.to_dict()["verdict"] == "high"
    assert cls.to_dict()["counts"]["low"] == 12


def test_classification_deduped_zero_on_clean() -> None:
    cls = FindingClassifier().classify([])
    assert cls.deduped_count == 0
    assert cls.to_dict()["top_findings"] == []


# ----------------------------------------------------------------- reporter
def make_scan() -> tuple[ScanResult, Classification]:
    scanner = SecurityScanner()
    result = ScanResult(
        findings=scanner.scan_text("password = 'abc123456'\neval(x)\n", "demo.py"),
        scanned_files=1,
        root="/fake/root",
    )
    return result, FindingClassifier().classify(result.findings)


def test_reporter_markdown() -> None:
    result, cls = make_scan()
    md = SecurityReporter().render_markdown(result, cls, title="My scan")
    assert md.startswith("# My scan")
    assert "/fake/root" in md
    assert "Verdict" in md
    assert "| Severity | Count |" in md
    assert "demo.py:2" in md
    assert "(secrets)" in md
    clean = SecurityReporter().render_markdown(ScanResult([], 1, "r"), FindingClassifier().classify([]))
    assert "No findings" in clean
    assert "| clean | 0 |" in clean


def test_reporter_json_and_summary() -> None:
    result, cls = make_scan()
    payload = json.loads(SecurityReporter().render_json(result, cls))
    assert payload["scan"]["scanned_files"] == 1
    assert payload["classification"]["deduped_count"] >= 1
    summary = SecurityReporter().render_summary(cls)
    assert RiskLevel(summary["verdict"]) in RiskLevel
    assert summary["findings"] == cls.finding_count
    assert "risk_score" in summary