"""Bonus - golden-contract snapshot tests (syrupy).

One golden file pins the whole 20-agent public contract: name, group, tools,
capabilities and whether a persona is set. Any accidental drift (a renamed
agent, a dropped tool, a widened permission) shows up as a readable diff in
review instead of shipping silently.
"""

from __future__ import annotations

import json

from src.agents import AGENT_CLASSES


def _catalog() -> list[dict[str, object]]:
    """Return the agent contract as a deterministic, sortable structure."""
    return [
        {
            "name": cls.name,
            "group": cls.group,
            "tools": sorted(cls.tools),
            "permissions": sorted(cls.permissions),
            "has_persona": bool(cls.system_prompt.strip()),
        }
        for cls in sorted(AGENT_CLASSES, key=lambda c: c.name)
    ]


def test_agent_catalog_matches_the_golden_snapshot(snapshot) -> None:
    """Lock the 20-agent contract into a single reviewable golden file."""
    assert json.dumps(_catalog(), indent=2, ensure_ascii=False) == snapshot
