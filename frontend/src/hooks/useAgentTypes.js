import { useState, useEffect } from "react";
import {
  Code2, Eye, Search, Map as MapIcon, User, Compass, Wand2,
  GitPullRequest, FileText, Clipboard, Bot,
} from "@icons";
import { api } from "../api/client";

// Module-level cache. Agent types rarely change at runtime; refresh
// on full reload. Pattern mirrors useCards.js.
let _typesCache = null;
let _typesPromise = null;

function fetchOnce() {
  if (_typesCache) return Promise.resolve(_typesCache);
  if (!_typesPromise) {
    _typesPromise = api
      .getAgentTypes()
      .then((data) => {
        _typesCache = Array.isArray(data) ? data : [];
        return _typesCache;
      })
      .catch(() => {
        _typesCache = [];
        return _typesCache;
      });
  }
  return _typesPromise;
}

// Lucide icon string → component. AgentType.icon stores the kebab
// name (e.g. "code", "git-pull-request"); components need the real
// React component. Add to this map when a new agent type uses an
// icon we haven't seen — unknown names render the User fallback so
// the row is still visible.
const ICON_MAP = {
  code: Code2,
  code2: Code2,
  eye: Eye,
  search: Search,
  map: MapIcon,
  user: User,
  bot: Bot,
  compass: Compass,
  wand: Wand2,
  wand2: Wand2,
  "git-pull-request": GitPullRequest,
  filetext: FileText,
  clipboard: Clipboard,
};

// CSS color var for each built-in. Custom agent types get a neutral
// default until we add a `color` column to AgentType (deferred).
const COLOR_MAP = {
  coding: "var(--pink)",
  review: "var(--blue)",
  investigation: "var(--lavender)",
  cartographer: "var(--lemon)",
};

export function useAgentTypes() {
  const [types, setTypes] = useState(_typesCache || []);
  useEffect(() => {
    if (_typesCache) return;
    let cancelled = false;
    fetchOnce().then((data) => {
      if (!cancelled) setTypes(data);
    });
    return () => {
      cancelled = true;
    };
  }, []);
  return types;
}

// Sync accessor for non-React contexts. Returns null until the first
// fetch completes; callers should treat null as "not loaded yet."
export function getAgentTypeSync(id) {
  if (!_typesCache || !id) return null;
  return _typesCache.find((t) => t.id === id) || null;
}

// Resolve display info for a role id. Returns a stable shape so
// consumers don't need to null-check every field — falls back to the
// raw id as label when the type isn't found yet.
export function roleMeta(id, types) {
  const list = types || _typesCache || [];
  const t = list.find((x) => x.id === id);
  const iconName = (t?.icon || "user").toLowerCase().replace(/_/g, "-");
  return {
    id,
    label: t?.name || id || "Unknown",
    tagline: t?.tagline || "",
    description: t?.description || "",
    icon: ICON_MAP[iconName] || User,
    color: COLOR_MAP[id] || "var(--text-muted)",
    // Capability flags — let components ask "should I show a Plan
    // tab / a Diff tab / a Report tab?" without grepping kind sets.
    outputKind: t?.output_kind || "diff",
    needsWorktree: !!t?.needs_worktree,
    requiresScopeRepoClone: !!t?.requires_scope_repo_clone,
    supportsPlanFirst: !!t?.supports_plan_first,
    commitsLocally: !!t?.commits_locally,
    producesPr: !!t?.produces_pr,
    isSelfReviewing: !!t?.is_self_reviewing,
    permissionMode: t?.permission_mode || null,
    defaultDisplayName: t?.default_display_name || null,
    raw: t || null,
  };
}
