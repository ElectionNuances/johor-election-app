#!/usr/bin/env python3
"""Merge news-hunter batch files into data/news_index.json.

Usage:
    python scripts/merge_news.py <batch_dir> [--freeze]

- Validates every record against the schema (drops invalid ones, loudly).
- Anti-fabrication gate: records must carry a real fetched title
  (title_raw non-empty) OR be explicitly status="dead" with a real URL.
- Canonicalises URLs (strips utm_*/ref/fbclid params, fragments, AMP
  suffixes) and dedupes on the result, preferring live > archived > dead.
- Seat-tags rows by matching headline text and URL slugs against the seat
  names in data/ballots_jhr.csv (normalised, longest-name-first so
  "Bukit Permai" wins over "Permai"-like partials).
- Emits a per-outlet × per-election summary table and dead-link %.
- --freeze also stamps data/meta.json (news_count, news_frozen).
"""
import csv
import json
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "ballots_jhr.csv"
OUT_PATH = ROOT / "data" / "news_index.json"
META_PATH = ROOT / "data" / "meta.json"

VALID_ELECTIONS = {f"SE-{i:02d}" for i in range(1, 17)} | {"byelection"}
VALID_STATUS = {"live", "archived", "dead"}
REQUIRED = ["election", "outlet", "language", "url", "status"]

STRIP_PARAMS = re.compile(r"^(utm_|ref$|fbclid$|gclid$|amp$)")


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def canonical_url(url: str) -> str:
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    netloc = parts.netloc.lower().replace("amp.", "www.", 1) if parts.netloc.startswith("amp.") else parts.netloc.lower()
    path = re.sub(r"/amp/?$", "/", parts.path)
    q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not STRIP_PARAMS.match(k.lower())]
    return urlunsplit((parts.scheme.lower() or "https", netloc, path.rstrip("/") or "/", urlencode(q), ""))


def load_seat_names():
    """All distinct seat names from the ballots CSV, longest first."""
    names = set()
    with open(CSV_PATH, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            seat = (row.get("seat") or "").strip()
            m = re.match(r"^\S+\s+(.*)$", seat)
            if m:
                names.add(m.group(1).strip())
    return sorted(names, key=len, reverse=True)


def seat_tag(record, seat_names):
    """Return a seat name if the headline or URL slug plainly contains one."""
    if record.get("seat"):
        # keep agent-provided tag if it matches a real seat name
        provided = norm(re.sub(r"^N\.?\d+\s*", "", str(record["seat"])))
        for name in seat_names:
            if norm(name) == provided:
                return name
    hay = norm((record.get("title_clean") or record.get("title_raw") or "") + " " +
               urlsplit(record.get("url") or "").path.replace("-", " ").replace("/", " "))
    for name in seat_names:
        n = norm(name)
        if len(n) >= 5 and re.search(r"\b" + re.escape(n) + r"\b", hay):
            return name
    return None


def validate(r, errors):
    for k in REQUIRED:
        if not r.get(k):
            errors.append(f"missing {k}: {json.dumps(r, ensure_ascii=False)[:110]}")
            return False
    if r["election"] not in VALID_ELECTIONS:
        errors.append(f"bad election {r['election']!r}: {r.get('url','')[:80]}")
        return False
    if r["status"] not in VALID_STATUS:
        errors.append(f"bad status {r['status']!r}: {r.get('url','')[:80]}")
        return False
    if not re.match(r"^https?://", r["url"]):
        errors.append(f"bad url: {r.get('url','')[:90]}")
        return False
    # anti-fabrication: a title must exist unless explicitly dead
    if r["status"] != "dead" and not (r.get("title_raw") or "").strip():
        errors.append(f"no fetched title on non-dead record: {r['url'][:90]}")
        return False
    if r["status"] == "archived" and not (r.get("archive_url") or "").startswith("http"):
        errors.append(f"archived without archive_url: {r['url'][:90]}")
        return False
    if r.get("date_published") and not re.match(r"^\d{4}-\d{2}-\d{2}$", r["date_published"]):
        r["date_published"] = None      # tolerate: null out malformed dates
    return True


STATUS_RANK = {"live": 0, "archived": 1, "dead": 2}


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    batch_dir = Path(sys.argv[1])
    freeze = "--freeze" in sys.argv
    files = sorted(batch_dir.glob("*.json"))
    if not files:
        print(f"No batch files in {batch_dir}", file=sys.stderr)
        return 1

    seat_names = load_seat_names()
    errors, merged = [], {}
    per_file = {}

    for f in files:
        try:
            batch = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            errors.append(f"{f.name}: unparseable JSON ({e})")
            continue
        if not isinstance(batch, list):
            errors.append(f"{f.name}: not a JSON array")
            continue
        kept = 0
        for r in batch:
            if not isinstance(r, dict) or not validate(r, errors):
                continue
            cu = canonical_url(r["url"])
            r["url"] = cu
            r["seat"] = seat_tag(r, seat_names)
            r["title_clean"] = (r.get("title_clean") or r.get("title_raw") or "").strip() or None
            prev = merged.get(cu)
            if prev is None or STATUS_RANK[r["status"]] < STATUS_RANK[prev["status"]]:
                merged[cu] = r
            kept += 1
        per_file[f.name] = (kept, len(batch))

    records = sorted(merged.values(),
                     key=lambda r: (r["election"], r.get("date_published") or "", r["outlet"]))
    OUT_PATH.write_text(json.dumps(records, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    # ---- summary ----
    print(f"\nBatches: " + ", ".join(f"{n} {k}/{t}" for n, (k, t) in per_file.items()))
    if errors:
        print(f"\nDROPPED {len(errors)} invalid records:")
        for e in errors[:20]:
            print("  -", e)
        if len(errors) > 20:
            print(f"  … and {len(errors)-20} more")

    table = defaultdict(lambda: defaultdict(int))
    langs, dead = set(), 0
    for r in records:
        table[r["outlet"]][r["election"]] += 1
        langs.add(r["language"])
        if r["status"] == "dead":
            dead += 1
    elections = sorted({r["election"] for r in records})
    w = max((len(o) for o in table), default=8)
    print(f"\n{'outlet'.ljust(w)}  " + "  ".join(e.rjust(10) for e in elections) + "  total")
    for outlet in sorted(table, key=lambda o: -sum(table[o].values())):
        row = table[outlet]
        print(outlet.ljust(w) + "  " + "  ".join(str(row.get(e, "")).rjust(10) for e in elections)
              + f"  {sum(row.values()):5d}")
    se15 = sum(1 for r in records if r["election"] == "SE-15")
    tagged = sum(1 for r in records if r["seat"])
    print(f"\nTotal {len(records)} unique · SE-15 {se15} · outlets {len(table)} · languages {sorted(langs)}"
          f" · seat-tagged {tagged} · dead {dead} ({dead*100//max(1,len(records))}%)")

    if freeze:
        meta = json.loads(META_PATH.read_text(encoding="utf-8"))
        meta["news_count"] = len(records)
        meta["news_frozen"] = date.today().isoformat()
        META_PATH.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        print(f"meta.json stamped: news_count={len(records)}, news_frozen={meta['news_frozen']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
