"""Core infrastructure: message types, LLM backend protocol, ZYVO HTTP client,
the reliability layer (retries/cache/queue/fallback) and the process watchdog.
"""

from __future__ import annotations

from src.core.backend import EchoBackend, LLMBackend
from src.core.context_manager import (
    CompactionResult,
    ContextEntry,
    ContextManager,
    estimate_tokens,
)
from src.core.logging_utils import JsonFormatter, setup_json_logging
from src.core.model_router import (
    DEFAULT_PROFILES,
    ModelProfile,
    ModelRouter,
    ModelTier,
    RoutingDecision,
)
from src.core.resilience import (
    BackendUnavailable,
    CommandQueue,
    FallbackBackend,
    RateLimiter,
    ResilientBackend,
    ResponseCache,
    RetryPolicy,
    retry_call,
)
from src.core.types import AgentResult, Message
from src.core.watchdog import Heartbeat, Watchdog
from src.core.zyvo_client import ZyvoBackend, ZyvoClient, ZyvoError

__all__ = [
    "EchoBackend",
    "LLMBackend",
    "Message",
    "AgentResult",
    "CompactionResult",
    "ContextEntry",
    "ContextManager",
    "estimate_tokens",
    "DEFAULT_PROFILES",
    "ModelProfile",
    "ModelRouter",
    "ModelTier",
    "RoutingDecision",
    "ZyvoBackend",
    "ZyvoClient",
    "ZyvoError",
    "JsonFormatter",
    "setup_json_logging",
    "RetryPolicy",
    "retry_call",
    "RateLimiter",
    "ResponseCache",
    "CommandQueue",
    "FallbackBackend",
    "ResilientBackend",
    "BackendUnavailable",
    "Watchdog",
    "Heartbeat",
]
