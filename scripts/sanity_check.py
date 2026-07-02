#!/usr/bin/env python3
"""Acceptance-criteria sanity gate. Exits non-zero on any failure.

Checks (against data/ballots_jhr.csv):
  1. SE-15 seat totals: 56 seats; coalition split BN 40 / PH 12 / PN 3 /
     ALONE 1, where the ALONE winner's party is MUDA (N.41 Puteri Wangsa).
  2. Every decided election: winners == seat count (no orphan seats).
  3. Spot-check three seat ballots row-for-row against the CSV.
  4. news_index.json (if non-empty): schema keys + no title-less live rows.
"""
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
failures = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  ({detail})" if detail else ""))
    if not cond:
        failures.append(name)


rows = list(csv.DictReader(open(ROOT / "data" / "ballots_jhr.csv", newline="", encoding="utf-8")))
WON = ("won", "won_uncontested")

# --- 1. SE-15 totals ---
se15 = [r for r in rows if r["election"] == "SE-15"]
coal = defaultdict(int)
muda_seat = None
for r in se15:
    if r["result"] in WON:
        coal[r["coalition"]] += 1
        if r["party"] == "MUDA":
            muda_seat = r["seat"]
check("SE-15 seats = 56", len({r["seat"] for r in se15}) == 56)
check("SE-15 BN=40 PH=12 PN=3 ALONE=1",
      coal.get("BN") == 40 and coal.get("PH") == 12 and coal.get("PN") == 3 and coal.get("ALONE") == 1,
      str(dict(coal)))
check("SE-15 MUDA won one seat (as ALONE)", muda_seat == "N.41 Puteri Wangsa", str(muda_seat))

# --- 2. per-election winner counts ---
bad = []
for el in sorted({r["election"] for r in rows}):
    seats = {r["seat"] for r in rows if r["election"] == el}
    winners = sum(1 for r in rows if r["election"] == el and r["result"] in WON)
    decided = winners > 0
    if decided and winners != len(seats):
        bad.append(f"{el}: {winners}/{len(seats)}")
check("every decided election: winners == seats", not bad, "; ".join(bad))

# --- 3. ballot spot-checks (exact rows from the CSV) ---
SPOTS = [("SE-15", "N.01 Buloh Kasap"), ("SE-14", "N.44 Larkin"), ("SE-01", "N.27 Endau")]
for el, seat in SPOTS:
    ballot = sorted((r for r in rows if r["election"] == el and r["seat"] == seat),
                    key=lambda r: int(r["rank"] or 99))
    ok = bool(ballot)
    if ok:
        votes = [int(r["votes"].replace(",", "")) for r in ballot]
        ranks = [int(r["rank"]) for r in ballot if r["rank"]]
        ok = votes == sorted(votes, reverse=True) and ranks == sorted(ranks) \
            and ballot[0]["result"] in WON
    check(f"ballot {el} {seat}: ranks/votes/winner consistent", ok,
          f"{len(ballot)} candidates" if ballot else "seat missing")

# --- 4. candidate profile invariants ---
prof_path = ROOT / "data" / "candidate_profiles.json"
if prof_path.exists():
    profiles = json.loads(prof_path.read_text(encoding="utf-8"))
    uids = {r["candidate_uid"] for r in rows}
    check("profiles cover every candidate_uid", set(profiles.keys()) == uids,
          f"{len(profiles)} vs {len(uids)}")
    tot_contests = sum(len(p["contests"]) for p in profiles.values())
    check("Σ profile contests == CSV rows", tot_contests == len(rows), str(tot_contests))
    tot_wins = sum(p["career_stats"]["wins"] for p in profiles.values())
    csv_wins = sum(1 for r in rows if r["result"] in WON)
    check("Σ profile wins == CSV wins", tot_wins == csv_wins, f"{tot_wins} vs {csv_wins}")
    # external blocks must carry provenance
    bad_ext = [u for u, p in profiles.items()
               if p["external"] and not (p["external"].get("wikidata_qid") and p["external"].get("verified"))]
    check("every external block has QID + provenance", not bad_ext, f"{len(bad_ext)} bad")
else:
    print("  SKIP  candidate_profiles.json absent")

# --- 5. news index integrity ---
news = json.loads((ROOT / "data" / "news_index.json").read_text(encoding="utf-8"))
if news:
    bad_news = [r for r in news
                if not r.get("url") or not r.get("outlet")
                or (r.get("status") != "dead" and not (r.get("title_raw") or "").strip())]
    check(f"news index: {len(news)} rows, all with fetched titles or dead-flag", not bad_news,
          f"{len(bad_news)} bad")
else:
    print("  SKIP  news index empty (pre-merge)")

print()
if failures:
    print(f"SANITY GATE FAILED: {len(failures)} failure(s): " + "; ".join(failures))
    sys.exit(1)
print("SANITY GATE: ALL PASS")
