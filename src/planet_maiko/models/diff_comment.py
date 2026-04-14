from datetime import datetime, timezone
from planet_maiko.database import db


class DiffComment(db.Model):
    """An inline comment on an agent's diff.

    Anchored to (task, file_path, line_number, side). Side is "old" or
    "new" to match git's pre-image / post-image — most user comments
    land on the "new" side since you're reviewing added code, but
    comments on removed lines are sometimes the right call.

    status lifecycle:
        draft     — locally authored, not yet shown to the agent
        submitted — pushed to the agent via /review/request-changes
        resolved  — user marked it addressed
        outdated  — the hunk the comment was anchored to changed in a
                    later commit (set automatically, kept for history)

    author is "user" (typed in the review UI) or "agent" (left via the
    leave_comment MCP tool to flag uncertain spots for the reviewer).
    parent_id supports single-level threads so replies on the same
    anchor stay grouped.
    """
    __tablename__ = "diff_comments"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    task_id = db.Column(db.String(128), db.ForeignKey("tasks.id"), nullable=False, index=True)

    file_path = db.Column(db.String(512), nullable=False)
    line_number = db.Column(db.Integer, nullable=False)
    side = db.Column(db.String(3), default="new", nullable=False)  # "old" | "new"

    # The commit sha the comment was anchored to. When the agent amends,
    # comments with a stale base_sha get flipped to "outdated" (still
    # displayed, but de-emphasized).
    base_sha = db.Column(db.String(40), nullable=True)

    body = db.Column(db.Text, nullable=False)

    parent_id = db.Column(db.Integer, db.ForeignKey("diff_comments.id"), nullable=True, index=True)

    status = db.Column(db.String(16), default="draft", nullable=False, index=True)
    # draft | submitted | resolved | outdated
    author = db.Column(db.String(8), default="user", nullable=False)
    # user | agent

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id": self.id,
            "task_id": self.task_id,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "side": self.side,
            "base_sha": self.base_sha,
            "body": self.body,
            "parent_id": self.parent_id,
            "status": self.status,
            "author": self.author,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
