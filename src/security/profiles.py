"""Scan profiles — named subsets of the eight agents for a target type.

A profile answers "what should we look for here?". ``full`` runs everything;
``secrets`` runs only the credential agent; ``dependencies`` only the manifest
agent; ``web`` the web-facing set; ``apk`` the mobile-app set. Profiles are the
seam the TUI uses to offer one-line "scan APK / web / dependencies / secrets".
"""

from __future__ import annotations

from dataclasses import dataclass

from src.security.agents import SECURITY_AGENTS

__all__ = ["ScanProfile", "PROFILES", "PROFILE_ORDER", "get_profile"]

#: Every agent name, in scan order.
_ALL: tuple[str, ...] = tuple(agent.name for agent in SECURITY_AGENTS)


@dataclass(frozen=True, slots=True)
class ScanProfile:
    """A named selection of agents to run against a target."""

    name: str
    description: str
    agents: frozenset[str]


PROFILES: dict[str, ScanProfile] = {
    "full": ScanProfile(
        "full",
        "All eight agents — secrets, injection, traversal, crypto, "
        "deserialization, network, dependency, pii.",
        frozenset(_ALL),
    ),
    "secrets": ScanProfile(
        "secrets",
        "Credential and key material only.",
        frozenset({"secrets"}),
    ),
    "dependencies": ScanProfile(
        "dependencies",
        "Manifest / requirement pinning only.",
        frozenset({"dependency"}),
    ),
    "web": ScanProfile(
        "web",
        "Web-facing checks — injection, secrets, traversal, network, pii.",
        frozenset({"injection", "secrets", "traversal", "network", "pii"}),
    ),
    "apk": ScanProfile(
        "apk",
        "Mobile-app checks — secrets, network, traversal, injection, crypto, pii.",
        frozenset({"secrets", "network", "traversal", "injection", "crypto", "pii"}),
    ),
}

#: Profile names in the order the UI cycles through them.
PROFILE_ORDER: tuple[str, ...] = ("full", "secrets", "dependencies", "web", "apk")


def get_profile(name: str) -> ScanProfile:
    """The profile called ``name`` (raises ``KeyError`` when unknown)."""
    return PROFILES[name]
