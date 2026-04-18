"""Pet records — a user tapped Maiko's avatar to send her love.

Every tap is one row. The deployment-wide counter the Home page
shows is a count of today's rows; the owner's Pet Log is the full
feed. Per-user daily cap is enforced at write time (see
/api/maiko/pet), not via a schema constraint, because the cap is
a config value that can change.

`user_key` is a soft identifier for who the petter is. For a
solo deployment it's always "self" (or whatever the user's
configured name is); for a shared instance it could be a session
ID, IP hash, or similar. Kept as a plain string so no new auth
surface is needed — the counter and log tolerate repeats from
the same key just fine.

`marked_irl_at` is set by the deployment owner when they've
actually petted IRL Maiko in response. Lets the log surface
"still owed" vs "delivered" without scanning timestamps.
"""

from datetime import datetime, timezone
from planet_maiko.database import db, iso_utc


class Pet(db.Model):
    __tablename__ = "pets"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_key = db.Column(db.String(128), default="self", nullable=False, index=True)
    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    marked_irl_at = db.Column(db.DateTime, nullable=True)
    # Optional free-text note the petter can leave ("for being a good girl
    # today", etc.). Stays empty for the one-click path; reserved for a
    # future "leave a note with your pet" affordance.
    note = db.Column(db.String(256), nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "user_key": self.user_key,
            "created_at": iso_utc(self.created_at),
            "marked_irl_at": iso_utc(self.marked_irl_at),
            "note": self.note or "",
        }
