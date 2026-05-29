// Socket color per kind, drawn from the Maiko palette so matching
// kinds read as "these connect." Shared by the node sockets and the
// edges, so a wire is the same color as the two doorways it joins.
export const KIND_COLOR = {
  task: "var(--lavender)",
  plan: "var(--blue)",
  diff: "var(--pink)",
  report: "var(--peach)",
  insight: "var(--lemon)",
  incident: "var(--urgent)",
  repo: "var(--green)",
};

export function kindColor(kind) {
  return KIND_COLOR[kind] || "var(--text-muted)";
}

// Which producer output kinds satisfy a given consumer input kind. A
// role declares one primary input_kind; this map says what actually
// plugs into it. A task-driven role (a coder) also takes a plan or a
// report as its marching orders (so planner -> coder is a valid edge);
// everything else is exact-match.
export const ACCEPTS = {
  task: ["task", "plan", "report"],
  plan: ["plan"],
  diff: ["diff"],
  report: ["report"],
  insight: ["insight", "repo"],
  incident: ["incident"],
  repo: ["repo"],
};

export function edgeValid(outputKind, inputKind) {
  const allowed = ACCEPTS[inputKind] || [inputKind];
  return allowed.includes(outputKind);
}
