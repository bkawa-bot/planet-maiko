import { useNavigate } from "react-router-dom";
import { Brain } from "lucide-react";

/**
 * "N learnings waiting for your nod" call-out card. Click-through
 * to /knowledge?tab=pending where the user approves or dismisses
 * in bulk. Hidden when the count is 0.
 */
export default function LearningsCard({ count, preview }) {
  const navigate = useNavigate();
  if (!count) return null;
  return (
    <section className="overview-section">
      <h2 className="overview-section-title">New learnings to review</h2>
      <div
        className="overview-learnings-card"
        onClick={() => navigate("/knowledge?tab=pending")}
      >
        <div className="overview-learnings-icon"><Brain size={16} /></div>
        <div className="overview-learnings-body">
          <div className="overview-learnings-count">
            {count} learning{count === 1 ? "" : "s"} waiting for your nod
          </div>
          <ul className="overview-learnings-preview">
            {(preview || []).slice(0, 3).map((l) => (
              <li key={l.id}>{l.rule}</li>
            ))}
            {count > 3 && (
              <li className="overview-learnings-more">
                + {count - 3} more
              </li>
            )}
          </ul>
        </div>
      </div>
    </section>
  );
}
