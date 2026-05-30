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

// An edge A -> B is valid when A's output kind is one of the kinds B
// accepts. Each role declares its own accept-set (AgentType.accepts),
// so this is a plain membership check with no hidden global rules: a
// Coder that accepts ["task", "plan", "report"] takes a Planner's plan
// because "plan" is literally in its list.
export function edgeValid(outputKind, accepts) {
  return Array.isArray(accepts) && accepts.includes(outputKind);
}
