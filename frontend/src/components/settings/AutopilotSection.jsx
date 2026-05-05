/**
 * Autopilot — what Maiko does without asking you.
 *
 * Three knobs:
 *   - auto-investigate incidents (correlator → investigation task)
 *   - cartographer auto-proposals (stale repo overviews suggest
 *     refresh tasks)
 *   - 1h prompt cache (cost knob — Anthropic's longer cache TTL,
 *     usually wins on multi-turn agent runs)
 */
export default function AutopilotSection({ config, setConfig }) {
  const auto = config.brain?.auto_investigate || {};
  const autoEnabled = auto.enabled ?? true;
  const cartographer = config.brain?.role_autonomy?.cartographer || {};
  const cartographerEnabled = cartographer.enabled ?? true;
  const promptCache1h = !!config.brain?.prompt_cache_1h;

  const updateAuto = (patch) => setConfig((c) => ({
    ...c,
    brain: {
      ...(c.brain || {}),
      auto_investigate: { ...(c.brain?.auto_investigate || {}), ...patch },
    },
  }));

  const updateCartographer = (patch) => setConfig((c) => ({
    ...c,
    brain: {
      ...(c.brain || {}),
      role_autonomy: {
        ...(c.brain?.role_autonomy || {}),
        cartographer: { ...(c.brain?.role_autonomy?.cartographer || {}), ...patch },
      },
    },
  }));

  const updatePromptCache = (value) => setConfig((c) => ({
    ...c,
    brain: { ...(c.brain || {}), prompt_cache_1h: value },
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
                checked={autoEnabled}
                onChange={(e) => updateAuto({ enabled: e.target.checked })}
              />
              Auto-investigate incidents
            </label>
            <label style={{ opacity: autoEnabled ? 1 : 0.5 }}>
              <input
                type="checkbox"
                checked={auto.dry_run ?? false}
                disabled={!autoEnabled}
                onChange={(e) => updateAuto({ dry_run: e.target.checked })}
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
                onChange={(e) => updateAuto({ daily_budget: parseInt(e.target.value) || 5 })}
              />
            </label>
          </div>
        </div>

        <div className="integration-section" style={{ marginTop: 18 }}>
          <div className="setup-hint">
            Cartographer auto-proposals — when a repo's overview goes stale,
            the cartographer agent proposes a refresh task into your inbox.
            Nothing fires without your click on the proposal. Turn off if
            the inbox feels noisy.
          </div>
          <div className="integration-fields">
            <label>
              <input
                type="checkbox"
                checked={cartographerEnabled}
                onChange={(e) => updateCartographer({ enabled: e.target.checked })}
              />
              Propose overview refreshes when repos go stale
            </label>
            <label style={{ opacity: cartographerEnabled ? 1 : 0.5 }}>
              Stale after — days since the last cartograph
              <input
                type="number"
                min="1"
                max="365"
                disabled={!cartographerEnabled}
                value={cartographer.stale_days ?? 30}
                onChange={(e) => updateCartographer({ stale_days: parseInt(e.target.value) || 30 })}
              />
            </label>
          </div>
        </div>

        <div className="integration-section" style={{ marginTop: 18 }}>
          <div className="setup-hint">
            1-hour prompt cache. Anthropic's longer cache TTL — agent
            sessions typically span 5-60 min and re-read the same preamble
            (role protocol + TASK.md + skill prompt), so 1h caching usually
            wins on cost after the second read. Turn off if you're on a
            metered plan and want to measure before committing.
          </div>
          <div className="integration-fields">
            <label>
              <input
                type="checkbox"
                checked={promptCache1h}
                onChange={(e) => updatePromptCache(e.target.checked)}
              />
              Use 1-hour prompt cache (passes ENABLE_PROMPT_CACHING_1H=1 to Claude Code)
            </label>
          </div>
        </div>
      </div>
    </section>
  );
}
