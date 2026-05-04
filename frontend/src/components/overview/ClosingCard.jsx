import { useNavigate } from "react-router-dom";

/**
 * "Enough for today" closing card. Shows up around the user's
 * configured workday_end_hour with permission to stop, plus an
 * "overnight" list of agents still working so they don't feel
 * abandoned. Lives at the bottom of the overview pane.
 */
export default function ClosingCard({ closing, overnight }) {
  const navigate = useNavigate();
  if (!closing) return null;
  return (
    <section className="overview-closing">
      <div className="overview-closing-label">Enough for today</div>
      <p className="overview-closing-body">{closing}</p>
      {overnight?.length > 0 && (
        <div className="overview-overnight">
          <div className="overview-overnight-label">Continuing overnight</div>
          <ul className="overview-overnight-list">
            {overnight.map((t) => (
              <li key={t.task_id} className="overview-overnight-item">
                <span className="overview-overnight-title">{t.title}</span>
                {t.agent_name && (
                  <span className="overview-overnight-agent">— {t.agent_name}</span>
                )}
              </li>
            ))}
          </ul>
          <a
            className="overview-overnight-link"
            onClick={(e) => { e.preventDefault(); navigate("/tasks"); }}
            href="/tasks"
          >
            open Tasks to amend
          </a>
        </div>
      )}
    </section>
  );
}
