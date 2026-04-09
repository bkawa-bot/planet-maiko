"""Batch classifier for raw feedback signals using LLM.

Also handles semantic deduplication of learnings — after classification
rewrites rule text, or on demand, merge learnings that express the same
rule in different words.
"""

import logging
from planet_maiko.database import db
from planet_maiko.models.signal import Signal

logger = logging.getLogger(__name__)

VALID_CATEGORIES = {
    "null_safety", "error_handling", "testing", "performance",
    "api_design", "architecture", "security", "style", "naming",
    "docs", "domain_knowledge", "pattern", "gotcha",
}


def classify_pattern_learnings(batch_size=20):
    """Find Learnings with category='pattern' and reclassify via LLM.

    Updates the learning's rule text (cleaned) and category. Use this
    to clean up the backlog of unsynthesized learnings that accumulated
    from earlier backfills.

    Returns: count of reclassified learnings
    """
    from planet_maiko.models.learning import Learning

    learnings = Learning.query.filter(
        Learning.category == "pattern",
        Learning.status != "dismissed",
    ).limit(batch_size).all()

    if not learnings:
        return 0

    try:
        from planet_maiko.agents.brain_session import _get_runtime
        runtime = _get_runtime()
        if not runtime or not runtime.is_available():
            return 0

        items = []
        for i, l in enumerate(learnings):
            items.append(f"{i+1}. [{l.scope_repo or 'global'}] {l.rule[:300]}")

        prompt = (
            "Synthesize these PR review observations into clean, actionable coding rules.\n"
            "For each observation, extract the core lesson as a short rule (one sentence)\n"
            "and classify it into a category.\n\n"
            "Categories: null_safety, error_handling, testing, performance, "
            "api_design, architecture, security, style, naming, docs, "
            "domain_knowledge, pattern, gotcha\n\n"
            "Observations:\n" + "\n".join(items) + "\n\n"
            "Respond as JSON: {\"rules\": ["
            "{\"index\": 1, \"rule\": \"...\", \"category\": \"...\"}"
            ", ...]}"
        )

        from planet_maiko.agents.routing import resolve_model
        result = runtime.send_json(prompt, timeout=90, model=resolve_model("classify"))

        parsed = result.get("parsed") if isinstance(result, dict) else None
        if not parsed or "rules" not in parsed:
            logger.warning(f"[classifier] No rules in response: {result.get('error') if isinstance(result, dict) else result}")
            return 0

        reclassified = 0
        for rule_data in parsed["rules"]:
            idx = rule_data.get("index", 0) - 1
            if 0 <= idx < len(learnings):
                cat = str(rule_data.get("category", "")).strip().lower()
                rule_text = rule_data.get("rule", "").strip()
                if cat in VALID_CATEGORIES and cat != "pattern" and rule_text:
                    learnings[idx].category = cat
                    learnings[idx].rule = rule_text
                    reclassified += 1

        db.session.commit()
        logger.info(f"[classifier] Reclassified {reclassified}/{len(learnings)} pattern learnings")

        # After rewriting rules, merge any that are now semantically identical
        if reclassified > 0:
            dedup_learnings()

        return reclassified

    except Exception as e:
        logger.warning(f"[classifier] Pattern learning reclassification failed: {e}")
        return 0


def classify_unclassified_signals(batch_size=20):
    """Find signals with category='pattern' (unclassified) and classify them via LLM.

    Returns: count of classified signals
    """
    # Find unclassified signals (category="pattern" is the default/unclassified value)
    signals = Signal.query.filter(
        Signal.category == "pattern",
        Signal.source_type == "pr_comment",
        Signal.aggregated == False,
    ).limit(batch_size).all()

    if not signals:
        return 0

    try:
        from planet_maiko.agents.brain_session import _get_runtime
        runtime = _get_runtime()
        if not runtime or not runtime.is_available():
            return 0

        # Build batch prompt
        items = []
        for i, s in enumerate(signals):
            items.append(f"{i+1}. [{s.repo or 'unknown'}] {s.text[:200]}")

        prompt = (
            "Classify each code review comment into exactly one category.\n\n"
            "Categories: null_safety, error_handling, testing, performance, "
            "api_design, architecture, security, style, naming, docs, "
            "domain_knowledge, pattern, gotcha\n\n"
            "Comments:\n" + "\n".join(items) + "\n\n"
            "Respond in JSON: {\"classifications\": [\"category1\", \"category2\", ...]}\n"
            "Return one category per comment, in order."
        )

        from planet_maiko.agents.routing import resolve_model
        result = runtime.send_json(prompt, timeout=30, model=resolve_model("classify"))

        # send_json returns {success, output, parsed} — the actual JSON is in parsed
        parsed = result.get("parsed") if isinstance(result, dict) else None
        if not parsed or "classifications" not in parsed:
            logger.warning(f"[classifier] No classifications in response: {result.get('error') if isinstance(result, dict) else result}")
            return 0

        classifications = parsed["classifications"]
        classified = 0
        for i, signal in enumerate(signals):
            if i < len(classifications):
                cat = str(classifications[i]).strip().lower()
                if cat in VALID_CATEGORIES:
                    signal.category = cat
                    classified += 1

        db.session.commit()
        logger.info(f"[classifier] Classified {classified}/{len(signals)} signals")
        return classified

    except Exception as e:
        logger.warning(f"[classifier] Batch classification failed: {e}")

    return 0


def dedup_learnings(batch_size=15, dry_run=False):
    """Merge semantically duplicate learnings using LLM comparison.

    Groups learnings by category+repo, sends each group to the LLM to
    identify duplicates, then merges them (summing signal counts and
    keeping the highest-confidence version).

    Args:
        batch_size: max learnings to process per group
        dry_run: if True, report merges without applying them

    Returns:
        dict with {groups_checked, merges, kept, dismissed}
    """
    from planet_maiko.models.learning import Learning
    from collections import defaultdict

    # Group active/pending learnings by category + repo
    learnings = Learning.query.filter(
        Learning.status.in_(["active", "pending"]),
    ).all()

    groups = defaultdict(list)
    for l in learnings:
        key = f"{l.category}|{l.scope_repo or '_global'}"
        groups[key].append(l)

    # Only process groups with potential dupes
    groups = {k: v for k, v in groups.items() if len(v) >= 2}

    if not groups:
        return {"groups_checked": 0, "merges": 0, "kept": 0, "dismissed": 0}

    try:
        from planet_maiko.agents.brain_session import _get_runtime
        runtime = _get_runtime()
        if not runtime or not runtime.is_available():
            logger.warning("[classifier] No runtime available for dedup")
            return {"groups_checked": 0, "merges": 0, "kept": 0, "dismissed": 0}
    except Exception:
        return {"groups_checked": 0, "merges": 0, "kept": 0, "dismissed": 0}

    from planet_maiko.agents.routing import resolve_model

    stats = {"groups_checked": 0, "merges": 0, "kept": 0, "dismissed": 0}

    for key, group in groups.items():
        if len(group) < 2:
            continue

        # Process in chunks to avoid timeouts on large groups
        chunks = [group[i:i+batch_size] for i in range(0, len(group), batch_size)]

        for chunk in chunks:
            if len(chunk) < 2:
                continue

            stats["groups_checked"] += 1

            items = []
            for i, l in enumerate(chunk):
                items.append(f"{i+1}. (id={l.id}, signals={l.signal_count}, conf={l.confidence:.1f}) {l.rule}")

            prompt = (
                "These rules are in the same category and repo. Identify which ones "
                "are expressing the SAME underlying rule in different words.\n\n"
                "Rules:\n" + "\n".join(items) + "\n\n"
                "Group duplicates together. For each group, pick the best-worded version "
                "as the keeper (prefer the one with more signals/higher confidence).\n\n"
                "Respond as JSON: {\"groups\": [[keeper_id, duplicate_id, ...], ...], "
                "\"unique\": [id, ...]}\n"
                "Put IDs that have no duplicates in \"unique\". "
                "Each group's first element is the keeper."
            )

            try:
                result = runtime.send_json(prompt, timeout=120, model=resolve_model("classify"))
                parsed = result.get("parsed") if isinstance(result, dict) else None
                if not parsed or "groups" not in parsed:
                    continue

                id_to_learning = {l.id: l for l in chunk}

                for merge_group in parsed["groups"]:
                    if len(merge_group) < 2:
                        continue

                    keeper_id = merge_group[0]
                    dupe_ids = merge_group[1:]

                    keeper = id_to_learning.get(keeper_id)
                    if not keeper:
                        continue

                    for dupe_id in dupe_ids:
                        dupe = id_to_learning.get(dupe_id)
                        if not dupe:
                            continue

                        if dry_run:
                            logger.info(f"[dedup] Would merge #{dupe.id} into #{keeper.id}: '{dupe.rule[:60]}' → '{keeper.rule[:60]}'")
                        else:
                            # Merge: absorb signals and dismiss the duplicate
                            keeper.signal_count += dupe.signal_count
                            keeper.confidence = min(1.0, keeper.confidence + dupe.confidence * 0.5)
                            if dupe.last_signal_at and (not keeper.last_signal_at or dupe.last_signal_at > keeper.last_signal_at):
                                keeper.last_signal_at = dupe.last_signal_at

                            # Reassign signals from dupe to keeper
                            Signal.query.filter_by(learning_id=dupe.id).update(
                                {"learning_id": keeper.id}, synchronize_session=False
                            )

                            dupe.status = "dismissed"
                            logger.info(f"[dedup] Merged #{dupe.id} into #{keeper.id}")

                        stats["merges"] += 1
                        stats["dismissed"] += 1

                    stats["kept"] += 1

            except Exception as e:
                logger.warning(f"[dedup] Failed on group {key}: {e}")
                continue

    if not dry_run:
        db.session.commit()

    logger.info(f"[dedup] Done: {stats}")
    return stats


def promote_global_rules(batch_size=15, dry_run=False):
    """Find rules that express the same thing across different repos and promote to global.

    Groups all active learnings by category (ignoring repo), asks the LLM
    which ones across different repos are the same rule, then merges them
    into a single global learning (scope_repo=NULL).

    Args:
        batch_size: max learnings per LLM call
        dry_run: if True, report what would be promoted without applying

    Returns:
        dict with {groups_checked, promoted, dismissed}
    """
    from planet_maiko.models.learning import Learning
    from collections import defaultdict

    # Group active learnings by category, but only those with a repo scope
    learnings = Learning.query.filter(
        Learning.status.in_(["active", "pending"]),
        Learning.scope_repo.isnot(None),
    ).all()

    # Group by category
    by_category = defaultdict(list)
    for l in learnings:
        by_category[l.category].append(l)

    # Only process categories that have rules from 2+ repos
    candidates = {}
    for cat, rules in by_category.items():
        repos = set(l.scope_repo for l in rules)
        if len(repos) >= 2:
            candidates[cat] = rules

    if not candidates:
        return {"groups_checked": 0, "promoted": 0, "dismissed": 0}

    try:
        from planet_maiko.agents.brain_session import _get_runtime
        runtime = _get_runtime()
        if not runtime or not runtime.is_available():
            return {"groups_checked": 0, "promoted": 0, "dismissed": 0}
    except Exception:
        return {"groups_checked": 0, "promoted": 0, "dismissed": 0}

    from planet_maiko.agents.routing import resolve_model

    stats = {"groups_checked": 0, "promoted": 0, "dismissed": 0}

    for cat, rules in candidates.items():
        chunks = [rules[i:i+batch_size] for i in range(0, len(rules), batch_size)]

        for chunk in chunks:
            if len(chunk) < 2:
                continue

            # Check that this chunk has rules from 2+ repos
            repos_in_chunk = set(l.scope_repo for l in chunk)
            if len(repos_in_chunk) < 2:
                continue

            stats["groups_checked"] += 1

            items = []
            for l in chunk:
                items.append(f"(id={l.id}, repo={l.scope_repo}, signals={l.signal_count}) {l.rule}")

            prompt = (
                "These rules are from DIFFERENT repos but the same category. "
                "Identify which ones express the SAME universal rule that should "
                "apply to ALL repos (not just the repo it was observed in).\n\n"
                "Rules:\n" + "\n".join(items) + "\n\n"
                "Group cross-repo duplicates together. For each group, pick the "
                "best-worded version as the keeper.\n\n"
                "IMPORTANT: Only group rules that are truly the same general principle. "
                "Repo-specific rules (referencing specific classes, services, or configs) "
                "should NOT be grouped.\n\n"
                "Respond as JSON: {\"global_groups\": [[keeper_id, dupe_id, ...], ...], "
                "\"repo_specific\": [id, ...]}\n"
                "Each group's first element is the keeper. "
                "Put rules that should stay repo-scoped in \"repo_specific\"."
            )

            try:
                result = runtime.send_json(prompt, timeout=120, model=resolve_model("classify"))
                parsed = result.get("parsed") if isinstance(result, dict) else None
                if not parsed or "global_groups" not in parsed:
                    continue

                id_to_learning = {l.id: l for l in chunk}

                for merge_group in parsed["global_groups"]:
                    if len(merge_group) < 2:
                        continue

                    keeper_id = merge_group[0]
                    dupe_ids = merge_group[1:]

                    keeper = id_to_learning.get(keeper_id)
                    if not keeper:
                        continue

                    # Check that this group actually spans repos
                    group_repos = set()
                    group_repos.add(keeper.scope_repo)
                    for did in dupe_ids:
                        d = id_to_learning.get(did)
                        if d:
                            group_repos.add(d.scope_repo)

                    if len(group_repos) < 2:
                        continue

                    if dry_run:
                        logger.info(f"[promote] Would promote #{keeper.id} to global: '{keeper.rule[:60]}' (from {group_repos})")
                    else:
                        # Promote keeper to global
                        keeper.scope_repo = None
                        keeper.source = "promoted"

                    for dupe_id in dupe_ids:
                        dupe = id_to_learning.get(dupe_id)
                        if not dupe:
                            continue

                        if dry_run:
                            logger.info(f"[promote] Would merge #{dupe.id} ({dupe.scope_repo}) into global #{keeper.id}")
                        else:
                            keeper.signal_count += dupe.signal_count
                            keeper.confidence = min(1.0, keeper.confidence + dupe.confidence * 0.5)
                            if dupe.last_signal_at and (not keeper.last_signal_at or dupe.last_signal_at > keeper.last_signal_at):
                                keeper.last_signal_at = dupe.last_signal_at

                            Signal.query.filter_by(learning_id=dupe.id).update(
                                {"learning_id": keeper.id}, synchronize_session=False
                            )
                            dupe.status = "dismissed"
                            logger.info(f"[promote] Merged #{dupe.id} into global #{keeper.id}")

                        stats["dismissed"] += 1

                    stats["promoted"] += 1

            except Exception as e:
                logger.warning(f"[promote] Failed on category {cat}: {e}")
                continue

    if not dry_run:
        db.session.commit()

    logger.info(f"[promote] Done: {stats}")
    return stats
