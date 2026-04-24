"""LoRA adapter evaluation — precision/recall/F1 on held-out training data.

Splits training data into train/test sets, runs inference on the test set,
and computes precision/recall/F1 metrics for LoRA quality measurement.

Usage:
    from planet_maiko.brain.learning.lora_eval import evaluate_adapter
    result = evaluate_adapter(adapter_path="/path/to/adapter", holdout_fraction=0.2)
"""

import json
import logging
import os
import random

logger = logging.getLogger(__name__)


def resolve_lora_for_repo(repo):
    """Return the adapter path for a repo, or None if none configured.

    Reads config.lora.models_by_repo — a flat dict of
    "org/repo-name" → adapter path. Used by the lora_check endpoint so
    the agent doesn't need to know or care which adapter to load;
    it's implicit from the repo it's scoped to. The path is
    os.path.expanduser-ed so ~ works in the config file.
    """
    if not repo:
        return None
    try:
        from planet_maiko.config import load_config
        mapping = (load_config().get("lora", {}) or {}).get("models_by_repo", {}) or {}
    except Exception:
        return None
    path = mapping.get(repo)
    if not path:
        return None
    path = os.path.expanduser(path)
    return path if os.path.exists(path) else None


SPLIT_SEED = 42


def evaluate_adapter(adapter_path=None, repo=None, holdout_fraction=0.2):
    """Evaluate a LoRA adapter on held-out training data.

    Args:
        adapter_path: path to the adapter directory (uses latest if None)
        repo: filter test data to this repo
        holdout_fraction: fraction of data to hold out for testing (default 0.2).
            Only used for the fallback path; adapters trained with the new
            code persist their actual holdout in <adapter_path>/holdout.jsonl.

    Returns:
        dict with {success, precision, recall, f1, test_count, per_category, adapter_path}

    Sources of test pairs, in order:
      1. <adapter_path>/holdout.jsonl — written by trainer._prepare_training_file.
         This is the ONLY honest signal: these exact pairs were excluded from
         train.jsonl, so eval measures generalization, not memorization.
      2. Fallback: pairs collected from data_dir/training-data/rules-*.jsonl,
         deterministically shuffled with SPLIT_SEED, top `holdout_fraction`
         taken as the eval set. Used for adapters trained before the split
         landed. Note: this is still contaminated for those adapters since
         the trainer they ran fit to all of the data — numbers are best read
         as "upper bound" not "held-out precision".
    """
    from planet_maiko.paths import data_dir
    from planet_maiko.brain.learning.trainer import review_code

    # Find adapter
    if not adapter_path:
        models_dir = os.path.join(data_dir(), "models")
        if os.path.isdir(models_dir):
            adapters = sorted(os.listdir(models_dir), reverse=True)
            if adapters:
                adapter_path = os.path.join(models_dir, adapters[0])

    if not adapter_path or not os.path.isdir(adapter_path):
        return {"success": False, "error": "No adapter found. Train one first."}

    # Primary path: the adapter was trained with the new split, so there's
    # a real holdout file sitting next to it.
    holdout_path = os.path.join(adapter_path, "holdout.jsonl")
    test_pairs = None
    source = None
    if os.path.isfile(holdout_path):
        pairs = []
        with open(holdout_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    pair = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if repo and pair.get("repo") and pair["repo"] != repo:
                    continue
                pairs.append(pair)
        if pairs:
            test_pairs = pairs
            source = "adapter_holdout"
            logger.info(f"[lora-eval] Using {len(test_pairs)} pairs from {holdout_path}")

    # Fallback: reconstruct the 20% with the same seed training would have used.
    if test_pairs is None:
        data_path = os.path.join(data_dir(), "training-data")
        if not os.path.isdir(data_path):
            return {"success": False, "error": "No training data found."}

        all_pairs = []
        for fname in sorted(os.listdir(data_path), reverse=True):
            if fname.startswith("rules-") and fname.endswith(".jsonl"):
                fpath = os.path.join(data_path, fname)
                with open(fpath, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            pair = json.loads(line)
                            if repo and pair.get("repo") and pair["repo"] != repo:
                                continue
                            all_pairs.append(pair)

        if len(all_pairs) < 10:
            return {"success": False, "error": f"Only {len(all_pairs)} test pairs — need at least 10."}

        # Deterministic shuffle so re-running the fallback on the same data
        # produces the same holdout. Old adapter runs were non-deterministic
        # (unseeded), so numbers across runs for the same model wandered.
        rng = random.Random(SPLIT_SEED)
        shuffled = list(all_pairs)
        rng.shuffle(shuffled)
        split_idx = max(1, int(len(shuffled) * holdout_fraction))
        test_pairs = shuffled[:split_idx]
        source = "reconstructed"
        logger.warning(
            f"[lora-eval] No holdout.jsonl next to adapter — reconstructing a "
            f"seeded 20% split. Numbers are contaminated if this adapter was "
            f"trained on the full dataset."
        )

    logger.info(f"[lora-eval] Evaluating on {len(test_pairs)} held-out examples ({source})")

    # Run inference on test set
    tp = 0  # true positives (model says VIOLATION, ground truth is VIOLATION)
    fp = 0  # false positives (model says VIOLATION, ground truth is PASS)
    tn = 0  # true negatives (model says PASS, ground truth is PASS)
    fn = 0  # false negatives (model says PASS, ground truth is VIOLATION)

    per_category = {}  # category -> {tp, fp, tn, fn}

    for pair in test_pairs:
        expected_violation = not pair["output"].startswith("PASS")
        category = pair.get("category", "unknown")

        if category not in per_category:
            per_category[category] = {"tp": 0, "fp": 0, "tn": 0, "fn": 0, "count": 0}
        per_category[category]["count"] += 1

        result = review_code(
            code=pair["input"],
            adapter_path=adapter_path,
        )

        if not result.get("success"):
            # Model failed to run — skip this example
            continue

        predicted_violation = "VIOLATION" in result.get("output", "")

        if predicted_violation and expected_violation:
            tp += 1
            per_category[category]["tp"] += 1
        elif predicted_violation and not expected_violation:
            fp += 1
            per_category[category]["fp"] += 1
        elif not predicted_violation and not expected_violation:
            tn += 1
            per_category[category]["tn"] += 1
        else:
            fn += 1
            per_category[category]["fn"] += 1

    # Compute metrics
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    # Per-category metrics
    cat_metrics = {}
    for cat, counts in per_category.items():
        p = counts["tp"] / (counts["tp"] + counts["fp"]) if (counts["tp"] + counts["fp"]) > 0 else 0.0
        r = counts["tp"] / (counts["tp"] + counts["fn"]) if (counts["tp"] + counts["fn"]) > 0 else 0.0
        cat_f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
        cat_metrics[cat] = {"precision": p, "recall": r, "f1": cat_f1, "count": counts["count"]}

    logger.info(f"[lora-eval] P={precision:.2f} R={recall:.2f} F1={f1:.2f} (tp={tp} fp={fp} fn={fn} tn={tn})")

    result = {
        "success": True,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "test_count": len(test_pairs),
        "per_category": cat_metrics,
        "adapter_path": adapter_path,
        # "adapter_holdout" means trainer persisted a real held-out set and
        # we evaluated against it. "reconstructed" means we re-derived a 20%
        # split from the source files — adapters pre-dating the split fix
        # trained on everything, so those numbers are optimistic.
        "test_source": source,
    }

    # Persist the eval so /lora/adapters can surface eval_score and the
    # UI can show a trend line. Best-effort — a DB failure here
    # shouldn't swallow the result the caller already has.
    try:
        _record_eval(result, repo=repo, holdout_fraction=holdout_fraction)
    except Exception as e:
        logger.warning(f"[lora-eval] Failed to persist eval row: {e}")

    return result


def _record_eval(result, repo=None, holdout_fraction=None):
    """Insert an AdapterEval row from an evaluate_adapter() result dict.

    Requires an active Flask app context. CLI entrypoints that call
    evaluate_adapter() set one up with create_app(start_scheduler=False)
    so the commit lands in the shared SQLite DB. Silently skips if no
    context is active — callers wrap this in a try/except anyway.
    """
    from planet_maiko.database import db
    from planet_maiko.models.adapter_eval import AdapterEval

    adapter_path = result.get("adapter_path")
    if not adapter_path:
        return

    row = AdapterEval(
        adapter_path=adapter_path,
        adapter_version=os.path.basename(adapter_path.rstrip(os.sep)),
        repo=repo,
        precision=result.get("precision", 0.0),
        recall=result.get("recall", 0.0),
        f1=result.get("f1", 0.0),
        tp=result.get("tp", 0),
        fp=result.get("fp", 0),
        fn=result.get("fn", 0),
        tn=result.get("tn", 0),
        test_count=result.get("test_count", 0),
        holdout_fraction=holdout_fraction,
        per_category=result.get("per_category") or {},
        # Stash on extra so old rows that didn't know about test_source
        # still deserialize cleanly. UI reads extra.test_source to flag
        # "reconstructed" runs as optimistic.
        extra={"test_source": result.get("test_source")},
    )
    db.session.add(row)
    db.session.commit()


def compare_adapters(adapter_a, adapter_b, repo=None, holdout_fraction=0.2):
    """Side-by-side comparison of two adapters on the same test set.

    Returns:
        dict with {a: metrics, b: metrics, winner: "a"|"b"|"tie"}
    """
    result_a = evaluate_adapter(adapter_path=adapter_a, repo=repo, holdout_fraction=holdout_fraction)
    result_b = evaluate_adapter(adapter_path=adapter_b, repo=repo, holdout_fraction=holdout_fraction)

    if not result_a.get("success"):
        return {"success": False, "error": f"Adapter A failed: {result_a.get('error')}"}
    if not result_b.get("success"):
        return {"success": False, "error": f"Adapter B failed: {result_b.get('error')}"}

    winner = "tie"
    if result_a["f1"] > result_b["f1"] + 0.01:
        winner = "a"
    elif result_b["f1"] > result_a["f1"] + 0.01:
        winner = "b"

    return {
        "success": True,
        "a": result_a,
        "b": result_b,
        "winner": winner,
    }
