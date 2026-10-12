"""Per-rule metadata: CWE id, CVSS base score and a remediation hint.

The scanner's rules identify *shapes*; this module attaches the standards
knowledge a human (or the TUI) needs to act on them: which CWE weakness the rule
maps to, a CVSS-style base score, and a short, concrete remediation. It is a
plain lookup keyed by rule name — no network, no dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["RuleMeta", "RULE_METADATA", "metadata_for", "DEFAULT_META"]


@dataclass(frozen=True, slots=True)
class RuleMeta:
    """The standards knowledge attached to one rule."""

    cwe: str
    cvss: float
    remediation: str


#: Fallback used for a rule with no explicit mapping.
DEFAULT_META = RuleMeta(
    cwe="CWE-693",
    cvss=0.0,
    remediation="Review the finding and decide whether the pattern is intended here.",
)

#: rule-name → metadata. Rule names are unique across all eight agents.
RULE_METADATA: dict[str, RuleMeta] = {
    # secrets
    "aws-access-key": RuleMeta("CWE-798", 8.0, "Rotate the key now and load it from a secret manager / env var."),
    "github-token": RuleMeta("CWE-798", 8.0, "Revoke the token, then read it from an env var or secret store."),
    "api-key": RuleMeta("CWE-798", 6.5, "Move the key out of source into configuration / a vault."),
    "private-key-block": RuleMeta("CWE-321", 7.5, "Never commit key material; store it in a secrets file with 600 perms."),
    "plaintext-credential": RuleMeta("CWE-256", 6.5, "Hash secrets with a slow KDF (bcrypt/argon2) or use a vault."),
    # injection
    "code-eval": RuleMeta("CWE-95", 9.0, "Remove eval/exec; use a parser or a strict allow-list instead."),
    "shell-call": RuleMeta("CWE-78", 6.5, "Pass an argument list (no shell); validate every value."),
    "sql-concatenation": RuleMeta("CWE-89", 8.5, "Use parameterised queries / bound placeholders."),
    "shell-true": RuleMeta("CWE-78", 8.0, "Drop shell=True and pass a list of arguments."),
    "command-format": RuleMeta("CWE-78", 8.5, "Build commands with argv lists, never f-strings or concatenation."),
    # traversal
    "path-traversal": RuleMeta("CWE-22", 7.5, "Resolve the path and reject anything outside the allowed root."),
    "absolute-path-open": RuleMeta("CWE-73", 5.5, "Validate the filename against an allow-list before opening."),
    # crypto
    "weak-hash": RuleMeta("CWE-328", 5.5, "Use SHA-256+ (or a password KDF for passwords)."),
    "weak-cipher": RuleMeta("CWE-327", 6.5, "Replace DES/RC4 with AES-GCM or ChaCha20-Poly1305."),
    "weak-rng": RuleMeta("CWE-338", 3.5, "Use secrets / os.urandom for anything security-related."),
    "ecb-mode": RuleMeta("CWE-327", 5.5, "Use an authenticated mode (GCM) instead of ECB."),
    # deserialization
    "pickle": RuleMeta("CWE-502", 8.5, "Never unpickle untrusted data; use JSON or a safe schema."),
    "yaml-load": RuleMeta("CWE-502", 8.5, "Use yaml.safe_load for untrusted input."),
    "marshal": RuleMeta("CWE-502", 8.0, "Treat marshal blobs as untrusted; prefer JSON."),
    "joblib": RuleMeta("CWE-502", 6.5, "Load models only from trusted sources / signed artefacts."),
    "eval-input": RuleMeta("CWE-95", 9.5, "Never eval user input; parse it explicitly."),
    # network
    "http-url": RuleMeta("CWE-319", 3.0, "Use https:// for anything carrying data."),
    "ip-literal": RuleMeta("CWE-693", 2.0, "Prefer a hostname + config over a hardcoded IP."),
    "raw-socket": RuleMeta("CWE-319", 5.0, "Wrap the socket in TLS; verify the peer certificate."),
    "http-client": RuleMeta("CWE-918", 4.0, "Validate/allow-list the URL to avoid SSRF."),
    # dependency
    "unpinned-dependency": RuleMeta("CWE-1104", 5.5, "Pin an exact version and review it on updates."),
    # pii
    "pii-in-log": RuleMeta("CWE-532", 7.0, "Redact personal data before it reaches a log or stdout."),
    "pii-capture": RuleMeta("CWE-359", 5.5, "Minimise stored PII and encrypt it at rest."),
}


def metadata_for(rule: str) -> RuleMeta:
    """The :class:`RuleMeta` for ``rule`` (a safe default when unmapped)."""
    return RULE_METADATA.get(rule, DEFAULT_META)
