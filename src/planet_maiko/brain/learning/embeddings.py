"""Text embeddings for RAG retrieval.

Multi-backend with graceful fallback so this works across environments
without forcing a specific dependency:

  1. Local via `sentence-transformers` (preferred — no API cost, no
     network, code stays local). Default model: bge-small-en-v1.5,
     ~335MB on disk, ~768-dim vectors.
  2. Voyage API (`voyageai` SDK + VOYAGE_API_KEY) — Anthropic's
     recommended embedding partner; voyage-code-2 is code-aware.
  3. OpenAI API (`openai` SDK + OPENAI_API_KEY) — text-embedding-3-small,
     fast and cheap.

The chosen backend is sticky for the process lifetime once selected.
The embedding-model name is returned alongside the vector so callers
can detect when stored embeddings are stale (different model = vectors
not comparable).
"""

import logging
import math
import os

logger = logging.getLogger(__name__)


# Module-level cache. Set once on first call to embed_text().
_backend = None  # "sentence_transformers" | "voyage" | "openai" | None
_model_name = None
_st_model = None  # sentence-transformers Model instance, lazy-loaded
_voyage_client = None
_openai_client = None


def _try_load_sentence_transformers():
    """Best-effort load of a small local embedding model. Returns
    True on success."""
    global _st_model, _model_name
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        logger.debug("[embeddings] sentence-transformers not installed")
        return False
    try:
        # bge-small is the sweet spot for our scale: ~335MB, fast on
        # CPU, comparable quality to OpenAI's text-embedding-3-small
        # for short text. Caches to ~/.cache/huggingface on first use.
        model = SentenceTransformer("BAAI/bge-small-en-v1.5")
        _st_model = model
        _model_name = "BAAI/bge-small-en-v1.5"
        logger.info("[embeddings] loaded local sentence-transformers model bge-small-en-v1.5")
        return True
    except Exception as e:
        logger.warning(f"[embeddings] sentence-transformers load failed: {e}")
        return False


def _try_load_voyage():
    """Initialize Voyage client if VOYAGE_API_KEY is set."""
    global _voyage_client, _model_name
    if not os.environ.get("VOYAGE_API_KEY"):
        return False
    try:
        import voyageai
        _voyage_client = voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"])
        _model_name = "voyage-code-2"
        logger.info("[embeddings] using Voyage voyage-code-2")
        return True
    except ImportError:
        logger.debug("[embeddings] voyageai not installed")
        return False
    except Exception as e:
        logger.warning(f"[embeddings] voyage init failed: {e}")
        return False


def _try_load_openai():
    """Initialize OpenAI client if OPENAI_API_KEY is set."""
    global _openai_client, _model_name
    if not os.environ.get("OPENAI_API_KEY"):
        return False
    try:
        from openai import OpenAI
        _openai_client = OpenAI()  # picks up OPENAI_API_KEY from env
        _model_name = "text-embedding-3-small"
        logger.info("[embeddings] using OpenAI text-embedding-3-small")
        return True
    except ImportError:
        logger.debug("[embeddings] openai not installed")
        return False
    except Exception as e:
        logger.warning(f"[embeddings] openai init failed: {e}")
        return False


def _select_backend():
    """Pick a backend on first use. Order: sentence-transformers (local,
    free), Voyage (Anthropic-aligned), OpenAI (universal fallback).
    Returns the backend name, or None if nothing's available."""
    global _backend
    if _backend:
        return _backend
    for name, loader in (
        ("sentence_transformers", _try_load_sentence_transformers),
        ("voyage", _try_load_voyage),
        ("openai", _try_load_openai),
    ):
        if loader():
            _backend = name
            return name
    logger.warning(
        "[embeddings] No embedding backend available. Install one of: "
        "`pip install sentence-transformers` (local), "
        "`pip install voyageai` (with VOYAGE_API_KEY), or "
        "`pip install openai` (with OPENAI_API_KEY)."
    )
    _backend = None
    return None


def embedding_model_name():
    """Name of the active embedding model. Stored alongside vectors so
    we can detect mismatches when the backend changes — vectors from
    different models aren't directly comparable."""
    if _backend is None:
        _select_backend()
    return _model_name


def embed_text(text):
    """Embed a single string. Returns list[float] or None if no backend
    is available.

    For consistency, callers should always store `embedding_model_name()`
    alongside the vector. At retrieval time, only compare vectors with
    matching model names.
    """
    if not text or not text.strip():
        return None
    backend = _select_backend()
    if backend is None:
        return None
    try:
        if backend == "sentence_transformers":
            vec = _st_model.encode(text, normalize_embeddings=True)
            return [float(x) for x in vec]
        if backend == "voyage":
            res = _voyage_client.embed([text], model=_model_name, input_type="document")
            return list(res.embeddings[0])
        if backend == "openai":
            res = _openai_client.embeddings.create(input=text, model=_model_name)
            return list(res.data[0].embedding)
    except Exception as e:
        logger.warning(f"[embeddings] embed failed via {backend}: {e}")
        return None


def embed_batch(texts):
    """Batch-embed a list of strings. Where the backend supports batched
    requests we use them (faster + cheaper); otherwise we fall back to
    per-item embed_text. Returns a list aligned with the input —
    None for any item that failed."""
    if not texts:
        return []
    backend = _select_backend()
    if backend is None:
        return [None] * len(texts)
    try:
        if backend == "sentence_transformers":
            # Encodes batch as a tensor; way faster than per-item.
            vecs = _st_model.encode(list(texts), normalize_embeddings=True, batch_size=16)
            return [[float(x) for x in v] for v in vecs]
        if backend == "voyage":
            res = _voyage_client.embed(list(texts), model=_model_name, input_type="document")
            return [list(e) for e in res.embeddings]
        if backend == "openai":
            res = _openai_client.embeddings.create(input=list(texts), model=_model_name)
            # OpenAI returns embeddings in input order.
            return [list(d.embedding) for d in res.data]
    except Exception as e:
        logger.warning(f"[embeddings] batch embed failed via {backend}: {e} — falling back to per-item")
        return [embed_text(t) for t in texts]


def cosine_similarity(vec_a, vec_b):
    """Standard cosine similarity. Both vectors must have the same
    dimension (otherwise we'd be comparing across embedding models).
    Returns a float in [-1, 1] — higher = more similar.

    Handles the edge case of either vector being all-zero (returns 0.0)
    so a malformed embedding doesn't propagate NaN through the scorer.
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
