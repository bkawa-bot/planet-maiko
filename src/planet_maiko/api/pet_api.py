"""Pet Maiko — the deployment-wide shared-love counter.

Public shape (what any user sees):

- POST /api/maiko/pet         — tap. Enforces per-user daily cap.
- GET  /api/maiko/pets/count  — today's total + lifetime total,
                                plus caller's remaining pets. When a
                                cross-deployment aggregator is
                                configured (config.pets.aggregator_url),
                                the response also carries the global
                                totals so Home can show "Maiko got N
                                pets from the pack today."

Owner shape (for the person maintaining the deployment, and therefore
the person who owes IRL Maiko the pets):

- GET  /api/maiko/pets/log          — paginated feed with who/when
- POST /api/maiko/pets/:id/mark_irl — mark a pet as delivered IRL

The "owner" check is deliberately not auth — this is a personal tool.
Anyone running Maiko on their own machine is the owner of that
deployment. In shared-deployment scenarios the log endpoint is still
technically reachable; treat user.name as the soft owner marker.
"""

import logging
import threading
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from planet_maiko.config import load_config, user_now
from planet_maiko.database import db
from planet_maiko.models.pet import Pet

logger = logging.getLogger(__name__)

pet_bp = Blueprint("pet", __name__)


def _user_key():
    """Soft identifier for the caller — no auth, just provenance.

    Prefers an explicit X-User header (useful if Maiko ever gets a
    simple session layer), falls back to the user.name in config, and
    finally "self" for the solo case. Used only for log provenance +
    daily-cap accounting, not for access control.
    """
    header = (request.headers.get("X-User") or "").strip()
    if header:
        return header[:128]
    try:
        name = (load_config().get("user", {}) or {}).get("name", "").strip()
        if name:
            return name[:128]
    except Exception:
        pass
    return "self"


def _today_midnight_utc():
    now_local = user_now()
    midnight_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight_local.astimezone(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Global counter aggregator — optional cross-deployment counter.
#
# Hits a Cloudflare Worker (deploy/cloudflare-worker/pet-counter.js) that
# proxies Upstash Redis. The Worker holds the Upstash creds as secrets;
# this client just calls two scoped endpoints (POST /incr, GET /counts)
# and can't reach anything else in Redis. That keeps the AGPL repo
# free of any deployable-auth-credential — shipping an Upstash token
# publicly would let anyone FLUSHDB or spam arbitrary keys.
#
# When aggregator_url is null, Maiko runs local-only: pets still work,
# the Home widget just omits the "from the pack" numbers.
# ---------------------------------------------------------------------------


def _aggregator_url():
    """Return the worker URL, or None when disabled."""
    pets_cfg = load_config().get("pets", {}) or {}
    url = (pets_cfg.get("aggregator_url") or "").strip().rstrip("/")
    return url or None


def _aggregator_incr_async():
    """Fire-and-forget INCR to the worker. Never blocks the caller.

    The Worker handles the Redis-side details (two INCRs + a TTL
    refresh); this client just pings POST /incr. All calls swallow
    exceptions — a broken aggregator must never take down the
    user-facing POST /maiko/pet.
    """
    url = _aggregator_url()
    if not url:
        return

    def _do():
        try:
            import requests  # local import — only needed when aggregator is on
            requests.post(f"{url}/incr", timeout=3)
        except Exception as e:
            logger.debug(f"[pets] aggregator INCR failed (non-fatal): {e}")

    threading.Thread(target=_do, daemon=True, name="pets-aggregator-incr").start()


def _aggregator_get_counts():
    """Best-effort GET of today + lifetime counts. Returns dict or None.

    Worker response shape: { "global_today": int, "global_lifetime": int }.
    Returns None on any failure (network, parse) so the caller can
    fall back to local-only counts.
    """
    url = _aggregator_url()
    if not url:
        return None
    try:
        import requests
        resp = requests.get(f"{url}/counts", timeout=3)
        data = resp.json()
        return {
            "global_today": _coerce_int(data.get("global_today")),
            "global_lifetime": _coerce_int(data.get("global_lifetime")),
        }
    except Exception as e:
        logger.debug(f"[pets] aggregator GET failed (non-fatal): {e}")
        return None


def _coerce_int(value):
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


@pet_bp.route("/maiko/pet", methods=["POST"])
def pet_maiko():
    """Record one pet. Respects user.pet_daily_cap.

    Body is optional: {"note": "for being a good girl"}
    """
    cfg = load_config().get("user", {}) or {}
    cap = cfg.get("pet_daily_cap")
    if cap is None:
        return jsonify({"error": "petting is disabled on this deployment"}), 403

    user_key = _user_key()
    midnight = _today_midnight_utc()

    given_today = (
        Pet.query
        .filter(Pet.user_key == user_key)
        .filter(Pet.created_at >= midnight)
        .count()
    )
    if given_today >= cap:
        return jsonify({
            "error": "daily pet cap reached",
            "cap": cap,
            "given_today": given_today,
            "message": "Maiko's had enough love from you for today — see you tomorrow.",
        }), 429

    data = request.get_json(silent=True) or {}
    note = (data.get("note") or "").strip() or None
    if note and len(note) > 256:
        note = note[:256]

    pet = Pet(user_key=user_key, note=note)
    db.session.add(pet)
    db.session.commit()

    # Fire-and-forget INCR to the cross-deployment aggregator (if
    # configured). Runs on a daemon thread so a slow or down
    # aggregator never blocks the user's pet.
    _aggregator_incr_async()

    return jsonify({
        "id": pet.id,
        "remaining_today": max(0, cap - given_today - 1),
        "cap": cap,
        "message": "Maiko wags happily.",
    }), 201


@pet_bp.route("/maiko/pets/count", methods=["GET"])
def pets_count():
    """Deployment-wide counters the Home widget displays.

    Returns today's total across all users, lifetime total, and the
    caller's remaining pets for the day. Cheap — one aggregate query
    per metric plus the caller's own count.
    """
    cfg = load_config().get("user", {}) or {}
    cap = cfg.get("pet_daily_cap")
    user_key = _user_key()
    midnight = _today_midnight_utc()

    today = Pet.query.filter(Pet.created_at >= midnight).count()
    lifetime = Pet.query.count()
    yours_today = (
        Pet.query
        .filter(Pet.user_key == user_key)
        .filter(Pet.created_at >= midnight)
        .count()
    )
    remaining = None if cap is None else max(0, cap - yours_today)

    payload = {
        "today": today,            # this deployment's today
        "lifetime": lifetime,      # this deployment's lifetime
        "your_today": yours_today,
        "your_remaining": remaining,
        "cap": cap,
    }

    # When the aggregator is configured, also fetch cross-deployment
    # counts so the Home widget can show the "from the pack" number.
    # Local counts stay in the response so the UI can fall back
    # gracefully if the aggregator is unreachable.
    global_counts = _aggregator_get_counts()
    if global_counts is not None:
        payload.update(global_counts)

    return jsonify(payload)


@pet_bp.route("/maiko/pets/log", methods=["GET"])
def pets_log():
    """Owner-view feed: every pet with who + when + IRL-delivery state.

    Query:
        limit (int, default 100, max 500)
        offset (int, default 0)
        unacked (bool) — if true, only pets without marked_irl_at
    """
    limit = min(int(request.args.get("limit") or 100), 500)
    offset = int(request.args.get("offset") or 0)
    unacked = (request.args.get("unacked") or "").lower() == "true"

    query = Pet.query
    if unacked:
        query = query.filter(Pet.marked_irl_at.is_(None))
    rows = (
        query.order_by(Pet.created_at.desc())
        .limit(limit).offset(offset)
        .all()
    )
    return jsonify([p.to_dict() for p in rows])


@pet_bp.route("/maiko/pets/<int:pet_id>/mark_irl", methods=["POST"])
def mark_irl(pet_id):
    """Owner marks a pet as delivered IRL."""
    pet = db.session.get(Pet, pet_id)
    if pet is None:
        return jsonify({"error": f"Pet {pet_id} not found"}), 404
    if pet.marked_irl_at is not None:
        return jsonify(pet.to_dict())  # idempotent

    pet.marked_irl_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify(pet.to_dict())


@pet_bp.route("/maiko/pets/mark_all_irl", methods=["POST"])
def mark_all_irl():
    """Bulk mark — owner's "just did a group pet session" action."""
    now = datetime.now(timezone.utc)
    updated = (
        Pet.query
        .filter(Pet.marked_irl_at.is_(None))
        .update({"marked_irl_at": now}, synchronize_session=False)
    )
    db.session.commit()
    return jsonify({"marked": updated})
