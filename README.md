# The Johor Election App

**Live: <https://electionnuances.github.io/johor-election-app/>**

A single-page interactive map of Johor (Malaysia) state assembly — DUN — election
results from **1959 to the present**, with an indexed archive of verified news
coverage. One hex ≈ one seat, laid out north→south to approximate Johor's geography
(seat numbering runs geographically, which makes this work).

- **Winner / Margin views**, election stepper SE-01…SE-16 (keyboard ←/→, play button)
- **Seat detail**: full ballot + a winner timeline across every election (seats are
  matched by *name*, never number — redelineations renumbered everything)
- **Coverage tab**: real press coverage per election and per seat. Every title was
  read from the page's actual `<title>` tag or a Wayback Machine snapshot — nothing
  fabricated; gaps are shown honestly
- **Crowd-labelled coalition names**: display names come from votable tags (see
  *Coalition labels* below), never hard-coded guesses
- **Candidate profiles**: click any candidate on a ballot — contest history, career
  stats and party switches computed from MECo; public offices from **structured
  Wikidata claims only** (165 of 341 ever-winning candidates verified by exact-name +
  politics-signal matching; ambiguous cases skipped, see `data/enrichment_report.json`)
- **Community forecast** (pending election only): visitors predict the winning
  *coalition per seat*. Clearly disclaimed as unofficial and self-selected; the app
  deliberately does **not** collect ratings or votes on named individuals

## Running

It's a static page — no build step. Serve the repo root and open it:

```bash
python3 -m http.server 8000     # or any static server
# → http://localhost:8000
```

Opening `index.html` directly as a file also works in browsers that allow
`fetch` of relative files; a local server is more reliable.

### Local label-voting server (optional)

`node server.js` serves the app at <http://localhost:5173> with a local
tag-and-vote API (persisted to `tags.json`, git-ignored). In production the same
API is provided by the Cloudflare Worker in [`worker/`](worker/) — see
[DEPLOY.md](DEPLOY.md). Without either, names fall back to
`data/coalition_labels.json`, then to bare codes.

## Data & Attribution

Election results are from **[ElectionData.MY](https://electiondata.my)** (CC0),
specifically the [headline ballots for Johor](https://electiondata.my/data-catalogue/headline-ballots-state-jhr/),
vendored at `data/ballots_jhr.csv` and refreshed by a monthly workflow.

Please cite the dataset:

```bibtex
@article{thevananthan2025malaysian,
  author  = {Thevananthan, Thevesh},
  title   = {The Malaysian Election Corpus (MECo): Federal and State-Level Election Results from 1955 to 2025},
  journal = {Scientific Data},
  year    = {2025}
}
```

### Licence split

| Component | Licence |
|---|---|
| Code (this repository) | [BSD-3-Clause-Attribution](LICENSE) — redistributions must retain the MECo/Thevesh acknowledgment |
| Election data (`data/ballots_jhr.csv`) | CC0 (public domain), as dedicated by its author |
| News index (`data/news_index.json`) | Links only; titles quoted solely for identification. No ownership claimed over articles or titles |

## News index methodology

`data/news_index.json` is a frozen, dated snapshot built by searching real news
coverage, fetching each page, and recording its **exact `<title>` text**. Dead or
paywalled pages are recovered via the Wayback Machine CDX API and served from
`archive_url` (marked 🗄 in the app). A link enters the index **only** if its title
was actually read from a real fetch or a real snapshot. The index is *not*
refreshed by cron — automated link-hunting risks fabrication — re-run the hunt
manually and merge with `scripts/merge_news.py`.

## Repository layout

```
index.html                    the app (self-contained; CDN React, SRI-pinned)
data/ballots_jhr.csv          vendored MECo CSV (CC0)
data/news_index.json          verified news index (frozen snapshot)
data/candidate_profiles.json  per-candidate profiles (derived + Wikidata external layer)
data/enrichment_report.json   Wikidata matching report (accepted/ambiguous/no-match)
data/coalition_labels.json    static fallback coalition names
data/meta.json                data-sync metadata (shown in the About tab)
server.js                     optional local dev server (labels + forecast APIs)
worker/                       Cloudflare Worker port of the labels + forecast APIs
scripts/refresh_data.py       CSV refresh (used by the monthly workflow)
scripts/merge_news.py         news-batch merge/dedupe/seat-tagging
scripts/build_profiles.py     candidate profiles: derived layer + news_refs
scripts/enrich_wikidata.py    bulk-SPARQL external enrichment (verified matches only)
scripts/sanity_check.py       acceptance gate (data + profiles + news invariants)
.github/workflows/            Pages deploy + monthly data refresh
```

## Acknowledgment

This product uses Malaysian election data from ElectionData.MY, created by
Thevesh Thevananthan (Malaysia), published as The Malaysian Election Corpus
(MECo), Scientific Data, 2025.
