"""LoRA trainer for per-agent models using MLX (Apple Silicon) or Unsloth (NVIDIA).

Trains a small LoRA adapter on the agent's training data (extracted from PR
review history). The adapter encodes the team's coding patterns so the agent
can check compliance without calling an external API.

Usage:
    from planet_maiko.brain.learning.trainer import train_agent
    result = train_agent("agent-blitzflow", repo="acme/backend")

Or via CLI:
    maiko train blitzflow
    maiko train --all
"""

import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Default training config
#
# Notes on the defaults below:
#   - epochs=2 because LoRA on synthetic-rule data overfits fast past
#     2 epochs (we've seen val plateau by ~iter 10k on 3-epoch runs of
#     ~6k pairs). Raise per-run via the UI when a run is converging well.
#   - max_seq_length=1024 fits comfortably on 32GB+ Apple Silicon for
#     8B-4bit; on 16GB or with very long pairs, drop to 512.
#   - grad_checkpoint=False (off) by default — it costs ~10% throughput
#     for ~30% memory savings; flip it on per-run when OOM happens.
#   - early_stop_patience=3 means "kill the run if val loss hasn't
#     improved for 3 evaluation rounds (~600 iters at default
#     --steps-per-eval=200)." Set to 0 to disable early stopping.
SYSTEM_PROMPT = (
    "You are a code review assistant. Given a code change with its "
    "file path and PR context, identify violations of coding standards, "
    "missing edge cases, security issues, or other problems. Respond "
    "PASS if the code is clean."
)

# Base models the trainer + inferer know how to handle. Adding a new
# row requires (a) the mlx-community 4-bit weights existing on HF and
# (b) family being one we have a chat-template for in
# `_build_chat_prompt`. UI exposes the `label` to the user.
SUPPORTED_BASE_MODELS = [
    {
        "id": "mlx-community/Meta-Llama-3.1-8B-Instruct-4bit",
        "label": "Llama 3.1 8B (default, general-purpose)",
        "family": "llama",
        "approx_ram_gb": 7,
    },
    {
        "id": "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit",
        "label": "Qwen 2.5 Coder 7B (code-specialized)",
        "family": "qwen",
        "approx_ram_gb": 7,
    },
    {
        "id": "mlx-community/Qwen2.5-Coder-14B-Instruct-4bit",
        "label": "Qwen 2.5 Coder 14B (code-specialized, recommended)",
        "family": "qwen",
        "approx_ram_gb": 12,
    },
]


def _model_family(base_model):
    """Detect chat-template family from base_model name. Llama 3.x and
    Qwen 2.x have different special tokens, and using the wrong wrapper
    silently produces garbage output."""
    name = (base_model or "").lower()
    if "qwen" in name:
        return "qwen"
    return "llama"


def _build_chat_prompt(base_model, system_prompt, user_prompt):
    """Construct the chat-template string for mlx_lm.generate. mlx-lm
    takes a raw prompt, not messages, so we wrap by hand. Each model
    family has its own special-token format — Llama uses
    `<|begin_of_text|>` + role headers, Qwen uses `<|im_start|>` /
    `<|im_end|>`."""
    family = _model_family(base_model)
    if family == "qwen":
        return (
            f"<|im_start|>system\n{system_prompt}<|im_end|>\n"
            f"<|im_start|>user\n{user_prompt}<|im_end|>\n"
            f"<|im_start|>assistant\n"
        )
    return (
        "<|begin_of_text|>"
        "<|start_header_id|>system<|end_header_id|>\n\n"
        f"{system_prompt}<|eot_id|>"
        "<|start_header_id|>user<|end_header_id|>\n\n"
        f"{user_prompt}<|eot_id|>"
        "<|start_header_id|>assistant<|end_header_id|>\n\n"
    )


def _resolve_base_model_for_adapter(adapter_path):
    """Read adapter's metadata.json to get the base_model used during
    training. Falls back to DEFAULT_TRAINING_CONFIG['base_model'] for
    adapters that predate the metadata write."""
    if not adapter_path:
        return DEFAULT_TRAINING_CONFIG["base_model"]
    metadata_path = os.path.join(adapter_path, "metadata.json")
    if not os.path.isfile(metadata_path):
        return DEFAULT_TRAINING_CONFIG["base_model"]
    try:
        with open(metadata_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        return meta.get("base_model") or DEFAULT_TRAINING_CONFIG["base_model"]
    except Exception:
        return DEFAULT_TRAINING_CONFIG["base_model"]


DEFAULT_TRAINING_CONFIG = {
    "base_model": "mlx-community/Meta-Llama-3.1-8B-Instruct-4bit",
    "lora_rank": 16,
    "lora_alpha": 16,
    "epochs": 2,
    "batch_size": 1,
    "learning_rate": 1e-4,
    "max_seq_length": 1024,
    "grad_checkpoint": False,
    "early_stop_patience": 3,
    # corrections_weight: how many times each pair from corrections.jsonl
    # gets repeated in the training file. 1 = current behavior (no
    # up-weighting). 3 lifts ~10 corrections in a 1500-pair dataset
    # from 0.7% of signal to ~2%. Capped at 5 in the UI; past that the
    # corrections start dominating and you risk catastrophic forgetting.
    "corrections_weight": 1,
}


def get_backend():
    """Detect available training backend."""
    # Check for MLX (Apple Silicon)
    try:
        import mlx
        return "mlx"
    except ImportError:
        pass

    # Check for CUDA (NVIDIA)
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass

    return None


def train_agent(agent_profile_id=None, dataset_path=None, repo=None, config=None, adapter_path=None):
    """Train a LoRA adapter scoped to a repo (or "global" by default).

    Args:
        agent_profile_id: optional. When provided, the adapter path is
            also written to that AgentProfile.extra (legacy assign-on-
            train flow used by the CLI). The HTTP API leaves this None
            and assigns later via /training/assign-adapter.
        dataset_path: explicit JSONL dataset (auto-selects latest if None,
            preferring repo-specific files when `repo` is set).
        repo: optional "org/name" — used for auto-selecting a
            repo-specific dataset and for naming the output adapter
            when adapter_path isn't provided.
        config: training hyperparameter overrides.
        adapter_path: caller-provided output dir. The HTTP API pre-
            creates this and seeds progress.json so the UI can poll
            immediately; CLI callers leave it None and we generate a
            timestamped path under data_dir/models/.

    Returns:
        dict with {adapter_path, examples, epochs, loss, duration_seconds}
    """
    from planet_maiko.paths import data_dir

    train_config = {**DEFAULT_TRAINING_CONFIG, **(config or {})}
    backend = get_backend()

    scope_label = agent_profile_id or (repo or "global")
    logger.info(f"[lora-train] Training LoRA for {scope_label}")
    logger.info(f"[lora-train] Backend: {backend or 'NONE — install mlx or pytorch'}")
    logger.info(f"[lora-train] Config: rank={train_config['lora_rank']}, epochs={train_config['epochs']}")

    if not backend:
        return {
            "success": False,
            "error": "No training backend available. Install mlx (Mac) or pytorch+cuda (NVIDIA).",
            "install_hint": "pip install mlx mlx-lm" if sys.platform == "darwin" else "pip install torch unsloth",
        }

    # Find training data — prefer repo-specific dataset if repo is specified
    if not dataset_path:
        data_path = os.path.join(data_dir(), "training-data")
        if os.path.isdir(data_path):
            files = sorted(os.listdir(data_path), reverse=True)
            if repo:
                # Look for repo-specific dataset first
                safe_name = repo.replace("/", "--")
                repo_files = [f for f in files if f.startswith(safe_name) and f.endswith(".jsonl")]
                if repo_files:
                    dataset_path = os.path.join(data_path, repo_files[0])
                    logger.info(f"[lora-train] Using repo-specific dataset for {repo}")
            if not dataset_path:
                # Fall back to combined dataset, then any dataset
                combined = [f for f in files if f.startswith("combined-") and f.endswith(".jsonl")]
                any_jsonl = [f for f in files if f.endswith(".jsonl")]
                pick = combined[0] if combined else (any_jsonl[0] if any_jsonl else None)
                if pick:
                    dataset_path = os.path.join(data_path, pick)

    if not dataset_path or not os.path.exists(dataset_path):
        return {"success": False, "error": "No training data found. Extract from PRs first."}

    # Count examples
    with open(dataset_path) as f:
        example_count = sum(1 for _ in f)
    logger.info(f"[lora-train] Dataset: {dataset_path} ({example_count} examples)")

    if example_count < 10:
        return {"success": False, "error": f"Only {example_count} examples — need at least 10 for training."}

    # Prepare output path. The API layer may pass an adapter_path it
    # already created (and seeded with a progress.json) so the UI can
    # poll the right adapter while we spin up; otherwise we generate.
    if not adapter_path:
        models_dir = os.path.join(data_dir(), "models")
        os.makedirs(models_dir, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        if agent_profile_id:
            adapter_name = f"{agent_profile_id}-{timestamp}"
        else:
            safe_repo = repo.replace("/", "--") if repo else "global"
            adapter_name = f"lora-{safe_repo}-{timestamp}"
        adapter_path = os.path.join(models_dir, adapter_name)
    else:
        os.makedirs(adapter_path, exist_ok=True)

    # Convert JSONL to the format the backend expects
    train_file = _prepare_training_file(dataset_path, adapter_path, train_config)

    start_time = datetime.now(timezone.utc)

    if backend == "mlx":
        result = _train_mlx(train_file, adapter_path, train_config)
    elif backend in ("cuda", "mps"):
        result = _train_pytorch(train_file, adapter_path, train_config, backend)
    else:
        result = {"success": False, "error": "Unknown backend"}

    duration = (datetime.now(timezone.utc) - start_time).total_seconds()

    # Persist adapter metadata so future inference loads the right
    # base model + the right chat template. Must happen on both
    # success and early-stop paths — the on-disk weights are usable
    # in both cases. Skipped on outright failure.
    if result.get("success"):
        try:
            metadata_path = os.path.join(adapter_path, "metadata.json")
            with open(metadata_path, "w", encoding="utf-8") as mf:
                json.dump({
                    "base_model": train_config["base_model"],
                    "trained_at": datetime.now(timezone.utc).isoformat(),
                    "iters": result.get("iters_run"),
                    "best_val_loss": result.get("best_val_loss"),
                    "early_stopped": result.get("early_stopped", False),
                    "examples": example_count,
                    "epochs": train_config["epochs"],
                    "max_seq_length": train_config["max_seq_length"],
                    "lora_rank": train_config["lora_rank"],
                    "repo": repo,
                }, mf, indent=2)
        except Exception as e:
            logger.warning(f"[lora-train] Could not write adapter metadata: {e}")

    if result.get("success"):
        # Legacy: when called with an explicit agent_profile_id (CLI),
        # write the adapter path back to that profile so the agent
        # picks it up. Repo-scoped HTTP training skips this — the UI
        # assigns adapters separately via /training/assign-adapter.
        if agent_profile_id:
            try:
                from planet_maiko.database import db
                from planet_maiko.models.agent_profile import AgentProfile
                profile = db.session.get(AgentProfile, agent_profile_id)
                if profile:
                    profile.extra = {
                        **(profile.extra or {}),
                        "adapter_path": adapter_path,
                        "trained_at": datetime.now(timezone.utc).isoformat(),
                        "trained_on_examples": example_count,
                    }
                    db.session.commit()
                    logger.info(f"[lora-train] Updated profile {agent_profile_id} with adapter path")
            except Exception as e:
                logger.debug(f"[lora-train] Could not update profile: {e}")

        result["adapter_path"] = adapter_path
        result["examples"] = example_count
        result["duration_seconds"] = round(duration)
        logger.info(f"[lora-train] Complete! Adapter: {adapter_path} ({duration:.0f}s)")
    else:
        logger.error(f"[lora-train] Failed: {result.get('error')}")

    return result


def _prepare_training_file(dataset_path, output_dir, config):
    """Convert our JSONL format to the chat format the training backend expects,
    splitting into train / validation so the eval set is actually held out.

    Previously the full dataset was written to train.jsonl and the evaluator
    ran a fresh random 20% split at eval time — which meant training had
    already fit to those examples. Precision/recall looked great because the
    model had memorized the "held-out" set.

    Now:
      - Collect all pairs, shuffle deterministically (seeded), split 80/20.
      - Write train.jsonl + valid.jsonl in the backend's chat format. MLX
        and other backends pick up valid.jsonl automatically from --data.
      - Write holdout.jsonl + train_pairs.jsonl alongside, in the
        original {input,output} format. holdout.jsonl is the canonical
        eval set; train_pairs.jsonl supports the train-vs-holdout F1
        comparison (`maiko eval --on-training`) for spotting overfit.
    """
    import random as _random

    os.makedirs(output_dir, exist_ok=True)
    train_file = os.path.join(output_dir, "train.jsonl")
    valid_file = os.path.join(output_dir, "valid.jsonl")
    holdout_file = os.path.join(output_dir, "holdout.jsonl")
    train_pairs_file = os.path.join(output_dir, "train_pairs.jsonl")

    system_prompt = "You are a code review assistant. Given a code change with its file path and PR context, identify violations of coding standards, missing edge cases, security issues, or other problems. Respond PASS if the code is clean."

    # Collect all source files: main dataset + corrections + global rules
    source_files = [dataset_path]
    training_dir = os.path.dirname(dataset_path)

    corrections_path = os.path.join(training_dir, "corrections.jsonl")
    if os.path.exists(corrections_path):
        source_files.append(corrections_path)
        logger.info(f"[lora-train] Including corrections from {corrections_path}")

    # Include rules-*.jsonl files (contain training data from global + repo-scoped learnings)
    if os.path.isdir(training_dir):
        for fname in sorted(os.listdir(training_dir)):
            fpath = os.path.join(training_dir, fname)
            if fname.startswith("rules-") and fname.endswith(".jsonl") and fpath not in source_files:
                source_files.append(fpath)
                logger.info(f"[lora-train] Including rule-based training data from {fname}")

    all_pairs = []
    corrections_pairs = []  # tracked separately so we can up-weight them post-split
    for source in source_files:
        with open(source) as f_in:
            for line in f_in:
                try:
                    pair = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "input" not in pair or "output" not in pair:
                    continue
                all_pairs.append(pair)
                if source == corrections_path:
                    corrections_pairs.append(pair)

    # Deterministic shuffle → same holdout every time the trainer runs on
    # the same data. If the evaluator can't find holdout.jsonl it'll fall
    # back to this same seed so the split is consistent across paths.
    seed = int(config.get("split_seed", 42))
    holdout_fraction = float(config.get("holdout_fraction", 0.2))
    holdout_fraction = max(0.0, min(0.5, holdout_fraction))

    rng = _random.Random(seed)
    shuffled = list(all_pairs)
    rng.shuffle(shuffled)

    split_idx = int(len(shuffled) * holdout_fraction)
    # Guarantee at least one valid pair when we have enough data — MLX
    # won't gracefully handle valid.jsonl with zero rows.
    if len(shuffled) >= 10 and split_idx == 0:
        split_idx = 1
    holdout_pairs = shuffled[:split_idx]
    train_pairs = shuffled[split_idx:]

    # Up-weight corrections AFTER the split so duplicates only land
    # in the train set — duplicating across both sides would inflate
    # holdout F1 by giving the evaluator pairs the trainer memorized.
    # Capped here too, in case a stale config sneaks past the UI cap.
    corrections_weight = max(1, min(5, int(config.get("corrections_weight", 1) or 1)))
    if corrections_weight > 1 and corrections_pairs:
        # Match by (input, output) — corrections that landed in the
        # holdout split don't get duplicated; the rest get N-1 extra
        # copies stitched onto train.
        correction_keys = {(p["input"], p["output"]) for p in corrections_pairs}
        train_corrections = [
            p for p in train_pairs
            if (p["input"], p["output"]) in correction_keys
        ]
        extra_copies = corrections_weight - 1
        for _ in range(extra_copies):
            train_pairs.extend(train_corrections)
        logger.info(
            f"[lora-train] Up-weighted {len(train_corrections)} corrections "
            f"by {corrections_weight}× (+{len(train_corrections) * extra_copies} "
            f"duplicates added to train only)"
        )

    def _write_chat(path, pairs):
        with open(path, "w", encoding="utf-8") as f_out:
            for pair in pairs:
                chat = {
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": pair["input"]},
                        {"role": "assistant", "content": pair["output"]},
                    ]
                }
                f_out.write(json.dumps(chat, ensure_ascii=False) + "\n")

    _write_chat(train_file, train_pairs)
    _write_chat(valid_file, holdout_pairs)
    with open(holdout_file, "w", encoding="utf-8") as f_out:
        for pair in holdout_pairs:
            f_out.write(json.dumps(pair, ensure_ascii=False) + "\n")
    with open(train_pairs_file, "w", encoding="utf-8") as f_out:
        for pair in train_pairs:
            f_out.write(json.dumps(pair, ensure_ascii=False) + "\n")

    logger.info(
        f"[lora-train] Split {len(all_pairs)} pairs → train={len(train_pairs)} "
        f"holdout={len(holdout_pairs)} (seed={seed}, fraction={holdout_fraction})"
    )

    return train_file


def _train_mlx(train_file, adapter_path, config):
    """Train using MLX (Apple Silicon).

    Three resilience features layered on top of the basic mlx-lm call:

      1. Early stopping — we parse "Val loss" lines as they stream and
         terminate the subprocess when val hasn't improved for
         `early_stop_patience` consecutive eval rounds. This caps both
         compute and overfit: the on-disk adapters.safetensors at
         termination is from the most recent --save-every checkpoint.

      2. grad_checkpoint — opt-in flag that recomputes activations
         during the backward pass instead of caching them. ~30% memory
         savings for ~10% slower iters; flip it on when the previous
         run OOM'd.

      3. resume_adapter_file — optional path to an existing adapters.
         safetensors. mlx-lm's --resume-adapter-file picks up training
         from those weights, so OOMs (or early-stops you regret) don't
         throw away progress.
    """
    import math as _math
    import re as _re

    logger.info("[lora-train] Using MLX backend (Apple Silicon)")

    try:
        data_dir = os.path.dirname(train_file)

        # Iters scaled to the actual dataset: ceil(N / batch) * epochs
        # so each epoch is a full pass.
        with open(train_file, "r", encoding="utf-8") as f:
            train_count = sum(1 for _ in f)
        batch_size = max(1, int(config["batch_size"]))
        epochs = max(1, int(config["epochs"]))
        total_iters = max(1, _math.ceil(train_count / batch_size)) * epochs

        # mlx-lm reads LoRA hyperparameters (rank, scale, dropout) from
        # a YAML config file, not CLI flags — so we write one alongside
        # the adapter and pass it via --config. Without this step, our
        # DEFAULT_TRAINING_CONFIG.lora_rank is a no-op and mlx-lm uses
        # its own default (rank 8), which is why earlier runs felt
        # capped regardless of what we set in Python.
        lora_rank = max(1, int(config.get("lora_rank", 16)))
        lora_alpha = max(1, int(config.get("lora_alpha", lora_rank)))
        lora_yaml_path = os.path.join(adapter_path, "lora_config.yaml")
        try:
            with open(lora_yaml_path, "w", encoding="utf-8") as yf:
                yf.write(
                    "lora_parameters:\n"
                    f"  rank: {lora_rank}\n"
                    f"  scale: {float(lora_alpha)}\n"
                    "  dropout: 0.0\n"
                )
        except Exception as e:
            logger.warning(f"[lora-train] Could not write lora_config.yaml: {e}")

        cmd = [
            sys.executable, "-m", "mlx_lm.lora",
            "--model", config["base_model"],
            "--data", data_dir,
            "--adapter-path", adapter_path,
            "--config", lora_yaml_path,
            "--train",
            "--iters", str(total_iters),
            "--batch-size", str(batch_size),
            "--learning-rate", str(config["learning_rate"]),
            "--max-seq-length", str(config["max_seq_length"]),
        ]
        if config.get("grad_checkpoint"):
            cmd.append("--grad-checkpoint")
        resume_from = config.get("resume_adapter_file")
        if resume_from:
            cmd.extend(["--resume-adapter-file", resume_from])

        patience = int(config.get("early_stop_patience") or 0)
        IMPROVE_DELTA = 1e-3  # val must drop by this much to count

        logger.info(
            f"[lora-train] {train_count} train pairs × {epochs} epochs "
            f"/ batch {batch_size} = {total_iters} iters "
            f"(early_stop_patience={patience}, "
            f"grad_checkpoint={bool(config.get('grad_checkpoint'))}, "
            f"resume={bool(resume_from)})"
        )
        logger.info(f"[lora-train] Running: {' '.join(cmd)}")

        progress_path = os.path.join(adapter_path, "progress.json")
        os.makedirs(adapter_path, exist_ok=True)

        output_lines = []
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )

        # mlx-lm prints separate "Train loss" and "Val loss" lines per
        # iter; matching them separately is what makes overfit visible.
        train_re = _re.compile(r"Iter\s+(\d+):\s*Train loss\s+([\d.]+)", _re.IGNORECASE)
        val_re = _re.compile(r"Iter\s+(\d+):\s*Val loss\s+([\d.]+)", _re.IGNORECASE)
        tok_re = _re.compile(r"([\d.]+)\s*Tokens/sec", _re.IGNORECASE)

        loss_history = []
        latest_train = None
        latest_val = None
        latest_tokens_sec = None
        latest_iter = 0
        best_val_loss = None
        evals_since_improve = 0
        early_stopped = False

        def _update_history(it, train=None, val=None):
            nonlocal latest_train, latest_val
            if loss_history and loss_history[-1]["iter"] == it:
                entry = loss_history[-1]
            else:
                entry = {"iter": it, "train": None, "val": None}
                loss_history.append(entry)
                if len(loss_history) > 500:
                    loss_history[:] = loss_history[::2]
            if train is not None:
                entry["train"] = train
                latest_train = train
            if val is not None:
                entry["val"] = val
                latest_val = val

        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            logger.info(f"[lora-train] {line}")
            output_lines.append(line)

            mt = train_re.search(line)
            mv = val_re.search(line)
            tokm = tok_re.search(line)
            if tokm:
                latest_tokens_sec = float(tokm.group(1))

            if mt:
                latest_iter = int(mt.group(1))
                _update_history(latest_iter, train=float(mt.group(2)))
            if mv:
                latest_iter = int(mv.group(1))
                latest_val_value = float(mv.group(2))
                _update_history(latest_iter, val=latest_val_value)

                # Early-stop bookkeeping: each val-loss line is one
                # "eval" — improvement resets the counter, plateau
                # increments it. Only fires when patience > 0.
                if patience > 0:
                    if best_val_loss is None or latest_val_value < best_val_loss - IMPROVE_DELTA:
                        best_val_loss = latest_val_value
                        evals_since_improve = 0
                    else:
                        evals_since_improve += 1
                        if evals_since_improve >= patience:
                            logger.info(
                                f"[lora-train] Early stop at iter {latest_iter}: "
                                f"val plateau for {evals_since_improve} evals "
                                f"(best={best_val_loss:.4f}, latest={latest_val_value:.4f})"
                            )
                            early_stopped = True
                            try:
                                process.terminate()
                            except Exception:
                                pass
                            break

            if mt or mv:
                progress = {
                    "iteration": latest_iter,
                    "total_iters": total_iters,
                    "train_loss": latest_train,
                    "val_loss": latest_val,
                    "best_val_loss": best_val_loss,
                    "loss": latest_train,  # back-compat
                    "tokens_sec": latest_tokens_sec,
                    "percent": round(latest_iter / total_iters * 100, 1) if total_iters else 0,
                    "status": "training",
                    "loss_history": loss_history,
                }
                try:
                    with open(progress_path, "w") as pf:
                        json.dump(progress, pf)
                except Exception:
                    pass

        process.wait()

        # Status semantics:
        #   done       — full run completed cleanly (rc 0, no early stop)
        #   done_early — we terminated due to val plateau; on-disk
        #                weights are from the last --save-every
        #                checkpoint, which is the right thing to keep.
        #   failed     — non-zero exit and no early-stop flag
        if early_stopped:
            status = "done_early"
        elif process.returncode == 0:
            status = "done"
        else:
            status = "failed"

        try:
            final = {
                "status": status,
                "percent": 100 if status == "done" else round(latest_iter / total_iters * 100, 1) if total_iters else 0,
                "iteration": latest_iter,
                "total_iters": total_iters,
                "train_loss": latest_train,
                "val_loss": latest_val,
                "best_val_loss": best_val_loss,
                "loss": latest_train,
                "loss_history": loss_history,
                "early_stopped": early_stopped,
            }
            with open(progress_path, "w") as pf:
                json.dump(final, pf)
        except Exception:
            pass

        if status in ("done", "done_early"):
            return {
                "success": True,
                "backend": "mlx",
                "early_stopped": early_stopped,
                "best_val_loss": best_val_loss,
                "iters_run": latest_iter,
            }
        tail = "\n".join(output_lines[-5:]) if output_lines else "no output"
        return {"success": False, "error": f"MLX training exited with code {process.returncode}:\n{tail}"}

    except FileNotFoundError:
        return {
            "success": False,
            "error": "mlx-lm not installed. Run: pip install mlx-lm",
            "install_hint": "pip install mlx-lm",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def _train_pytorch(train_file, adapter_path, config, device):
    """Train using PyTorch (CUDA or MPS).

    Not implemented — only the MLX backend (Apple Silicon) is supported.
    To add a PyTorch path, wire up a real SFTTrainer call here. The
    previous stub built a script string with f-string interpolation,
    which both swallowed errors and risked code injection.
    """
    return {
        "success": False,
        "error": (
            f"PyTorch backend ({device}) is not implemented. Only MLX (Apple Silicon) "
            "is currently supported. Install mlx-lm on a Mac, or contribute a PyTorch "
            "implementation in src/planet_maiko/brain/learning/trainer.py::_train_pytorch."
        ),
    }


def reset_stale_training_progress():
    """On app startup, mark any in-progress training runs as failed.

    progress.json with status="training" or "preparing" survives a
    server crash or laptop reboot because mlx-lm doesn't get to write
    a final state — it was killed mid-run along with everything else.
    The next server boot has no subprocess attached, so the UI would
    poll /training/progress, see "training", and lock the spinner on
    forever. Sweep them to a clean failed state with a clear reason.

    Guard: skips entries whose progress.json was modified in the
    last 30 seconds. In the rare case where Flask restarted but the
    mlx-lm subprocess is still running (orphaned but alive), we don't
    want to stomp its live progress.
    """
    from planet_maiko.paths import data_dir

    models_dir = os.path.join(data_dir(), "models")
    if not os.path.isdir(models_dir):
        return 0

    now = datetime.now(timezone.utc).timestamp()
    cleaned = 0
    for adapter_name in os.listdir(models_dir):
        progress_path = os.path.join(models_dir, adapter_name, "progress.json")
        if not os.path.isfile(progress_path):
            continue
        try:
            mtime = os.path.getmtime(progress_path)
        except Exception:
            continue
        # If updated in the last 30s, the subprocess might still be
        # writing to it — leave it alone.
        if now - mtime < 30:
            continue
        try:
            with open(progress_path, "r", encoding="utf-8") as f:
                progress = json.load(f)
        except Exception:
            continue
        if progress.get("status") in ("training", "preparing"):
            progress["status"] = "failed"
            progress["error"] = (
                "Training did not complete — server restarted or the "
                "process was killed before the run finished."
            )
            try:
                with open(progress_path, "w", encoding="utf-8") as f:
                    json.dump(progress, f)
                cleaned += 1
            except Exception:
                pass
    if cleaned:
        logger.info(f"[lora-train] Marked {cleaned} stale training run(s) as failed")
    return cleaned


def check_requirements():
    """Check what training backends are available."""
    backend = get_backend()
    info = {
        "backend": backend,
        "ready": backend is not None,
    }

    if sys.platform == "darwin":
        info["recommendation"] = "Install MLX: pip install mlx mlx-lm"
        try:
            import mlx
            from importlib.metadata import version
            info["mlx_version"] = version("mlx")
            info["mlx_installed"] = True
        except ImportError:
            info["mlx_installed"] = False
    else:
        info["recommendation"] = "Install PyTorch + Unsloth: pip install torch unsloth"
        try:
            import torch
            info["pytorch_version"] = torch.__version__
            info["cuda_available"] = torch.cuda.is_available()
        except ImportError:
            info["pytorch_installed"] = False

    return info


def review_code(code, agent_profile_id=None, adapter_path=None, file_path=None):
    """Run code through a trained LoRA adapter and return the review.

    Args:
        code: the code to review
        agent_profile_id: look up adapter from agent profile
        adapter_path: explicit adapter path (overrides profile lookup)
        file_path: optional file path for context

    Returns:
        dict with {output, adapter_path, success}
    """
    backend = get_backend()
    if not backend:
        return {"success": False, "error": "No backend available"}

    # Find adapter
    if not adapter_path and agent_profile_id:
        try:
            from flask import current_app
            from planet_maiko.database import db
            from planet_maiko.models.agent_profile import AgentProfile
            profile = db.session.get(AgentProfile, agent_profile_id)
            if profile and profile.extra:
                adapter_path = profile.extra.get("adapter_path")
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


def review_batch(files, agent_profile_id=None, adapter_path=None):
    """Review multiple files in a single model load.

    Args:
        files: list of {"code": str, "file_path": str} dicts
        agent_profile_id: look up adapter from agent profile
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
        agent_profile_id=agent_profile_id,
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


def _infer_mlx(prompt_text, adapter_path, config):
    """Run inference with MLX."""
    try:
        # Format as chat using the same template the model was trained on.
        # mlx_lm.generate takes a raw string, so we wrap by hand —
        # _build_chat_prompt picks the right family-specific template
        # (Llama special tokens vs Qwen's <|im_start|>/<|im_end|>).
        chat_prompt = _build_chat_prompt(
            config["base_model"], SYSTEM_PROMPT, prompt_text
        )

        cmd = [
            sys.executable, "-m", "mlx_lm.generate",
            "--model", config["base_model"],
            "--adapter-path", adapter_path,
            "--max-tokens", "512",
            "--prompt", chat_prompt,
        ]

        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=120, encoding="utf-8", errors="replace",
        )

        if result.returncode == 0:
            output = result.stdout.strip()
            return {"success": True, "output": output, "adapter_path": adapter_path}
        else:
            return {"success": False, "error": result.stderr[:500] or f"Exit code {result.returncode}"}

    except Exception as e:
        return {"success": False, "error": str(e)}
