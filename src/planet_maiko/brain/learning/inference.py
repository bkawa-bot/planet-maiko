"""LoRA inference — `review_code` (single file) and `review_diff`
(per-hunk over a diff) plus the MLX session cache that keeps weights
in-process between requests.

Originally lived inside trainer.py — extracted because training
orchestration and inference share almost nothing at runtime; an
import that just wants `review_code` was pulling MLX training imports
into memory unnecessarily.

Shared helpers (chat-prompt builder, base-model resolver, system
prompt) come from .trainer; the inference module is the only place
where MLX session caching lives.
"""

import json
import logging
import os
import re
import subprocess
import sys

from planet_maiko.brain.learning.trainer import (  # noqa: F401  -- re-exposed for back-compat
    SYSTEM_PROMPT,
    _build_chat_prompt,
    _clean_subprocess_env,
    _model_family,
    _resolve_base_model_for_adapter,
    get_backend,
)

logger = logging.getLogger(__name__)




def review_code(code, repo=None, adapter_path=None, file_path=None):
    """Run code through a trained LoRA adapter and return the review.

    Args:
        code: the code to review
        repo: look up adapter from config.lora.models_by_repo[repo]
        adapter_path: explicit adapter path (overrides repo lookup)
        file_path: optional file path for context

    Returns:
        dict with {output, adapter_path, success}
    """
    backend = get_backend()
    if not backend:
        return {"success": False, "error": "No backend available"}

    if not adapter_path and repo:
        try:
            from planet_maiko.config import load_config
            adapter_path = (load_config().get("lora") or {}).get("models_by_repo", {}).get(repo)
        except Exception:
            pass

    if not adapter_path:
        # Fall back to most recent adapter
        from planet_maiko.paths import data_dir
        models_dir = os.path.join(data_dir(), "models")
        if os.path.isdir(models_dir):
            adapters = sorted(os.listdir(models_dir), reverse=True)
            if adapters:
                adapter_path = os.path.join(models_dir, adapters[0])

    if not adapter_path or not os.path.isdir(adapter_path):
        return {"success": False, "error": "No trained adapter found. Run training first."}

    # Build prompt
    context_parts = []
    if file_path:
        context_parts.append(f"File: {file_path}")
    context_parts.append(f"```\n{code}\n```")
    prompt_text = "\n".join(context_parts)

    # Use the base_model the adapter was trained against — Llama and
    # Qwen weights aren't interchangeable; loading the wrong base
    # silently produces garbage output.
    config = {
        **DEFAULT_TRAINING_CONFIG,
        "base_model": _resolve_base_model_for_adapter(adapter_path),
    }

    if backend == "mlx":
        return _infer_mlx(prompt_text, adapter_path, config)
    else:
        return {"success": False, "error": f"Inference not yet supported on {backend}"}


def review_batch(files, repo=None, adapter_path=None):
    """Review multiple files in a single model load.

    Args:
        files: list of {"code": str, "file_path": str} dicts
        repo: look up adapter via config.lora.models_by_repo
        adapter_path: explicit adapter path

    Returns:
        dict with {results: [{file_path, output}], success}
    """
    if not files:
        return {"success": True, "results": []}

    # Build combined prompt
    parts = []
    for i, f in enumerate(files):
        header = f"--- File {i}: {f.get('file_path', 'unknown')} ---"
        parts.append(f"{header}\n```\n{f['code']}\n```")

    combined = "\n\n".join(parts)
    combined += "\n\nFor EACH file above, respond with the file number and either PASS or VIOLATION with a description. One line per file."

    result = review_code(
        code=combined,
        repo=repo,
        adapter_path=adapter_path,
    )

    if not result.get("success"):
        return result

    # Parse per-file results from output
    output = result.get("output", "")
    results = []
    for i, f in enumerate(files):
        # Find the line for this file in the output
        file_result = "PASS"  # default
        for line in output.split("\n"):
            if f"File {i}" in line or f.get("file_path", "") in line:
                if "VIOLATION" in line:
                    file_result = line.strip()
                break
        results.append({
            "file_path": f.get("file_path", "unknown"),
            "output": file_result,
        })

    return {"success": True, "results": results, "adapter_path": result.get("adapter_path")}


def _split_diff_into_hunks(diff_text):
    """Split a unified diff into (file_path, hunk_body) tuples.

    Each hunk_body keeps the @@ header line and the changed lines —
    enough context for the LoRA to classify, sized like the chunks
    the LoRA was trained on (one focused change per pair). File-
    level metadata like `diff --git`, `index`, `---` lines are
    skipped — they don't help classification.

    For inputs that aren't unified diffs (raw code, or a single hunk
    without an @@ header), returns a single tuple `("", diff_text)`
    so callers can still pass the whole thing through review_code.
    """
    if not diff_text or not diff_text.strip():
        return []
    if "@@" not in diff_text:
        # Not a unified diff — treat as a single chunk.
        return [("", diff_text)]

    chunks = []
    current_file = ""
    current_hunk_lines = []
    in_hunk = False

    def flush():
        if current_hunk_lines:
            chunks.append((current_file, "\n".join(current_hunk_lines)))

    for line in diff_text.split("\n"):
        # `+++ b/path` (or `+++ /dev/null`) is the canonical "after"
        # path for a hunk in a unified diff. Drop the b/ prefix to
        # get the real path.
        if line.startswith("+++ "):
            flush()
            current_hunk_lines = []
            in_hunk = False
            path = line[4:].strip()
            if path.startswith("b/"):
                path = path[2:]
            if path != "/dev/null":
                current_file = path
            continue

        # Diff metadata lines we don't want in the LoRA's input
        if (
            line.startswith("diff --git ")
            or line.startswith("index ")
            or line.startswith("--- ")
            or line.startswith("similarity ")
            or line.startswith("rename ")
            or line.startswith("new file mode")
            or line.startswith("deleted file mode")
            or line.startswith("Binary files ")
        ):
            flush()
            current_hunk_lines = []
            in_hunk = False
            continue

        if line.startswith("@@"):
            # New hunk — flush the previous one and start fresh
            flush()
            current_hunk_lines = [line]
            in_hunk = True
            continue

        if in_hunk:
            current_hunk_lines.append(line)

    flush()
    # Drop trivially-tiny hunks — anything under 30 chars is just
    # noise (a single context line, blank lines, etc.) and the LoRA
    # can't do anything useful with it.
    return [(p, h) for (p, h) in chunks if len(h.strip()) >= 30]


def _parse_violation_output(output):
    """Pull (rule_text, category) out of a `VIOLATION: [cat] rule` line.
    Falls back to (whole_output, "") when the format is unexpected so
    aggregation still has something to dedup on."""
    import re as _re
    output = output or ""
    m = _re.search(r"VIOLATION:\s*\[([^\]]+)\]\s*(.+)", output, _re.IGNORECASE)
    if m:
        return m.group(2).strip(), m.group(1).strip()
    m = _re.search(r"VIOLATION:\s*(.+)", output, _re.IGNORECASE)
    if m:
        return m.group(1).strip(), ""
    return output.strip(), ""


def review_diff(diff_text, repo=None, adapter_path=None, file_path=None):
    """Review a (potentially multi-hunk) diff by chunking per hunk
    and aggregating the verdicts.

    Per-hunk inference is much closer to the LoRA's training
    distribution — each rules-*.jsonl pair was a single small chunk,
    and the model learned `(small focused chunk) → verdict` rather
    than `(whole PR) → verdict`. Feeding it whole diffs is an out-
    of-distribution input that dilutes attention across irrelevant
    code; per-hunk feeds the model what it was trained on.

    The persistent mlx session means the model loads once and every
    per-hunk call is just a forward pass (~1-2s), so a 5-hunk diff
    costs ~5-10s of LoRA wall-clock — acceptable given the quality
    win, and still far cheaper than any cloud-API alternative.

    Returns a dict with:
        success: bool
        verdict: "VIOLATION" | "PASS"
        violations: list of unique violations (deduped by rule text)
            Each: {rule, category, raw, hunk_index, file_path}
        hunks_reviewed: int (total chunks fed to the LoRA)
        hunks_flagged: int
        per_hunk: list of every per-hunk result, for debugging
        output: str — joined violation text (for callers expecting
            the same shape as review_code)
        adapter_path: str (echoed from the underlying review_code)
    """
    chunks = _split_diff_into_hunks(diff_text)
    if not chunks:
        return {
            "success": False,
            "error": "no reviewable hunks in diff",
            "verdict": "PASS",
            "violations": [],
            "hunks_reviewed": 0,
            "hunks_flagged": 0,
            "per_hunk": [],
            "output": "",
        }

    violations = []
    seen_rule_keys = set()
    per_hunk = []
    used_adapter_path = None
    errored = 0

    for i, (chunk_path, hunk_body) in enumerate(chunks):
        # Per-chunk file path: prefer the path parsed from the diff;
        # fall back to the function arg (for inputs without diff
        # metadata, e.g. raw code that the caller passed through).
        chunk_file = chunk_path or (file_path or "")

        result = review_code(
            code=hunk_body,
            repo=repo,
            adapter_path=adapter_path,
            file_path=chunk_file,
        )

        if not used_adapter_path and result.get("adapter_path"):
            used_adapter_path = result.get("adapter_path")

        if not result.get("success"):
            errored += 1
            per_hunk.append({
                "hunk_index": i,
                "file_path": chunk_file,
                "verdict": "ERROR",
                "error": result.get("error", ""),
            })
            continue

        out = (result.get("output") or "").strip()
        verdict = "VIOLATION" if "VIOLATION" in out.upper() else "PASS"
        per_hunk.append({
            "hunk_index": i,
            "file_path": chunk_file,
            "verdict": verdict,
            "raw": out,
        })

        if verdict == "VIOLATION":
            rule_text, category = _parse_violation_output(out)
            # Dedup key: same rule text flagged across multiple hunks
            # collapses into a single violation entry. Strip and
            # truncate to absorb minor wording variations.
            key = (rule_text or out).strip().lower()[:120]
            if key not in seen_rule_keys:
                seen_rule_keys.add(key)
                violations.append({
                    "rule": rule_text,
                    "category": category,
                    "raw": out,
                    "hunk_index": i,
                    "file_path": chunk_file,
                })

    overall_verdict = "VIOLATION" if violations else "PASS"
    output_text = "\n".join(v["raw"] for v in violations) if violations else "PASS"
    hunks_flagged = sum(1 for h in per_hunk if h["verdict"] == "VIOLATION")

    return {
        "success": True,
        "verdict": overall_verdict,
        "violations": violations,
        "hunks_reviewed": len(chunks),
        "hunks_flagged": hunks_flagged,
        "per_hunk": per_hunk,
        "errored_hunks": errored,
        "output": output_text,
        "adapter_path": used_adapter_path,
    }


# Module-level cache for the persistent mlx-lm inference session.
# Loading the base model + adapter is ~20-60s; per-inference cost
# drops from ~30s (subprocess startup) to ~1-2s (just the forward
# pass) when we keep the model resident. Used by _infer_mlx below.
#
# Cache shape: {"base_model": str, "adapter_path": str|None,
#               "model": MLXModel, "tokenizer": MLXTokenizer}
_mlx_session = None


def _get_mlx_session(base_model, adapter_path):
    """Return cached (model, tokenizer); load on miss.

    Cache key is (base_model, adapter_path) — when either changes,
    we replace the session. mlx-lm doesn't support hot-swapping
    adapters, so an adapter change costs a full reload (still
    cheap if you stay on one adapter for a whole eval run).

    Returns None when mlx_lm isn't importable or load fails;
    callers should fall back to the subprocess path.
    """
    global _mlx_session

    if (
        _mlx_session is not None
        and _mlx_session["base_model"] == base_model
        and _mlx_session["adapter_path"] == adapter_path
    ):
        return _mlx_session["model"], _mlx_session["tokenizer"]

    try:
        from mlx_lm import load
    except ImportError:
        return None

    logger.info(
        f"[mlx-infer] Loading {base_model}"
        + (f" + adapter {adapter_path}" if adapter_path else "")
    )
    try:
        if adapter_path:
            model, tokenizer = load(base_model, adapter_path=adapter_path)
        else:
            model, tokenizer = load(base_model)
    except Exception as e:
        logger.warning(f"[mlx-infer] Could not load in-process session: {e}")
        return None

    _mlx_session = {
        "base_model": base_model,
        "adapter_path": adapter_path,
        "model": model,
        "tokenizer": tokenizer,
    }
    return model, tokenizer


def _infer_mlx(prompt_text, adapter_path, config):
    """Run inference with MLX.

    Fast path: the persistent in-process session above. Loads the
    base model + adapter once per (base, adapter) combo, then every
    subsequent call is a single forward pass — turning per-call
    cost from ~30s to ~1-2s. Critical for `maiko eval` and
    `maiko eval-prs`, which loop through hundreds of inferences.

    Slow path (fallback): subprocess `python -m mlx_lm.generate`.
    Used when mlx_lm Python API isn't importable (different venv,
    rare). One model load per call; ~30s per inference.
    """
    base_model = config["base_model"]
    chat_prompt = _build_chat_prompt(base_model, SYSTEM_PROMPT, prompt_text)

    # Fast path
    session = _get_mlx_session(base_model, adapter_path)
    if session is not None:
        model, tokenizer = session
        try:
            from mlx_lm import generate
            try:
                output = generate(
                    model, tokenizer,
                    prompt=chat_prompt,
                    max_tokens=512,
                    verbose=False,
                )
            except TypeError:
                # Older/newer mlx_lm versions have slightly different
                # kwargs (e.g. `temp` was renamed; `verbose` not always
                # accepted). Retry with the minimal signature.
                output = generate(model, tokenizer, prompt=chat_prompt, max_tokens=512)
            # Some versions return a GenerationResponse-like object
            # rather than a raw string — coerce.
            if not isinstance(output, str):
                output = getattr(output, "text", None) or str(output)
            # If the version returns prompt + completion concatenated,
            # strip the prefix so the scorer only sees the model's reply.
            if output.startswith(chat_prompt):
                output = output[len(chat_prompt):]
            return {"success": True, "output": output.strip(), "adapter_path": adapter_path}
        except Exception as e:
            logger.warning(
                f"[mlx-infer] In-process generate failed ({e}); "
                f"falling back to subprocess for this call"
            )

    # Slow path
    try:
        cmd = [
            sys.executable, "-m", "mlx_lm.generate",
            "--model", base_model,
            "--adapter-path", adapter_path,
            "--max-tokens", "512",
            "--prompt", chat_prompt,
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=120, encoding="utf-8", errors="replace",
            env=_clean_subprocess_env(),
        )
        if result.returncode == 0:
            return {"success": True, "output": result.stdout.strip(), "adapter_path": adapter_path}
        return {"success": False, "error": result.stderr[:500] or f"Exit code {result.returncode}"}
    except Exception as e:
        return {"success": False, "error": str(e)}
