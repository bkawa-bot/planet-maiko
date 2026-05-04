"""Templating + pupdate-snapshot helpers shared by every action handler.

Lives in its own file so cycle.py and pupdate.py can both import it
without going through the package __init__ (which would create a
circular import via ACTIONS).
"""


def _interpolate(template, pupdate=None, context=None):
    """Substitute {pupdate_title}, {pupdate_body}, {pupdate_url},
    {repo}, {task_title} into a notify template.

    Tokens that can't be resolved pass through as the empty string;
    we'd rather the user see "PR  was reviewed" with a visual gap
    than a crash or a literal "{pupdate_title}" showing up in the
    notification body. Missing-data blanks are a clearer bug signal.
    """
    if not template:
        return ""
    ctx = context or {}
    mapping = {
        "pupdate_title": getattr(pupdate, "title", "") or "" if pupdate else "",
        # 2000 chars is enough for most PR descriptions / incident
        # bodies / Linear issue bodies to fit without cutting context
        # the user is trying to read.
        "pupdate_body": (getattr(pupdate, "body", "") or "")[:2000] if pupdate else "",
        "pupdate_url": getattr(pupdate, "url", "") or "" if pupdate else "",
        "repo": (ctx.get("repo")
                 or ctx.get("service")
                 or (getattr(pupdate, "extra", {}) or {}).get("repo")
                 or ""),
        "task_title": ctx.get("task_title", ""),
    }
    out = template
    for key, value in mapping.items():
        out = out.replace("{" + key + "}", str(value))
    return out


def _pupdate_snapshot(pupdate):
    """Extract a pupdate's full surface for downstream consumers (memos,
    agent jobs, task extras). Includes the raw `extra` blob — different
    skills key off different metadata fields (pr-review needs `url`;
    investigate wants `body`; correlator-style skills might key on tags
    or extra.repo / extra.pr_number), so plumb it all and let the
    consumer pick.
    """
    if pupdate is None:
        return None
    body = getattr(pupdate, "body", None) or ""
    return {
        "id": getattr(pupdate, "id", None),
        "type": getattr(pupdate, "type", None),
        "title": (getattr(pupdate, "title", None) or "")[:300],
        # Full body here — the memo's top-level body field may have
        # been templated/truncated by the user's copy; this keeps the
        # raw text available for context.
        "body": body[:4000],
        "url": getattr(pupdate, "url", None),
        "source": getattr(pupdate, "source", None),
        "priority": getattr(pupdate, "priority", None),
        "tags": list(getattr(pupdate, "tags", None) or []),
        "timestamp": (
            pupdate.timestamp.isoformat()
            if getattr(pupdate, "timestamp", None) and hasattr(pupdate.timestamp, "isoformat")
            else None
        ),
        "extra": dict(getattr(pupdate, "extra", None) or {}),
    }


def format_pupdate_for_context(snapshot):
    """Render a pupdate snapshot dict as a markdown block for skill
    prompts. Ships every field — skills decide what to use. Returns
    an empty string when snapshot is None / empty so callers can
    unconditionally splice the result into a `{context}` placeholder.
    """
    if not snapshot:
        return ""
    lines = ["### Triggered by pupdate"]
    for key, label in (
        ("type", "Type"),
        ("source", "Source"),
        ("title", "Title"),
        ("url", "URL"),
        ("priority", "Priority"),
        ("timestamp", "Created"),
    ):
        value = snapshot.get(key)
        if value:
            lines.append(f"{label}: {value}")
    tags = snapshot.get("tags") or []
    if tags:
        lines.append(f"Tags: {', '.join(str(t) for t in tags)}")
    extra = snapshot.get("extra") or {}
    if extra:
        # Flatten one level — pupdate.extra is mostly key/value scalars
        # (repo, pr_number, head_sha, identifier, etc.). For nested
        # values, fall back to JSON so the structure stays readable.
        import json as _json
        lines.append("Metadata:")
        for k, v in extra.items():
            if isinstance(v, (dict, list)):
                v_str = _json.dumps(v, default=str)
            else:
                v_str = str(v)
            lines.append(f"  {k}: {v_str}")
    body = (snapshot.get("body") or "").strip()
    if body:
        lines.append("")
        lines.append("Body:")
        lines.append(body)
    return "\n".join(lines)
