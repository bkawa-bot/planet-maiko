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
DEFAULT_TRAINING_CONFIG = {
    "base_model": "mlx-community/Meta-Llama-3.1-8B-Instruct-4bit",
    "lora_rank": 16,
    "lora_alpha": 16,
    "epochs": 3,
    "batch_size": 1,
    "learning_rate": 1e-4,
    "max_seq_length": 1024,
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


def train_agent(agent_profile_id, dataset_path=None, repo=None, config=None, adapter_path=None):
    """Train a LoRA adapter for an agent.

    Args:
        agent_profile_id: the agent to train
        dataset_path: path to JSONL training data (auto-selects latest if None)
        repo: optional repo filter for training data
        config: training config overrides
        adapter_path: override the output adapter directory. If provided,
            the API layer has typically pre-created the dir and seeded
            progress.json so the UI can poll immediately; if None, a
            timestamped path under data_dir/models/ is generated.

    Returns:
        dict with {adapter_path, examples, epochs, loss, duration_seconds}
    """
    from planet_maiko.paths import data_dir

    train_config = {**DEFAULT_TRAINING_CONFIG, **(config or {})}
    backend = get_backend()

    logger.info(f"[lora-train] Training agent {agent_profile_id}")
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
        adapter_name = f"{agent_profile_id}-{timestamp}"
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

    if result.get("success"):
        # Update agent profile
        try:
            from planet_maiko.app import create_app
            # Only update if we're in an app context
            from flask import current_app
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
      - Write holdout.jsonl alongside, in the original {input,output}
        format the evaluator reads. evaluate_adapter() loads this file
        so eval uses exactly the pairs the trainer never saw.
    """
    import random as _random

    os.makedirs(output_dir, exist_ok=True)
    train_file = os.path.join(output_dir, "train.jsonl")
    valid_file = os.path.join(output_dir, "valid.jsonl")
    holdout_file = os.path.join(output_dir, "holdout.jsonl")

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

    logger.info(
        f"[lora-train] Split {len(all_pairs)} pairs → train={len(train_pairs)} "
        f"holdout={len(holdout_pairs)} (seed={seed}, fraction={holdout_fraction})"
    )

    return train_file


def _train_mlx(train_file, adapter_path, config):
    """Train using MLX (Apple Silicon)."""
    logger.info("[lora-train] Using MLX backend (Apple Silicon)")

    try:
        data_dir = os.path.dirname(train_file)

        cmd = [
            sys.executable, "-m", "mlx_lm.lora",
            "--model", config["base_model"],
            "--data", data_dir,
            "--adapter-path", adapter_path,
            "--train",
            "--iters", str(config["epochs"] * 100),
            "--batch-size", str(config["batch_size"]),
            "--learning-rate", str(config["learning_rate"]),
            "--max-seq-length", str(config["max_seq_length"]),
        ]

        logger.info(f"[lora-train] Running: {' '.join(cmd)}")

        total_iters = config["epochs"] * 100
        progress_path = os.path.join(adapter_path, "progress.json")
        os.makedirs(adapter_path, exist_ok=True)

        output_lines = []
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )

        import re as _re
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            logger.info(f"[lora-train] {line}")
            output_lines.append(line)

            # Parse progress from mlx-lm output (e.g. "Iter 50: Train loss 0.712, ...")
            m = _re.search(r"Iter\s+(\d+).*?loss\s+([\d.]+)", line, _re.IGNORECASE)
            if m:
                iteration = int(m.group(1))
                loss = float(m.group(2))
                # Also look for tokens/sec
                tok_m = _re.search(r"([\d.]+)\s*Tokens/sec", line, _re.IGNORECASE)
                tokens_sec = float(tok_m.group(1)) if tok_m else None

                progress = {
                    "iteration": iteration,
                    "total_iters": total_iters,
                    "loss": loss,
                    "tokens_sec": tokens_sec,
                    "percent": round(iteration / total_iters * 100, 1),
                    "status": "training",
                }
                try:
                    with open(progress_path, "w") as pf:
                        json.dump(progress, pf)
                except Exception:
                    pass

        process.wait()

        # Write final status
        try:
            final = {"status": "done" if process.returncode == 0 else "failed", "percent": 100}
            with open(progress_path, "w") as pf:
                json.dump(final, pf)
        except Exception:
            pass

        if process.returncode == 0:
            return {"success": True, "backend": "mlx"}
        else:
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

    config = DEFAULT_TRAINING_CONFIG

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
        # mlx_lm.generate expects a raw string, so we apply the Llama 3.1
        # chat template manually to match the training data format.
        chat_prompt = (
            "<|begin_of_text|>"
            "<|start_header_id|>system<|end_header_id|>\n\n"
            "You are a code review assistant. Given a code change with its "
            "file path and PR context, identify violations of coding standards, "
            "missing edge cases, security issues, or other problems. Respond "
            "PASS if the code is clean.<|eot_id|>"
            "<|start_header_id|>user<|end_header_id|>\n\n"
            f"{prompt_text}<|eot_id|>"
            "<|start_header_id|>assistant<|end_header_id|>\n\n"
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
