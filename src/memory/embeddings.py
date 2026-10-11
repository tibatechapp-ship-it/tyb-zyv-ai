"""Embeddings for semantic search — dependency-free, deterministic.

ChromaDB / FAISS / sentence-transformers do not install on a phone (Termux,
Python 3.14), so the default embedder is a **feature-hashing** model that runs
anywhere with the standard library:

* every word, plus every character *n*-gram inside it, is hashed into a fixed
  vector with a signed hash (the "hashing trick");
* the vector is L2-normalised, so :func:`cosine` gives a clean similarity.

This catches near-matches that plain token overlap misses — typos, stems and
substrings — while staying fast and fully offline. Any real model can be dropped
in instead: anything matching the :class:`Embedder` protocol plugs straight into
:class:`~src.memory.semantic.SemanticMemory`.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from typing import Protocol

__all__ = ["Embedder", "HashingEmbedder", "cosine", "make_embedder"]

_WORD = re.compile(r"\w+", re.UNICODE)


class Embedder(Protocol):
    """Anything that maps text to a dense vector (e.g. a model client)."""

    def __call__(self, text: str) -> Sequence[float]: ...


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two equal-length vectors (0.0 on mismatch/zero)."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _signed_index(feature: str, dim: int) -> tuple[int, int]:
    """Map a feature to ``(bucket, sign)`` using a *stable* hash.

    ``hashlib`` is used (not :func:`hash`) because Python salts string hashing
    per process — we need the same vector across runs and machines.
    """
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    sign = 1 if (value >> 63) & 1 else -1
    return value % dim, sign


class HashingEmbedder:
    """Deterministic feature-hashing embedder over word + character *n*-grams."""

    def __init__(self, dim: int = 512, *, ngram: int = 3) -> None:
        if dim <= 0:
            raise ValueError("dim must be positive")
        if ngram <= 0:
            raise ValueError("ngram must be positive")
        self.dim = dim
        self.ngram = ngram

    def features(self, text: str) -> set[str]:
        """The feature set for ``text``: words plus their character n-grams."""
        words = _WORD.findall(text.lower())
        features = set(words)
        for word in words:
            if len(word) < self.ngram:
                continue
            for start in range(len(word) - self.ngram + 1):
                features.add(word[start : start + self.ngram])
        return features

    def __call__(self, text: str) -> list[float]:
        """Return the L2-normalised hashing vector for ``text``."""
        vector = [0.0] * self.dim
        for feature in self.features(text):
            bucket, sign = _signed_index(feature, self.dim)
            vector[bucket] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            return vector
        return [value / norm for value in vector]


def make_embedder(spec: Embedder | str | None) -> Embedder | None:
    """Resolve an embedder spec into a callable.

    * ``None`` -> ``None`` (callers fall back to lexical matching);
    * an existing callable -> returned unchanged;
    * a string name -> a built-in embedder (``"hashing"``, ``"hash"``,
      ``"default"`` or ``"builtin"``).
    """
    if spec is None:
        return None
    if isinstance(spec, str):
        name = spec.strip().lower()
        if name in {"hashing", "hash", "default", "builtin"}:
            return HashingEmbedder()
        raise ValueError(f"unknown embedder name: {spec!r}")
    if callable(spec):
        return spec
    raise TypeError(f"embedder must be a callable, name or None, not {type(spec)!r}")
