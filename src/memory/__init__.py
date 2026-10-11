"""The agent memory system.

Four complementary stores over one SQLite database, fronted by
:class:`MemoryManager`:

* :class:`EpisodicMemory` — *what happened* (timestamped agent episodes);
* :class:`SemanticMemory` — *what is true* (durable facts);
* :class:`ProceduralMemory` — *how to do things* (named skills);
* :class:`EmotionalMemory` — *how the user feels* (tone, preferences, satisfaction).

The dependency-free default works offline on a phone; an optional embedder can be
plugged into :class:`SemanticMemory` for vector search.
"""

from __future__ import annotations

from src.memory.base import (
    Clock,
    MemoryKind,
    MemoryRecord,
    MemoryStore,
    connect,
    default_memory_path,
    utc_iso,
)
from src.memory.embeddings import Embedder, HashingEmbedder, cosine, make_embedder
from src.memory.emotional import EmotionalMemory, Tone, analyze_tone, classify
from src.memory.episodic import EpisodicMemory, EventKind
from src.memory.manager import MemoryManager
from src.memory.procedural import ProceduralMemory, Skill, SkillsRegistry
from src.memory.semantic import SemanticMemory

__all__ = [
    "Clock",
    "Embedder",
    "EmotionalMemory",
    "EpisodicMemory",
    "EventKind",
    "HashingEmbedder",
    "MemoryKind",
    "MemoryManager",
    "MemoryRecord",
    "MemoryStore",
    "ProceduralMemory",
    "SemanticMemory",
    "Skill",
    "SkillsRegistry",
    "Tone",
    "analyze_tone",
    "classify",
    "connect",
    "cosine",
    "default_memory_path",
    "make_embedder",
    "utc_iso",
]
