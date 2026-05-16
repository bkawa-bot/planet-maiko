/**
 * Date formatting helpers. Centralizes the inconsistent toLocale* calls
 * scattered across pages so changing the format (or adding i18n later)
 * happens in one spot.
 *
 * Each helper accepts an ISO string, Date, or null/undefined and returns
 * an empty string for missing values rather than throwing.
 */

function _toDate(value) {
  if (!value) return null;
  if (value instanceof Date) return value;
  return new Date(value);
}

/**
 * Time of day, no date. Used for "last seen at 14:23". Strips seconds
 * — the default toLocaleTimeString() leaks "14:23:47" everywhere,
 * which makes the UI feel like ops tooling.
 */
export function formatTime(value) {
  const d = _toDate(value);
  return d ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
}

/**
 * Short clock face: 14:23. Used for calendar event start times where
 * we want the compact 24h-ish form.
 */
export function formatClock(value) {
  const d = _toDate(value);
  return d ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
}

/**
 * Date only: "4/9/2026". Used for "updated 5d ago" style chips and
 * inbox/idea timestamps where the time-of-day is noise.
 */
export function formatDate(value) {
  const d = _toDate(value);
  return d ? d.toLocaleDateString() : "";
}

/**
 * Friendly long date: "Fri, May 16". For ambient surfaces (the Today
 * widget header) where a slashed numeric date reads as ops tooling.
 * No year — the widget is always "today" so the year is noise.
 */
export function formatLongDate(value) {
  const d = _toDate(value);
  return d
    ? d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" })
    : "";
}

/**
 * Full date + time: "4/9/2026, 2:23 PM". Used in pupdate card meta where
 * both pieces matter. No seconds — see formatTime for rationale.
 */
export function formatDateTime(value) {
  const d = _toDate(value);
  if (!d) return "";
  const date = d.toLocaleDateString();
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return `${date}, ${time}`;
}

/**
 * Relative time: "just now", "5m ago", "3h ago", "2d ago".
 * Past-only — doesn't render future times sensibly.
 */
export function relativeTime(value) {
  const d = _toDate(value);
  if (!d) return "";
  const diff = Date.now() - d.getTime();
  const mins = Math.floor(diff / 60000);
  return relativeFromMinutes(mins);
}

/**
 * Same grammar as relativeTime but takes an already-computed minute
 * count. Used for backend fields that already arrive as minutes-since
 * (e.g. agent activity idle_minutes, external session ageMin) — avoids
 * a double conversion.
 */
export function relativeFromMinutes(mins) {
  if (!Number.isFinite(mins) || mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}
