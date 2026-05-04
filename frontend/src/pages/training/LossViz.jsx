/**
 * Training-progress visualizations: the loss sparkline + the
 * overfit-hint badge. Pure presentation — both take loss numbers
 * as props and render SVG/labels.
 */

export function LossSparkline({ history, totalIters }) {
  if (!history || history.length < 2) return null;

  const W = 280;
  const H = 64;
  const PAD = 4;

  // Auto-scale Y to the highest observed loss; clamp the floor so a
  // perfectly converged run still has visible amplitude.
  const allLosses = history.flatMap((p) => [p.train, p.val].filter((v) => v != null));
  if (!allLosses.length) return null;
  const yMax = Math.max(...allLosses, 0.1) * 1.05;
  const yMin = 0;

  const xMax = totalIters || history[history.length - 1].iter || 1;
  const xOf = (it) => PAD + (it / xMax) * (W - 2 * PAD);
  const yOf = (loss) => H - PAD - ((loss - yMin) / (yMax - yMin)) * (H - 2 * PAD);

  const buildPath = (key) => {
    let d = "";
    let started = false;
    for (const point of history) {
      const v = point[key];
      if (v == null) continue;
      const x = xOf(point.iter);
      const y = yOf(v);
      d += `${started ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)} `;
      started = true;
    }
    return d.trim();
  };

  const trainPath = buildPath("train");
  const valPath = buildPath("val");

  return (
    <svg
      className="loss-sparkline"
      width={W}
      height={H}
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      role="img"
      aria-label="Train and validation loss over training iterations"
    >
      {/* Y-axis baseline at 0 for visual anchor */}
      <line x1={PAD} y1={H - PAD} x2={W - PAD} y2={H - PAD} className="loss-sparkline-axis" />
      {trainPath && <path d={trainPath} className="loss-sparkline-line loss-sparkline-train" />}
      {valPath && <path d={valPath} className="loss-sparkline-line loss-sparkline-val" />}
    </svg>
  );
}



// Hint surfaced beneath the sparkline. Keeps the read short — the
// chart is the canonical signal; this just calls out a number when
// it crosses an obvious threshold.
export function OverfitHint({ trainLoss, valLoss }) {
  if (trainLoss == null || valLoss == null) return null;
  const gap = valLoss - trainLoss;
  let label, severity;
  if (gap < 0.1) { label = "healthy"; severity = "ok"; }
  else if (gap < 0.3) { label = "watching"; severity = "warn"; }
  else { label = "overfit suspected"; severity = "high"; }
  return (
    <div className={`overfit-hint overfit-hint-${severity}`}>
      val − train = {gap.toFixed(2)} · {label}
    </div>
  );
}
