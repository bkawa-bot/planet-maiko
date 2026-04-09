"""Project driver — auto-advances projects through phases."""

import logging
import uuid
from planet_maiko.database import db
from planet_maiko.models.project import Project
from planet_maiko.models.task import Task
from planet_maiko.models.pupdate import Pupdate
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def drive_projects():
    """Check active projects and advance phases when ready.

    Returns: dict with counts of advanced/completed projects
    """
    projects = Project.query.filter_by(status="active").all()
    advanced = 0
    completed = 0

    for project in projects:
        phases = project.phases or []
        if not phases:
            continue

        current = project.current_phase or 0
        if current >= len(phases):
            # All phases done
            if project.status != "done":
                project.status = "done"
                project.updated_at = datetime.now(timezone.utc)
                completed += 1
                _notify_project_done(project)
            continue

        phase = phases[current]
        if phase.get("status") == "done":
            # Current phase done — advance to next
            next_idx = current + 1
            if next_idx < len(phases):
                phases[next_idx]["status"] = "active"
                project.current_phase = next_idx
                project.phases = list(phases)  # copy for SQLAlchemy
                project.updated_at = datetime.now(timezone.utc)
                advanced += 1
                _notify_phase_advanced(project, phases[next_idx], next_idx)
                _create_phase_task(project, phases[next_idx], next_idx)
            else:
                project.status = "done"
                project.updated_at = datetime.now(timezone.utc)
                completed += 1
                _notify_project_done(project)
            continue

        # Check if current phase's tasks are all done
        if phase.get("status") == "active":
            phase_tasks = Task.query.filter_by(
                project_id=project.id,
            ).filter(
                Task.extra.contains({"phase_number": current})
            ).all()

            if phase_tasks and all(t.status in ("done", "cancelled") for t in phase_tasks):
                phases[current]["status"] = "done"
                project.phases = list(phases)
                # Will advance on next cycle

    if advanced or completed:
        db.session.commit()
        logger.info(f"[driver] Advanced {advanced} phase(s), completed {completed} project(s)")

    return {"advanced": advanced, "completed": completed}


def _notify_phase_advanced(project, phase, phase_idx):
    """Create a pupdate notifying that a project phase advanced."""
    notify = Pupdate(
        id=f"phase-{project.id}-{phase_idx}-{uuid.uuid4().hex[:8]}",
        source="maiko",
        type="project_phase_advanced",
        priority="normal",
        title=f"Project '{project.title}' — Phase {phase_idx + 1}: {phase.get('title', 'Next phase')}",
        body=phase.get("description", ""),
        tags=[project.id, "project", "phase"],
        extra={"project_id": project.id, "phase_number": phase_idx},
    )
    db.session.add(notify)


def _notify_project_done(project):
    """Create a pupdate notifying that a project completed."""
    notify = Pupdate(
        id=f"project-done-{project.id}-{uuid.uuid4().hex[:8]}",
        source="maiko",
        type="project_completed",
        priority="normal",
        title=f"Project completed: {project.title}",
        body=f"All phases done for project {project.id}.",
        tags=[project.id, "project", "completed"],
        extra={"project_id": project.id},
    )
    db.session.add(notify)


def _create_phase_task(project, phase, phase_idx):
    """Auto-create a task for the new active phase."""
    task_id = f"task-{project.id}-phase-{phase_idx}"
    existing = db.session.get(Task, task_id)
    if existing:
        return

    task = Task(
        id=task_id,
        title=f"[{project.title}] Phase {phase_idx + 1}: {phase.get('title', '')}",
        type="todo",
        priority=project.priority or "normal",
        project_id=project.id,
        url=project.source_url,
        tags=[project.id, f"phase-{phase_idx}"],
        extra={"phase_number": phase_idx, "phase_title": phase.get("title", "")},
    )
    db.session.add(task)
