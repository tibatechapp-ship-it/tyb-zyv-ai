"""The eight specialized security agents (Phase 9).

Each :class:`SecurityAgent` owns one category of static-analysis rule, expressed
as plain regular expressions. Scanning is fully offline and deterministic: a
regex that matches yields one :class:`~src.security.types.Finding` per line
(with its line number), nothing more.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

from src.security.types import Finding, Severity

#: A compiled pattern plus its human name and default severity.
Pattern = tuple[re.Pattern[str], str, Severity]


class SecurityAgent:
    """Base class — one category of checks, one name, one description."""

    name: ClassVar[str] = "base"
    description: ClassVar[str] = ""
    #: (compiled, rule_name, severity, message-template). Message may contain
    #: ``{rule}`` which is replaced by the rule name.
    patterns: ClassVar[tuple[Pattern, ...]] = ()
    reporter: ClassVar[str] = "generic"

    def path_filter(self, path: Path) -> bool:
        """Whether this agent cares about the file at ``path`` (default: all)."""
        return True

    def scan(self, text: str, path: str) -> list[Finding]:
        """Run every pattern over ``text`` and return findings with line numbers."""
        findings: list[Finding] = []
        for compiled, rule, severity in self.patterns:
            for match in compiled.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                message = self._message(rule).format(rule=rule)
                findings.append(
                    Finding(
                        agent=self.name,
                        rule=rule,
                        path=path,
                        line=line,
                        severity=severity,
                        message=message,
                        snippet=match.group(0)[:80],
                    )
                )
        return findings

    def matches(self, file_path: Path) -> bool:
        """Whether this agent cares about ``file_path``."""
        return self.path_filter(file_path)

    @classmethod
    def _message(cls, rule: str) -> str:
        return f"{rule}: potential issue detected"


class _SecretsAgent(SecurityAgent):
    name = "secrets"
    description = "Hardcoded credentials, tokens and private keys."
    patterns: ClassVar[tuple[Pattern, ...]] = (
        (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "aws-access-key", Severity.HIGH),
        (re.compile(r"\bghp_[A-Za-z0-9]{36}\b"), "github-token", Severity.HIGH),
        (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "api-key", Severity.MEDIUM),
        (
            re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
            "private-key-block",
            Severity.HIGH,
        ),
        (
            re.compile(
                r"\b(password|passwd|pwd|secret|token)\s*=\s*"
                r"['\"]([^'\"]{6,})['\"]"
            ),
            "plaintext-credential",
            Severity.MEDIUM,
        ),
    )

    @classmethod
    def _message(cls, rule: str) -> str:
        return {
            "aws-access-key": "AWS access key literal — rotate and move to a vault",
            "github-token": "GitHub token literal — revoke and use a secret store",
            "api-key": "API key literal in source",
            "private-key-block": "embedded private key material",
            "plaintext-credential": "credential assigned as a literal",
        }[rule]


class _InjectionAgent(SecurityAgent):
    name = "injection"
    description = "Code / shell / SQL injection sinks."
    patterns: ClassVar[tuple[Pattern, ...]] = (
        (re.compile(r"\b(exec|eval)\s*\("), "code-eval", Severity.HIGH),
        (
            re.compile(r"\b(subprocess\.(?:call|run|Popen|check_output)|os\.system)\s*\("),
            "shell-call",
            Severity.MEDIUM,
        ),
        (
            re.compile(r"\b(?:cursor|conn|db)\.execute\(\s*f?[\"'](?:.*(\{|\+|%))"),
            "sql-concatenation",
            Severity.HIGH,
        ),
        (re.compile(r"subprocess\.(?:call|run|Popen)\([^)]*shell\s*=\s*True"), "shell-true", Severity.HIGH),
        (
            re.compile(r"\b(os\.system|popen|system)\s*\(\s*f?[\"'].*(\{|\+|%)"),
            "command-format",
            Severity.HIGH,
        ),
    )

    @classmethod
    def _message(cls, rule: str) -> str:
        return {
            "code-eval": "dynamic code execution (eval/exec)",
            "shell-call": "subprocess / shell invocation",
            "sql-concatenation": "query string built by concatenation — possible SQL injection",
            "shell-true": "subprocess with shell=True",
            "command-format": "command built from interpolated/concatenated input",
        }[rule]


class _TraversalAgent(SecurityAgent):
    name = "traversal"
    description = "Path traversal and unsafe file access."
    patterns: ClassVar[tuple[Pattern, ...]] = (
        (
            re.compile(r"[\"'][^\"']*(?:\.\./|\.\.\\)[^\"']*[\"']"),
            "path-traversal",
            Severity.HIGH,
        ),
        (
            re.compile(r"\bopen\(\s*[\"'](?:/|~|\.\.)[^)]*\)"),
            "absolute-path-open",
            Severity.MEDIUM,
        ),
    )

    @classmethod
    def _message(cls, rule: str) -> str:
        return {
            "path-traversal": "path literal containing ../ traversal",
            "absolute-path-open": "open() on an absolute/relative-to-root path",
        }[rule]


class _CryptoAgent(SecurityAgent):
    name = "crypto"
    description = "Weak or broken cryptography."
    patterns: ClassVar[tuple[Pattern, ...]] = (
        (
            re.compile(r"\b(hashlib\.|hmac\.|Cryptodome\.|cryptography\.)?(md5|sha1)\s*\("),
            "weak-hash",
            Severity.MEDIUM,
        ),
        (re.compile(r"\b(des|rc4|arc4|des3)\b(?![A-Za-z])", re.IGNORECASE), "weak-cipher", Severity.MEDIUM),
        (re.compile(r"\brandom\.(random|randint|uniform|choice)\b"), "weak-rng", Severity.LOW),
        (re.compile(r"\bcipher\.ECB\b|AES\.MODE_ECB\b"), "ecb-mode", Severity.MEDIUM),
    )

    @classmethod
    def _message(cls, rule: str) -> str:
        return {
            "weak-hash": "broken/weak hash function (md5/sha1)",
            "weak-cipher": "legacy cipher (DES/RC4) — treat as broken",
            "weak-rng": "non-cryptographic random API",
            "ecb-mode": "AES in ECB mode — leaks structure",
        }[rule]


class _DeserializationAgent(SecurityAgent):
    name = "deserialization"
    description = "Unsafe deserialization sinks."
    patterns: ClassVar[tuple[Pattern, ...]] = (
        (re.compile(r"\bpickle\.(loads?|dumps?)\s*\("), "pickle", Severity.HIGH),
        (re.compile(r"\byaml\.load\s*\((?![^)]*SafeLoader)"), "yaml-load", Severity.HIGH),
        (re.compile(r"\bmarshal\.loads?\s*\("), "marshal", Severity.HIGH),
        (re.compile(r"\bjoblib\.(load|dump)\s*\("), "joblib", Severity.MEDIUM),
        (re.compile(r"\beval\s*\(\s*(input|request\.args|request\.form)"), "eval-input", Severity.CRITICAL),
    )

    @classmethod
    def _message(cls, rule: str) -> str:
        return {
            "pickle": "pickle deserialization — arbitrary code execution risk",
            "yaml-load": "yaml.load without SafeLoader — arbitrary code execution risk",
            "marshal": "marshal deserialization of untrusted data",
            "joblib": "joblib load/dump of untrusted data",
            "eval-input": "eval on user-supplied input",
        }[rule]


class _NetworkAgent(SecurityAgent):
    name = "network"
    description = "Insecure transports, hardcoded endpoints, sockets."
    patterns: ClassVar[tuple[Pattern, ...]] = (
        (re.compile(r"\bhttps?://"), "http-url", Severity.INFO),
        (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "ip-literal", Severity.INFO),
        (
            re.compile(r"\bsocket\.(?:connect|create_connection)\s*\("),
            "raw-socket",
            Severity.MEDIUM,
        ),
        (re.compile(r"\brequests\.(get|post|put|delete|patch)\s*\("), "http-client", Severity.INFO),
    )

    @classmethod
    def _message(cls, rule: str) -> str:
        return {
            "http-url": "URL literal — check for plain http://",
            "ip-literal": "hardcoded IP address",
            "raw-socket": "raw socket connection",
            "http-client": "outbound HTTP request — verify the URL source is trusted",
        }[rule]


class _DependencyAgent(SecurityAgent):
    name = "dependency"
    description = "Unpinned or unbounded dependencies."

    def path_filter(self, path: Path) -> bool:
        return path.name.startswith("requirements") or path.name in (
            "pyproject.toml",
            "Pipfile",
            "Pipfile.lock",
            "poetry.lock",
        )

    patterns: ClassVar[tuple[Pattern, ...]] = (
        (
            re.compile(
                r"^\s*([a-zA-Z0-9_.\-]+(?:\[[^\]]*\])?)\s*(?:#.*)?$",
                re.MULTILINE,
            ),
            "unpinned-dependency",
            Severity.MEDIUM,
        ),
    )

    @classmethod
    def _message(cls, rule: str) -> str:
        return "requirement without a version pin — supply-chain risk"


class _PiiAgent(SecurityAgent):
    name = "pii"
    description = "Personal data written to logs or output."
    patterns: ClassVar[tuple[Pattern, ...]] = (
        (
            re.compile(
                r"(log(?:ger)?\.(?:info|debug|warning|error|critical)|\bprint)\s*\("
                r"(?=[^)]*?(?:[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
                r"|(?:\+?\d[\d\s\-()]{8,}\d)))"
            ),
            "pii-in-log",
            Severity.HIGH,
        ),
        (
            re.compile(
                r"(?:email|phone|ssn|national_id|passport|credit_card|cvv)\s*=\s*"
                r"(?:request\.|input\(|f[\"'])",
                re.IGNORECASE,
            ),
            "pii-capture",
            Severity.MEDIUM,
        ),
    )

    @classmethod
    def _message(cls, rule: str) -> str:
        return {
            "pii-in-log": "possible personal data (email/phone) written to a log/print",
            "pii-capture": "PII-looking value read from user input",
        }[rule]


#: The canonical eight agents, in scan order.
SECURITY_AGENTS: tuple[type[SecurityAgent], ...] = (
    _SecretsAgent,
    _InjectionAgent,
    _TraversalAgent,
    _CryptoAgent,
    _DeserializationAgent,
    _NetworkAgent,
    _DependencyAgent,
    _PiiAgent,
)