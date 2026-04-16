from datetime import datetime, timezone
from planet_maiko.database import db, iso_utc


class AgentMessage(db.Model):
    __tablename__ = "agent_messages"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    task_id = db.Column(db.String(128), nullable=False, index=True)
    direction = db.Column(db.String(10), nullable=False)  # "to_agent" or "from_agent"
    sender = db.Column(db.String(50), nullable=False)  # "brain", "user", "agent"
    content = db.Column(db.Text, nullable=False)
    message_type = db.Column(db.String(50), default="message")  # message, directive, context, stop
    read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "task_id": self.task_id,
            "direction": self.direction,
            "sender": self.sender,
            "content": self.content,
            "message_type": self.message_type,
            "read": self.read,
            "created_at": iso_utc(self.created_at),
        }
