"""Tests for the dependency-free embedder (Phase 2).

Everything here is deterministic and offline: the :class:`HashingEmbedder` uses a
stable hash, so the same text always yields the same vector, even across runs.
"""

from __future__ import annotations

import math

import pytest

from src.memory.embeddings import Embedder, HashingEmbedder, cosine, make_embedder


def test_embedder_dimension_matches_config():
    embedder = HashingEmbedder(dim=64)
    assert len(embedder("hello world")) == 64


def test_embedder_output_is_l2_normalised():
    embedder = HashingEmbedder(dim=128)
    vector = embedder("the quick brown fox jumps over the lazy dog")
    norm = math.sqrt(sum(value * value for value in vector))
    assert norm == pytest.approx(1.0)


def test_embedder_is_deterministic_across_instances():
    assert HashingEmbedder()("deploy the app") == HashingEmbedder()("deploy the app")


def test_embedder_empty_text_is_zero_vector():
    embedder = HashingEmbedder(dim=32)
    vector = embedder("")
    assert vector == [0.0] * 32


def test_features_include_words_and_character_ngrams():
    embedder = HashingEmbedder(dim=64, ngram=3)
    features = embedder.features("abcd")
    assert "abcd" in features          # the whole word
    assert "abc" in features and "bcd" in features   # its trigrams


def test_features_skip_words_shorter_than_ngram():
    embedder = HashingEmbedder(dim=64, ngram=5)
    # no 5-grams inside "ab", but the whole word is still a feature
    assert embedder.features("ab") == {"ab"}


def test_similar_text_scores_above_dissimilar():
    embedder = HashingEmbedder()
    base = embedder("ship the release to github pages")
    near = embedder("deploy the release to github")
    far = embedder("feed the cat and water the plants")
    assert cosine(base, near) > cosine(base, far)


def test_embedder_tolerates_typos():
    embedder = HashingEmbedder()
    correct = embedder("deployment pipeline")
    typo = embedder("deploymant pipeline")
    unrelated = embedder("banana smoothie recipe")
    assert cosine(correct, typo) > cosine(correct, unrelated)


def test_embedder_rejects_bad_configuration():
    with pytest.raises(ValueError):
        HashingEmbedder(dim=0)
    with pytest.raises(ValueError):
        HashingEmbedder(ngram=0)


def test_cosine_full_partial_and_edge_cases():
    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine([1.0], [1.0, 2.0]) == 0.0
    assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


# --------------------------------------------------------------------------- #
# make_embedder factory
# --------------------------------------------------------------------------- #
def test_make_embedder_none_returns_none():
    assert make_embedder(None) is None


@pytest.mark.parametrize("name", ["hashing", "HASHING", "hash", "default", "builtin"])
def test_make_embedder_builds_hashing_by_name(name):
    embedder = make_embedder(name)
    assert isinstance(embedder, HashingEmbedder)


def test_make_embedder_rejects_unknown_name():
    with pytest.raises(ValueError):
        make_embedder("nope")


def test_make_embedder_passes_callable_through():
    def custom(text: str) -> list[float]:
        return [float(len(text))]

    assert make_embedder(custom) is custom


def test_make_embedder_rejects_bad_type():
    with pytest.raises(TypeError):
        make_embedder(123)  # type: ignore[arg-type]


def test_embedder_protocol_is_runtime_satisfiable():
    embedder: Embedder = HashingEmbedder()
    assert isinstance(embedder("x"), list)
