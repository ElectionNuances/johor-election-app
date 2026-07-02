#!/usr/bin/env python3
"""Re-download the Johor ballots CSV from lake.electiondata.my.

Commits happen only when the file hash changes: this script downloads the
CSV, compares sha256 against data/meta.json, and rewrites the CSV + meta.json
only on change. Emits `changed=true|false` to $GITHUB_OUTPUT (or stdout when
run locally) so the workflow can decide whether to commit.
"""
import hashlib
import json
import os
import sys
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

CSV_URL = "https://lake.electiondata.my/results_headline/headline_ballots_state_jhr.csv"
ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "ballots_jhr.csv"
META_PATH = ROOT / "data" / "meta.json"


def emit(key: str, value: str) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"{key}={value}\n")
    print(f"{key}={value}")


def main() -> int:
    req = urllib.request.Request(CSV_URL, headers={"User-Agent": "johor-election-app data refresh"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = resp.read()
    if not body or b"date,election" not in body[:200]:
        print("Downloaded content does not look like the ballots CSV; aborting.", file=sys.stderr)
        return 1

    new_hash = hashlib.sha256(body).hexdigest()

    meta = {}
    if META_PATH.exists():
        meta = json.loads(META_PATH.read_text(encoding="utf-8"))

    if meta.get("csv_sha256") == new_hash:
        emit("changed", "false")
        print(f"No change (sha256 {new_hash[:12]}…).")
        return 0

    CSV_PATH.write_bytes(body)
    meta["csv_sha256"] = new_hash
    meta["last_synced"] = date.today().isoformat()
    META_PATH.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    emit("changed", "true")
    print(f"Updated: {len(body):,} bytes, sha256 {new_hash[:12]}… at {datetime.now(timezone.utc).isoformat()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
