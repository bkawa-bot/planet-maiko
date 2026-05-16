"""Text embeddings for RAG retrieval.

Local-only via `sentence-transformers` (no API cost, no per-call
network, code stays local). Default model: bge-small-en-v1.5, ~335MB
on disk, ~768-dim vectors, cached to ~/.cache/huggingface on first
use. If sentence-transformers isn't installed (`pip install -e
".[rag]"`), retrieval is disabled and the rest of Maiko runs fine.

Voyage / OpenAI backends were removed: no one asked for them and the
multi-backend selection + clients were complexity we didn't need.

The embedding-model name is returned alongside vectors so callers can
detect when stored embeddings are stale (different model = vectors
not comparable).
"""

import logging
import math

logger = logging.getLogger(__name__)

# Module-level cache. Set once on first call.
_backend = None  # "sentence_transformers" | None  (None = unavailable)
_model_name = None
_st_model = None  # lazy-loaded SentenceTransformer instance
_load_attempted = False

_MODEL_ID = "BAAI/bge-small-en-v1.5"


def _load_model():
    """Best-effort one-time load of the local embedding model.

    Tries a normal load first so a cold machine still downloads the
    model. If that raises (the usual culprit is huggingface_hub's
    update/etag HEAD request failing — hub API churn, offline, flaky
    network), retry with local_files_only=True, which skips that
    check entirely and uses whatever's already in the HF cache. Sets
    _backend to "sentence_transformers" on success, None otherwise.
    """
    global _st_model, _model_name, _backend, _load_attempted
    if _load_attempted:
        return
    _load_attempted = True

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        logger.debug("[embeddings] sentence-transformers not installed — RAG disabled")
        _backend = None
        return

    try:
        _st_model = SentenceTransformer(_MODEL_ID)
    except Exception as e:
        # The etag/update check is the common failure. Fall back to the
        # cached copy and skip the network entirely.
        logger.warning(
            f"[embeddings] online model load failed ({e}); "
            "retrying from local HF cache (local_files_only)"
        )
        try:
            _st_model = SentenceTransformer(_MODEL_ID, local_files_only=True)
        except Exception as e2:
            logger.warning(
                f"[embeddings] cached load also failed: {e2} — RAG disabled. "
                'Run once with network to populate the cache (pip install -e ".[rag]").'
            )
            _backend = None
            return

    _model_name = _MODEL_ID
    _backend = "sentence_transformers"
    logger.info(f"[embeddings] loaded local model {_MODEL_ID}")


def _ready():
    if not _load_attempted:
        _load_model()
    return _backend is not None


def embedding_model_name():
    """Name of the active embedding model, or None if RAG is disabled.
    Stored alongside vectors so we can detect mismatches if the model
    ever changes — vectors from different models aren't comparable."""
    _ready()
    return _model_name


def embed_text(text):
    """Embed a single string. Returns list[float], or None if RAG is
    unavailable. Callers should store embedding_model_name() alongside
    the vector and only compare vectors with a matching model name."""
    if not text or not text.strip():
        return None
    if not _ready():
        return None
    try:
        vec = _st_model.encode(text, normalize_embeddings=True)
        return [float(x) for x in vec]
    except Exception as e:
        logger.warning(f"[embeddings] embed failed: {e}")
        return None


def embed_batch(texts):
    """Batch-embed a list of strings (encoded as one tensor — much
    faster than per-item). Returns a list aligned with the input;
    None for any item if RAG is unavailable / the batch failed."""
    if not texts:
        return []
    if not _ready():
        return [None] * len(texts)
    try:
        vecs = _st_model.encode(list(texts), normalize_embeddings=True, batch_size=16)
        return [[float(x) for x in v] for v in vecs]
    except Exception as e:
        logger.warning(f"[embeddings] batch embed failed: {e}")
        return [None] * len(texts)


def cosine_similarity(vec_a, vec_b):
    """Standard cosine similarity. Both vectors must have the same
    dimension. Returns a float in [-1, 1] — higher = more similar.
    Returns 0.0 for an all-zero vector so a malformed embedding
    doesn't propagate NaN through the scorer.
    """
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for a, b in zip(vec_a, vec_b):
        dot += a * b
        norm_a += a * a
        norm_b += b * b
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))
