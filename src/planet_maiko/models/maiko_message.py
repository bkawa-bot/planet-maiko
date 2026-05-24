from datetime import datetime, timezone

from planet_maiko.database import db, iso_utc


class MaikoMessage(db.Model):
    """One turn in the global conversation with Maiko (the controller,
    not a worker agent). Distinct from AgentMessage, which is
    task-scoped chatter with a specific agent on a specific job.
    """

    __tablename__ = "maiko_messages"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    # "user" = the human's turn, "maiko" = her reply.
    role = db.Column(db.String(10), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    def to_dict(self):
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "created_at": iso_utc(self.created_at),
        }
