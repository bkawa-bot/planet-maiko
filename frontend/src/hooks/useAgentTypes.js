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

// Live subscribers. Every mounted useAgentTypes() registers its state
// setter here so refreshAgentTypes() can push a fresh list to all of
// them after a create / edit / delete, instead of waiting for a full
// page reload to repopulate the module cache.
const _subscribers = new Set();

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

// Resolve an AgentType.icon string to its component (User fallback).
// Shared by roleMeta and the agent-type editor's icon picker so both
// agree on what a given icon name renders as.
export function iconForName(name) {
  const key = (name || "user").toLowerCase().replace(/_/g, "-");
  return ICON_MAP[key] || User;
}

// Curated, deduped icon choices for the agent-type editor picker.
// A subset of ICON_MAP (which carries a few aliases like code/code2).
export const AGENT_ICON_CHOICES = [
  "code", "git-pull-request", "search", "map", "eye",
  "compass", "wand", "bot", "clipboard", "filetext", "user",
];

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
    let cancelled = false;
    const update = (data) => { if (!cancelled) setTypes(data); };
    _subscribers.add(update);
    // Resolves instantly from cache when warm; fetches once when cold.
    fetchOnce().then(update);
    return () => {
      cancelled = true;
      _subscribers.delete(update);
    };
  }, []);
  return types;
}

// Drop the cache, refetch, and push the new list to every mounted
// useAgentTypes(). Call after any AgentType mutation so the Roles tab,
// the New Agent role picker, and all roleMeta() consumers refresh
// together rather than drifting until the next reload.
export function refreshAgentTypes() {
  _typesCache = null;
  _typesPromise = null;
  return fetchOnce().then((data) => {
    _subscribers.forEach((fn) => fn(data));
    return data;
  });
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
  return {
    id,
    label: t?.name || id || "Unknown",
    description: t?.description || "",
    icon: iconForName(t?.icon),
    color: COLOR_MAP[id] || "var(--text-muted)",
    // Capability flags — let components ask "should I show a Plan
    // tab / a Diff tab / a Report tab?" without grepping kind sets.
    inputKind: t?.input_kind || "task",
    outputKind: t?.output_kind || "diff",
    spawnMode: t?.spawn_mode || "worktree",
    permissionMode: t?.permission_mode || null,
    raw: t || null,
  };
}
