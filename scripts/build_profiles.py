#!/usr/bin/env python3
"""Build data/candidate_profiles.json — Phase 4.

Two layers, strictly separated by provenance:

DERIVED (all candidates; computed from data/ballots_jhr.csv only — no
external claims, zero fabrication risk):
  contests[]        full contest history
  career_stats      contested/wins/losses/lost_deposits, best/worst share,
                    seats_held with year ranges
  party_switches[]  chronological affiliation changes
  news_refs[]       indices into data/news_index.json whose title contains
                    the candidate's name (conservative: >=2 significant
                    name tokens must appear; single-token names never match)

EXTERNAL (subset; merged from scripts/enrich_wikidata.py output if present):
  external{}        wikidata_qid, positions[], birth_year, wikipedia links,
                    provenance string. Absent unless verified.

Usage:
    python scripts/build_profiles.py [path/to/external.json]
"""
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "ballots_jhr.csv"
NEWS_PATH = ROOT / "data" / "news_index.json"
OUT_PATH = ROOT / "data" / "candidate_profiles.json"

WON = ("won", "won_uncontested")
# name tokens that are connectors/honorifics, not identifying
STOP = {"bin", "binti", "bte", "bt", "a/l", "a/p", "al", "ap", "haji", "hajjah",
        "hj", "dato", "dato'", "datuk", "dr", "tan", "sri", "abdul", "abd",
        "mohd", "mohamed", "mohammad", "muhammad", "mohamad", "ahmad", "md",
        "nik", "wan", "che", "ir", "ts", "syed", "sharifah", "mat", "man"}


def toint(v):
    try:
        return int(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0


def sig_tokens(name):
    """Distinct significant (identifying) tokens of a name, lowercase."""
    toks = re.findall(r"[a-z']+", (name or "").lower())
    return sorted({t for t in toks if t not in STOP and len(t) >= 3})


def main() -> int:
    external = {}
    if len(sys.argv) > 1 and Path(sys.argv[1]).exists():
        external = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        print(f"external enrichment loaded: {len(external)} candidates")

    rows = list(csv.DictReader(open(CSV_PATH, newline="", encoding="utf-8")))
    news = json.loads(NEWS_PATH.read_text(encoding="utf-8"))
    news_titles = [(i, (r.get("title_clean") or r.get("title_raw") or "").lower())
                   for i, r in enumerate(news)]

    by_uid = defaultdict(list)
    for r in rows:
        by_uid[r["candidate_uid"]].append(r)

    profiles = {}
    total_contests = total_wins = 0

    for uid, cand_rows in by_uid.items():
        cand_rows.sort(key=lambda r: r["election"])
        first = cand_rows[0]
        latest = cand_rows[-1]

        contests = []
        for r in cand_rows:
            age = toint(r["age"])
            contests.append({
                "election": r["election"],
                "year": int(r["date"][:4]),
                "seat": r["seat"],
                "party": r["party"],
                "coalition": r["coalition"],
                "votes": toint(r["votes"]),
                "perc": round(float(r["votes_perc"] or 0), 2),
                "rank": toint(r["rank"]),
                "result": r["result"],
                "age": age if age > 0 else None,
            })
        total_contests += len(contests)

        wins = [c for c in contests if c["result"] in WON]
        losses = [c for c in contests if c["result"] in ("lost", "lost_deposit")]
        deposits = [c for c in contests if c["result"] == "lost_deposit"]
        total_wins += len(wins)
        shares = [c["perc"] for c in contests if c["result"] != "pending" and c["perc"] > 0]

        # seats_held: consecutive wins in the same seat NAME → one span
        seats_held = []
        for c in wins:
            seat_name = re.sub(r"^\S+\s+", "", c["seat"])
            if seats_held and seats_held[-1]["seat"] == seat_name \
               and c["year"] > seats_held[-1]["to_year"]:
                seats_held[-1]["to_year"] = c["year"]
                seats_held[-1]["terms"] += 1
            else:
                seats_held.append({"seat": seat_name, "from_year": c["year"],
                                   "to_year": c["year"], "terms": 1})

        switches = []
        for a, b in zip(contests, contests[1:]):
            if a["party"] != b["party"]:
                switches.append({"from": a["party"], "to": b["party"],
                                 "between": [a["election"], b["election"]]})

        # conservative news matching: >=2 DISTINCT tokens as whole words
        toks = sig_tokens(first["name"])
        news_refs = []
        if len(toks) >= 2:
            pats = [re.compile(r"\b" + re.escape(t) + r"\b") for t in toks]
            for i, title in news_titles:
                hits = sum(1 for p in pats if p.search(title))
                if hits >= 2:
                    news_refs.append(i)

        profiles[uid] = {
            "uid": uid,
            "name": first["name"],
            "sex": first["sex"] or None,
            "ethnicity": first["ethnicity"] or None,
            "contests": contests,
            "career_stats": {
                "contested": len(contests),
                "wins": len(wins),
                "losses": len(losses),
                "lost_deposits": len(deposits),
                "best_share": max(shares) if shares else None,
                "worst_share": min(shares) if shares else None,
                "first_year": contests[0]["year"],
                "last_year": latest and contests[-1]["year"],
                "seats_held": seats_held,
            },
            "party_switches": switches,
            "news_refs": news_refs,
            "external": external.get(uid) or None,
        }

    OUT_PATH.write_text(json.dumps(profiles, ensure_ascii=False, separators=(",", ":")) + "\n",
                        encoding="utf-8")

    enriched = sum(1 for p in profiles.values() if p["external"])
    with_news = sum(1 for p in profiles.values() if p["news_refs"])
    switched = sum(1 for p in profiles.values() if p["party_switches"])
    print(f"profiles: {len(profiles)} | contests Σ {total_contests} | wins Σ {total_wins}")
    print(f"enriched(external): {enriched} | with news_refs: {with_news} | party switches: {switched}")
    print(f"wrote {OUT_PATH} ({OUT_PATH.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
