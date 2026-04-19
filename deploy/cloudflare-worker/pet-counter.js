// Planet Maiko pet counter — Cloudflare Worker proxy to Upstash Redis.
//
// Why a proxy: Upstash REST tokens aren't scoped to specific commands,
// so shipping the token in AGPL source would let anyone FLUSHDB or
// spam arbitrary keys. This Worker exposes only the two operations
// Pet Maiko actually needs (increment + read the pair of counters) and
// nothing else. The real Upstash creds live as Worker secrets —
// they never reach any client.
//
// Endpoints:
//   POST /incr    — bump today + lifetime counters; refresh today TTL
//   GET  /counts  — return { global_today, global_lifetime }
//
// Env (set via `wrangler secret put` or the Cloudflare dashboard
// under Settings → Variables and Secrets):
//   UPSTASH_URL    — e.g. https://xxx.upstash.io
//   UPSTASH_TOKEN  — REST token
//
// Deploy: see README.md in this directory.

const TOTAL_KEY = "maiko:pets:total";
const DAILY_TTL_SECONDS = 60 * 60 * 48;  // keep daily key around two days, then let it drop

function todayKey() {
  // YYYY-MM-DD in UTC. Close enough for a vibes counter, and keeps the
  // key deterministic regardless of caller timezone so concurrent pets
  // from different zones all INCR the same bucket.
  return `maiko:pets:day:${new Date().toISOString().slice(0, 10)}`;
}

async function upstash(env, path) {
  return fetch(`${env.UPSTASH_URL}${path}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${env.UPSTASH_TOKEN}` },
  });
}

async function handleIncr(env) {
  const today = todayKey();
  // Fire all three in parallel; we don't need the returned values.
  // EXPIRE is idempotent — calling it every pet just refreshes the TTL,
  // which is exactly what we want so the key doesn't drop mid-day.
  await Promise.all([
    upstash(env, `/incr/${today}`),
    upstash(env, `/incr/${TOTAL_KEY}`),
    upstash(env, `/expire/${today}/${DAILY_TTL_SECONDS}`),
  ]);
  return json({ ok: true });
}

async function handleCounts(env) {
  const today = todayKey();
  const [todayResp, totalResp] = await Promise.all([
    upstash(env, `/get/${today}`),
    upstash(env, `/get/${TOTAL_KEY}`),
  ]);
  const todayData = await todayResp.json();
  const totalData = await totalResp.json();
  return json({
    global_today: parseInt(todayData.result ?? "0", 10) || 0,
    global_lifetime: parseInt(totalData.result ?? "0", 10) || 0,
  });
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
      // CORS open — the Worker only ever exposes two scalar counters,
      // so letting any origin read is fine. POST /incr from the
      // browser is also fine; the Worker rate-limits the blast radius
      // (you can INCR but you can't do anything else).
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
    },
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") return json({}, 204);
    if (url.pathname === "/incr" && request.method === "POST") return handleIncr(env);
    if (url.pathname === "/counts" && request.method === "GET") return handleCounts(env);
    return json({ error: "not found", pathname: url.pathname }, 404);
  },
};
