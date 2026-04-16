from datetime import datetime, timezone
from planet_maiko.database import db, iso_utc


class CustomSkill(db.Model):
    """User-editable skill prompt.

    Skills are prompt templates that the brain session executes.
    Default skills are seeded on first run. Users can edit them
    or create their own.

    The prompt can reference context variables like {pupdates},
    {tasks}, {calendar} and can mention MCPs to use.
    """
    __tablename__ = "custom_skills"

    id = db.Column(db.String(50), primary_key=True)  # e.g. "morning-brief"
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(256), nullable=True)
    prompt = db.Column(db.Text, nullable=False)
    mcps = db.Column(db.JSON, default=list)  # ["slack", "figma", "linear"]
    icon = db.Column(db.String(20), default="wand")  # lucide icon name
    is_default = db.Column(db.Boolean, default=False)  # shipped with Maiko
    user_edited = db.Column(db.Boolean, default=False)  # True once user edits the prompt
    schedule_interval_minutes = db.Column(db.Integer, nullable=True)  # null = manual only
    creates_pupdates = db.Column(db.Boolean, default=False)  # parse output into pupdates
    last_run_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "prompt": self.prompt,
            "mcps": self.mcps,
            "icon": self.icon,
            "is_default": self.is_default,
            "user_edited": self.user_edited,
            "schedule_interval_minutes": self.schedule_interval_minutes,
            "creates_pupdates": self.creates_pupdates,
            "last_run_at": iso_utc(self.last_run_at),
            "created_at": iso_utc(self.created_at),
            "updated_at": iso_utc(self.updated_at),
        }
