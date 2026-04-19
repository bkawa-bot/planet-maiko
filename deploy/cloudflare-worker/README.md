# Pet Maiko counter — Cloudflare Worker

This directory holds the Cloudflare Worker that backs the **global**
Pet Maiko counter (the "N pets from the pack today" number on Home).
It's a tiny proxy in front of an Upstash Redis database: every Maiko
deployment hits this Worker's two endpoints, the Worker holds the
Upstash creds as secrets, and nothing sensitive ships in the open
source.

You only need to deploy this **once** — for the whole Maiko project,
not per user. End users don't run any of this. They point at whatever
URL you end up with and forget it exists.

## One-time setup (~10 min, $0)

### 1. Create the Upstash Redis database

Free, no credit card.

1. Sign up at [upstash.com](https://upstash.com).
2. **Create Database** → Redis → Global region → Free plan.
3. Once created, open the **REST API** tab in the database dashboard.
4. Copy these two values — you'll paste them into Cloudflare in a minute:
   - `UPSTASH_REDIS_REST_URL`
   - `UPSTASH_REDIS_REST_TOKEN`

### 2. Create the Cloudflare Worker

Free, no credit card. 100K requests/day included, which this
counter will never come close to.

1. Sign up at [cloudflare.com](https://cloudflare.com) if you don't
   already have an account.
2. Dashboard → **Workers & Pages** → **Create** → **Hello World**
   template.
3. Name it `maiko-pets` (this gives you `maiko-pets.<account>.workers.dev`
   as the public URL — remember that URL, you'll need it).
4. Paste the contents of `pet-counter.js` (this directory) into the
   editor, replacing the default template.
5. Click **Save and Deploy**.

### 3. Add the Upstash secrets to the Worker

1. Worker dashboard → **Settings** → **Variables and Secrets** → **Add**.
2. Add two secrets (set **Type: Secret**, not Plaintext):
   - `UPSTASH_URL` — the URL you copied from Upstash
   - `UPSTASH_TOKEN` — the token
3. Hit **Deploy** again after adding the secrets, so the new version
   picks them up.

### 4. Point Maiko at the Worker

In your `~/.maiko/config.yaml` (or `%APPDATA%\maiko\config.yaml` on
Windows), set:

```yaml
pets:
  aggregator_url: https://maiko-pets.<your-account>.workers.dev
```

No token — the Worker handles Upstash auth internally.

For public Maiko installs to use the same counter by default, update
`DEFAULT_CONFIG` in `src/planet_maiko/config.py` with the same URL
and push a release.

### 5. Verify

Open the Home widget, pet Maiko. Check the Upstash **Data Browser** —
`maiko:pets:total` should have gone up by one. The widget will show
the global counts within a minute.

## Updating the Worker code

If `pet-counter.js` changes, re-paste it into the Cloudflare Worker
editor and redeploy. No CLI needed. The secrets persist across deploys.

For heavier workflows, `npx wrangler deploy` from this directory
works too (needs `wrangler.toml`, not included because the one-shot
dashboard paste is simpler for a single-file Worker).

## Rotating Upstash creds

If the URL gets scraped and abused (unlikely — it only exposes INCR +
GET on two specific keys), rotate the Upstash token in the Upstash
dashboard and update the `UPSTASH_TOKEN` secret in the Worker. No
Maiko deploy needed — clients still hit the same Worker URL.
