/**
 * Autopilot — master switch for auto-investigating incidents.
 *
 * When the correlator detects an incident chain (CI fail + deploy
 * rollback, etc.), Maiko can auto-create an investigation task and
 * kick off an investigator. Off = manual triage of every incident.
 */
export default function AutopilotSection({ config, setConfig }) {
  const auto = config.brain?.auto_investigate || {};
  const enabled = auto.enabled ?? true;

  const update = (patch) => setConfig((c) => ({
    ...c,
    brain: {
      ...(c.brain || {}),
      auto_investigate: { ...(c.brain?.auto_investigate || {}), ...patch },
    },
  }));

  return (
    <section className="settings-collapsible">
      <div className="collapsible-header" style={{ cursor: "default" }}>
        <span>Autopilot</span>
      </div>
      <div className="collapsible-body">
        <div className="integration-section">
          <div className="setup-hint">
            When the correlator detects an incident (CI fail + deploy rollback,
            error spike chain, etc.), Maiko can auto-create an investigation
            task and kick off an investigation agent on it. Turn this off to
            require manual triage of every incident.
          </div>
          <div className="integration-fields">
            <label>
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => update({ enabled: e.target.checked })}
              />
              Auto-investigate incidents
            </label>
            <label style={{ opacity: enabled ? 1 : 0.5 }}>
              <input
                type="checkbox"
                checked={auto.dry_run ?? false}
                disabled={!enabled}
                onChange={(e) => update({ dry_run: e.target.checked })}
              />
              Dry-run only (create the task so you can see what would've fired, skip the agent kickoff)
            </label>
            <label>
              Daily budget — hard stop after N auto-investigations per day
              <input
                type="number"
                min="1"
                max="50"
                value={auto.daily_budget ?? 5}
                onChange={(e) => update({ daily_budget: parseInt(e.target.value) || 5 })}
              />
            </label>
          </div>
        </div>
      </div>
    </section>
  );
}
