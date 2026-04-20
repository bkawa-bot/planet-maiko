/**
 * Repo display helpers.
 *
 * Repos are stored as "org/name" strings everywhere (AgentProfile.scope_repo,
 * Learning.scope_repo, task metadata). Rendering the org prefix on every
 * label is noisy when most of your work lives under a single org. These
 * helpers strip the configured `github.default_org` prefix for display
 * while leaving cross-org repos rendered in full.
 */

import { useEffect, useState } from "react";
import { api } from "../api/client";

/**
 * Strip defaultOrg from "org/name" for display.
 *
 *   formatRepo("bkawa-bot/planet-maiko", "bkawa-bot") -> "planet-maiko"
 *   formatRepo("other-org/thing", "bkawa-bot")        -> "other-org/thing"
 *   formatRepo("", ...)                               -> ""
 */
export function formatRepo(scopeRepo, defaultOrg) {
  if (!scopeRepo) return "";
  if (!defaultOrg) return scopeRepo;
  const prefix = `${defaultOrg}/`;
  return scopeRepo.startsWith(prefix) ? scopeRepo.slice(prefix.length) : scopeRepo;
}

let _cachedOrg = null;
let _inflight = null;

function loadDefaultOrg() {
  if (_cachedOrg !== null) return Promise.resolve(_cachedOrg);
  if (!_inflight) {
    _inflight = api
      .getConfig()
      .then((cfg) => {
        _cachedOrg = cfg?.github?.default_org || "";
        return _cachedOrg;
      })
      .catch(() => {
        _cachedOrg = "";
        return "";
      })
      .finally(() => {
        _inflight = null;
      });
  }
  return _inflight;
}

/**
 * Invalidate the cached default_org. Call after the user saves settings so
 * components re-render with the new prefix without a page reload.
 */
export function invalidateDefaultOrg() {
  _cachedOrg = null;
  _inflight = null;
}

/**
 * Hook for components that render repo labels. Returns the current
 * default_org string (empty until loaded, so formatRepo falls through
 * to the raw scope_repo on first paint — acceptable since it's a
 * cosmetic trim, not a correctness concern).
 */
export function useDefaultOrg() {
  const [org, setOrg] = useState(_cachedOrg || "");
  useEffect(() => {
    let cancelled = false;
    loadDefaultOrg().then((v) => {
      if (!cancelled) setOrg(v);
    });
    return () => {
      cancelled = true;
    };
  }, []);
  return org;
}
