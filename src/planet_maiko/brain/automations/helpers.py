"""Shared helpers for the automation engine.

Both conditions and actions need template-formatting + pupdate
snapshot serialization. Lives here so neither module has to import
the other.
"""

def _safe_format(template, context):
    """Substitute {key} placeholders in a template string with values
    from `context`. Missing keys render as "(unknown)" rather than
    raising — automation text should degrade gracefully when upstream
    shape shifts, not crash the cycle.
    """
    if not template or not isinstance(template, str):
        return template
    if "{" not in template:
        return template
    try:
        class _Defaulting(dict):
            def __missing__(self, key):  # noqa: D401
                return "(unknown)"
        return template.format_map(_Defaulting(context or {}))
    except Exception:
        return template