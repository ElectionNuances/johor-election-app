# Deploying the Johor Election App

## 1. GitHub Pages (the app)

1. Create the repository and push:
   ```bash
   git init && git add -A && git commit -m "Johor Election App"
   gh repo create vgnshlvnz/johor-election-app --public --source . --push
   ```
2. On github.com → the repo → **Settings → Pages** → under *Build and deployment*,
   set **Source: GitHub Actions**.
3. Push to `main` (or re-run the **Deploy to GitHub Pages** workflow). Wait for the
   green check in the **Actions** tab.
4. Visit **https://vgnshlvnz.github.io/johor-election-app/** — done.

There is no build step: the workflow uploads the repo root as-is. All asset paths in
`index.html` are relative, so nothing breaks under the `/johor-election-app/` subpath.

## 2. Cloudflare Worker (live coalition-label voting — optional)

Without this, the app quietly falls back to `data/coalition_labels.json`.

```bash
cd worker
npm i -g wrangler          # or: npm x wrangler …
wrangler login             # opens browser; needs your Cloudflare account
wrangler kv namespace create TAGS
#   → copy the returned "id" into wrangler.toml (kv_namespaces → id)
wrangler deploy
#   → note the URL, e.g. https://johor-election-tags.<account>.workers.dev
```

Then point the app at it:

1. In `index.html`, set
   `const API_BASE = "https://johor-election-tags.<account>.workers.dev";`
2. If your Pages origin differs from `https://vgnshlvnz.github.io`, add it to
   `ALLOWED_ORIGINS` in `worker/worker.js`.
3. Commit and push — the Pages workflow redeploys automatically.

Smoke-test the worker:

```bash
curl https://johor-election-tags.<account>.workers.dev/api/tags
```

## 3. Monthly data refresh

`.github/workflows/refresh-data.yml` runs monthly (and on manual dispatch): it
re-downloads the CSV from lake.electiondata.my and commits **only if the file hash
changed**, updating `data/meta.json` (the About tab shows "Last synced"). The news
index is deliberately not cron-refreshed — re-run the hunt manually and merge with
`scripts/merge_news.py`.
