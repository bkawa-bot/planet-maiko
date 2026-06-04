import { useEffect, useState } from "react";
import { api } from "../../../api/client";

// Built-in agent-job kinds — executor dispatch keys, not skills (mirrors
// AutomationEditor's list). Custom skills are appended from /skills so a
// skill added there shows up in the picker without a code change.
const BUILTIN_JOB_KINDS = [
  { value: "cartograph", label: "cartograph — walk the repo" },
  { value: "investigation", label: "investigation — spawn an investigator" },
  { value: "repo_analysis", label: "repo_analysis — read-only" },
];

// Module-level cache so every trigger/action node on the canvas shares ONE
// fetch of the option lists, not one request per node.
let _cache = null;
let _promise = null;

function _load() {
  if (_cache) return Promise.resolve(_cache);
  if (!_promise) {
    _promise = Promise.all([
      api.getPupdateTypes().catch(() => []),
      api.getSkills().catch(() => []),
      api.getPupdateSources().catch(() => []),
    ]).then(([pupdateTypes, skills, sources]) => {
      _cache = {
        pupdateTypes: pupdateTypes || [],
        sources: sources || [],
        agentJobKinds: [
          ...BUILTIN_JOB_KINDS,
          ...(skills || []).map((s) => ({
            value: s.id || s.name,
            label: `${s.name}${s.is_default ? " (skill)" : " (custom skill)"}`,
          })),
        ],
      };
      return _cache;
    });
  }
  return _promise;
}

const EMPTY = { pupdateTypes: [], sources: [], agentJobKinds: BUILTIN_JOB_KINDS };

// Shared option lists for the trigger/action node editors (pupdate types to
// fire on, sources to narrow by, skill/job kinds to run). Fetched once and
// cached at module scope; returns the cached value immediately on later
// mounts so the dropdowns populate without a flash.
export function useFlowOptions() {
  const [opts, setOpts] = useState(_cache || EMPTY);
  useEffect(() => {
    let alive = true;
    _load().then((c) => { if (alive) setOpts(c); });
    return () => { alive = false; };
  }, []);
  return opts;
}
