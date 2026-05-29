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
