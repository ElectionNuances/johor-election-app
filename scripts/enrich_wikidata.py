#!/usr/bin/env python3
"""Wikidata enrichment for Johor DUN candidates — Phase 4 external layer.

Public-role information only, verified matching, zero guessing.

Strategy (bulk, ~10-15 HTTP calls total — per-name API search proved
unusable under Wikidata rate limits):
  1. One SPARQL VALUES query per ~250 name variants matches candidate
     names EXACTLY against rdfs:label / skos:altLabel (en + ms), filtered
     to humans with a politics signal (P39 position / P102 party /
     P106 politician).
  2. Only ever-winning candidates are matched, via exact-name variants
     ("Onn Hafiz bin Ghazi" → also "Onn Hafiz Ghazi"); exactness is the
     name signal, the SPARQL filter is the politics signal.
  3. Ambiguity in either direction (one name → several people, or one
     person → several of our candidates) is SKIPPED and reported.
  4. wbgetentities (batches of 50) fetches full claims for accepted QIDs:
     positions (P39 + years), party (P102), birth year (P569, year only),
     en/ms Wikipedia sitelinks. Structured claims only — no prose.

Usage:  python scripts/enrich_wikidata.py <output.json>
"""
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "ballots_jhr.csv"
API = "https://www.wikidata.org/w/api.php"
SPARQL = "https://query.wikidata.org/sparql"
UA = "johor-election-app enrichment (github.com/ElectionNuances/johor-election-app)"
WON = ("won", "won_uncontested")
CHUNK = 250          # name variants per SPARQL query

_last = [0.0]
BASE_DELAY = 2.0
MAX_RETRIES = 5


def throttled_get(url, post_data=None):
    """GET/POST with pacing + Retry-After-aware exponential backoff on 429/5xx."""
    for attempt in range(MAX_RETRIES + 1):
        wait = BASE_DELAY - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        headers = {"User-Agent": UA, "Accept": "application/json"}
        data = None
        if post_data is not None:
            data = urllib.parse.urlencode(post_data).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503) or attempt == MAX_RETRIES:
                raise
            retry_after = e.headers.get("Retry-After")
            backoff = int(retry_after) if (retry_after or "").isdigit() else 10 * (2 ** attempt)
            print(f"    (HTTP {e.code}, backing off {backoff}s)", flush=True)
            time.sleep(min(backoff, 300))


def api(params):
    params = dict(params, format="json")
    return throttled_get(API + "?" + urllib.parse.urlencode(params))


def name_variants(name):
    """Exact-label variants likely used by Wikidata for a Malaysian name."""
    base = re.sub(r"\s*\(.*?\)\s*", " ", name).strip()          # drop alias parens
    alias = None
    m = re.search(r"\((.+?)\)", name)
    if m:
        alias = m.group(1).strip()
    nobin = re.sub(r"\b(bin|binti|bte|bt|a/l|a/p)\b\.?", " ", base, flags=re.I)
    nobin = re.sub(r"\s+", " ", nobin).strip()
    out = {base, nobin}
    if alias and len(alias.split()) >= 2:
        out.add(alias)
    return {v for v in out if len(v.split()) >= 2}


def sparql_match(variants_chunk):
    """One VALUES query: exact label/alias match + politics signal."""
    values = " ".join(
        f'"{v}"@en "{v}"@ms' for v in variants_chunk
    )
    q = f"""
SELECT DISTINCT ?person ?name WHERE {{
  VALUES ?name {{ {values} }}
  ?person rdfs:label|skos:altLabel ?name .
  ?person wdt:P31 wd:Q5 .
  {{ ?person p:P39 ?a }} UNION {{ ?person p:P102 ?b }} UNION {{ ?person wdt:P106 wd:Q82955 }}
  OPTIONAL {{ ?person wdt:P27 ?nat }}
  FILTER(!BOUND(?nat) || ?nat = wd:Q833)
}}"""
    data = throttled_get(SPARQL, post_data={"query": q, "format": "json"})
    out = []
    for b in data["results"]["bindings"]:
        out.append((b["person"]["value"].rsplit("/", 1)[1], b["name"]["value"]))
    return out


def claim_year(snak_time):
    m = re.match(r"[+-](\d{4})", snak_time or "")
    return int(m.group(1)) if m else None


def extract(ent):
    """Structured claims only — positions, party QIDs, birth year, sitelinks."""
    claims = ent.get("claims", {})
    out = {"positions_q": [], "party_q": [], "birth_year": None, "wikipedia": {}}
    for c in claims.get("P39", []):
        try:
            snak = c["mainsnak"]["datavalue"]["value"]["id"]
        except (KeyError, TypeError):
            continue
        years = {}
        for qual, key in (("P580", "from"), ("P582", "to")):
            for q in c.get("qualifiers", {}).get(qual, []):
                try:
                    years[key] = claim_year(q["datavalue"]["value"]["time"])
                except (KeyError, TypeError):
                    pass
        out["positions_q"].append({"q": snak, **years})
    for c in claims.get("P102", []):
        try:
            out["party_q"].append(c["mainsnak"]["datavalue"]["value"]["id"])
        except (KeyError, TypeError):
            continue
    for c in claims.get("P569", []):
        try:
            out["birth_year"] = claim_year(c["mainsnak"]["datavalue"]["value"]["time"])
            break
        except (KeyError, TypeError):
            continue
    for wiki, key in (("enwiki", "en"), ("mswiki", "ms")):
        sl = ent.get("sitelinks", {}).get(wiki)
        if sl:
            prefix = "https://en.wikipedia.org/wiki/" if key == "en" else "https://ms.wikipedia.org/wiki/"
            out["wikipedia"][key] = prefix + urllib.parse.quote(sl["title"].replace(" ", "_"))
    return out


def resolve_labels(qids):
    labels = {}
    qids = sorted(set(qids))
    for i in range(0, len(qids), 50):
        data = api({"action": "wbgetentities", "ids": "|".join(qids[i:i + 50]),
                    "props": "labels", "languages": "en|ms"})
        for qid, ent in data.get("entities", {}).items():
            lbls = ent.get("labels", {})
            labels[qid] = (lbls.get("en") or lbls.get("ms") or {}).get("value", qid)
    return labels


def main() -> int:
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("wikidata_external.json")

    rows = list(csv.DictReader(open(CSV_PATH, newline="", encoding="utf-8")))
    by_uid = defaultdict(list)
    for r in rows:
        by_uid[r["candidate_uid"]].append(r)
    winners = {uid: rs[0]["name"] for uid, rs in by_uid.items()
               if any(r["result"] in WON for r in rs)}
    print(f"matching {len(winners)} ever-winning candidates via bulk SPARQL…", flush=True)

    # variant -> [uids] (a variant shared by two candidates is ambiguous by construction)
    variant_uids = defaultdict(list)
    for uid, name in winners.items():
        for v in name_variants(name):
            variant_uids[v].append(uid)

    variants = sorted(variant_uids)
    print(f"{len(variants)} exact name variants, "
          f"{(len(variants)+CHUNK-1)//CHUNK} SPARQL queries", flush=True)

    # qid <-> uid candidate matches
    uid_qids = defaultdict(set)
    qid_uids = defaultdict(set)
    for i in range(0, len(variants), CHUNK):
        chunk = variants[i:i + CHUNK]
        for qid, matched_name in sparql_match(chunk):
            for uid in variant_uids.get(matched_name, []):
                uid_qids[uid].add(qid)
                qid_uids[qid].add(uid)
        print(f"  …query {i//CHUNK+1} done ({len(uid_qids)} candidates matched so far)", flush=True)

    accepted_qid, ambiguous = {}, []
    for uid, qids in uid_qids.items():
        if len(qids) != 1:
            ambiguous.append({"uid": uid, "name": winners[uid], "qids": sorted(qids),
                              "reason": "one name, several Wikidata people"})
            continue
        qid = next(iter(qids))
        if len(qid_uids[qid]) != 1:
            ambiguous.append({"uid": uid, "name": winners[uid], "qids": [qid],
                              "reason": "several candidates share this Wikidata person"})
            continue
        accepted_qid[uid] = qid

    # fetch full claims for accepted QIDs in batches
    accepted = {}
    qlist = sorted(set(accepted_qid.values()))
    ents = {}
    for i in range(0, len(qlist), 50):
        data = api({"action": "wbgetentities", "ids": "|".join(qlist[i:i + 50]),
                    "props": "labels|descriptions|claims|sitelinks"})
        ents.update(data.get("entities", {}))
    today = time.strftime("%Y-%m-%d")
    for uid, qid in accepted_qid.items():
        ent = ents.get(qid)
        if not ent or ent.get("missing") is not None:
            continue
        d = extract(ent)
        d["wikidata_qid"] = qid
        d["verified"] = f"exact label match + politics signal (SPARQL), {today}"
        accepted[uid] = d

    # resolve position/party labels
    all_q = [p["q"] for d in accepted.values() for p in d["positions_q"]]
    all_q += [q for d in accepted.values() for q in d["party_q"]]
    labels = resolve_labels(all_q) if all_q else {}
    for d in accepted.values():
        d["positions"] = [{"label": labels.get(p["q"], p["q"]),
                           **{k: v for k, v in p.items() if k in ("from", "to") and v}}
                          for p in d.pop("positions_q")]
        d["parties"] = sorted({labels.get(q, q) for q in d.pop("party_q")})

    report = {"accepted": len(accepted), "ambiguous_skipped": ambiguous,
              "no_match": len(winners) - len(uid_qids), "searched": len(winners)}
    out_path.write_text(json.dumps(accepted, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
    Path(str(out_path) + ".report").write_text(
        json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\naccepted {len(accepted)} / searched {len(winners)} "
          f"(ambiguous-skipped {len(ambiguous)}, no-match {report['no_match']})")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
