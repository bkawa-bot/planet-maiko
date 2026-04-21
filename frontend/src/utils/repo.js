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

/**
 * Derive a "default org" from a configured repos list.
 *
 * If every configured repo shares the same org prefix, that's the
 * right thing to strip from display — one common case, no user
 * action required. If repos span multiple orgs, return empty so
 * formatRepo leaves the disambiguating prefix in.
 */
function inferDefaultOrg(repos) {
  if (!Array.isArray(repos) || repos.length === 0) return "";
  const orgs = new Set();
  for (const r of repos) {
    const idx = (r || "").indexOf("/");
    if (idx > 0) orgs.add(r.slice(0, idx));
  }
  return orgs.size === 1 ? [...orgs][0] : "";
}


let _cachedOrg = null;
let _inflight = null;

function loadDefaultOrg() {
  if (_cachedOrg !== null) return Promise.resolve(_cachedOrg);
  if (!_inflight) {
    _inflight = api
      .getConfig()
      .then((cfg) => {
        // Honour an explicit default_org if the user set one; otherwise
        // auto-derive from github.repos. Most users only ever touch one
        // org, so inferring means formatRepo actually strips without
        // needing a Settings visit.
        const gh = cfg?.github || {};
        _cachedOrg =
          (gh.default_org || "").trim() || inferDefaultOrg(gh.repos);
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


let _cachedRepos = null;
let _reposInflight = null;

function loadConfiguredRepos() {
  if (_cachedRepos !== null) return Promise.resolve(_cachedRepos);
  if (!_reposInflight) {
    _reposInflight = api
      .getConfig()
      .then((cfg) => {
        _cachedRepos = (cfg?.github?.repos) || [];
        return _cachedRepos;
      })
      .catch(() => {
        _cachedRepos = [];
        return [];
      })
      .finally(() => {
        _reposInflight = null;
      });
  }
  return _reposInflight;
}

/**
 * Invalidate the cached repo list. Call after the user saves Settings →
 * Integrations so existing pages pick up new/removed repos.
 */
export function invalidateConfiguredRepos() {
  _cachedRepos = null;
  _reposInflight = null;
}

/**
 * Hook for any input that accepts a repo. Returns the configured
 * `github.repos` list so components can render a <datalist> for
 * autocomplete without duplicating the fetch. Starts empty; components
 * should treat an empty list as "no suggestions available, still allow
 * free-form input".
 */
export function useConfiguredRepos() {
  const [repos, setRepos] = useState(_cachedRepos || []);
  useEffect(() => {
    let cancelled = false;
    loadConfiguredRepos().then((v) => {
      if (!cancelled) setRepos(v);
    });
    return () => { cancelled = true; };
  }, []);
  return repos;
}
