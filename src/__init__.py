"""tyb-zyv-ai — 20-agent framework + interactive terminal UI.

This package layers a multi-agent system and an interactive TUI on top of the
local ZYVO HTTP API. The two original standalone assistants
(``zyvo-telegram.py`` and ``zyvo-voice.py``) remain untouched at the repository
root; everything under ``src/`` is new and importable.

Import layout::

    src.core       → message types, LLM backend protocol, ZYVO HTTP client
    src.agents     → BaseAgent, AgentRegistry, AgentState, AgentMetrics
    src.patterns   → Supervisor, Pipeline, Fanout, Delegation, Blackboard
    src.tui        → terminal UI (Textual app + screens + widgets)
"""

from __future__ import annotations

__version__ = "2.2.0"

__all__ = ["__version__"]
