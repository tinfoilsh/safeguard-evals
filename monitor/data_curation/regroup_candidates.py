#!/usr/bin/env python3
"""Regroup accumulated candidates into bot FAMILIES by shared interior content, and induce
a promotion-ready regex per family. Run after candidates have built up.

Prefix-clustering (the sweep's fast path) shatters any bot whose variable part is at the
FRONT (different greeting / language) into separate clusters. This re-unites them by
content, not prefix:

  Pass 1: shingle each candidate's example into word n-grams and count, per shingle, how
          many candidates contain it.
  Pass 2: each candidate's SIGNATURE = its most-shared shingle (the invariant interior line
          recurs across the whole family, so front-variable siblings pick the SAME shingle).
          Bucket by signature — that's the family.

Mostly-multi-turn families are hidden: bots fire one prompt per call (~all single-turn),
while real-user repeats (story sagas) iterate over many turns.

Each family's members go through long_generate to build one flexible regex pinning the
shared interior line and wildcarding the variable front/back. Writes
data/regroup_candidates.json for review; nothing is promoted automatically.

    python regroup_candidates.py                 # regroup + write proposals
    python regroup_candidates.py --induce 3       # paste-ready anchor for family #3 (as numbered in regroup.html)
"""

import argparse
import json
import os
import random
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher

from anchors import norm

HERE = os.path.dirname(os.path.abspath(__file__))
CANDS = os.path.join(HERE, "data", "candidates.json")
OUTFILE = os.path.join(HERE, "data", "regroup_candidates.json")

SHINGLE_N = 7  # words per shingle
HEAD = 2000  # chars of each example considered (matches anchors.MATCH_HEAD)
GEN_CAP = 120  # only induce regexes for the top-N families (bounds O(len^2) work)
MIN_SINGLE = float(
    os.environ.get("REGROUP_MIN_SINGLE", "0.5")
)  # hide mostly-multi-turn


def induce_pattern(texts, min_frag=4, min_invariant=40, head=200, max_members=80):
    """Build a regex (on normalized text) from a cluster of similar messages: keep the
    character runs common to ALL members and replace differing spans with `.*?`. Returns ''
    if too little is shared to be a safe anchor (< min_invariant chars of literal)."""
    norms = [n[:head] for n in (norm(t) for t in texts[:max_members]) if n]
    if not norms:
        return ""
    if len(norms) == 1:
        return re.escape(norms[0])
    SEP = "\x01"  # sentinel between fragments; never appears in text
    frags = [norms[0]]
    for m in norms[1:]:
        text = SEP.join(frags)
        new = []
        for blk in SequenceMatcher(None, text, m, autojunk=False).get_matching_blocks():
            if blk.size:
                # split so a matched block never spans two fragments (keeps every
                # fragment a literal substring of every member)
                new.extend(p for p in text[blk.a : blk.a + blk.size].split(SEP) if p)
        frags = new
        if not frags:
            return ""
    frags = [f for f in frags if len(f.strip()) >= min_frag]
    if sum(len(f) for f in frags) < min_invariant:
        return ""
    return r".*?".join(re.escape(f) for f in frags)


def long_generate(members, min_frag=30, max_members=15, head=2000, longest_only=False):
    """Induce a FLEXIBLE regex for a bot whose invariant sits in the MIDDLE of the message.
    Scans the whole message and keeps only LONG common substrings (>= min_frag), pinning the
    distinctive interior line and wildcarding everything variable around it. Members are
    sampled RANDOMLY (seeded) so variable slots get wildcarded rather than baked in."""
    if len(members) > max_members:
        members = random.Random(0).sample(members, max_members)
    pat = induce_pattern(
        members,
        min_frag=min_frag,
        min_invariant=min_frag,
        head=head,
        max_members=max_members,
    )
    if longest_only and pat:
        return max(pat.split(r".*?"), key=len)
    return pat


def shingles(text):
    w = norm(text[:HEAD]).split()
    if len(w) < SHINGLE_N:
        return {" ".join(w)} if w else set()
    return {" ".join(w[i : i + SHINGLE_N]) for i in range(len(w) - SHINGLE_N + 1)}


def build_families(cands):
    """Return families (lists of candidates) sorted by total count, and the shingle df."""
    df = Counter()
    cand_sh = []
    for c in cands:
        sh = shingles(c["example"])
        cand_sh.append(sh)
        for s in sh:
            df[s] += 1

    families = defaultdict(list)
    for c, sh in zip(cands, cand_sh):
        if not sh:
            continue
        sig = max(sh, key=lambda s: (df[s], len(s)))
        families[sig].append(c)

    def single_pct(cs):
        tot = sum(c["count"] for c in cs)
        return sum(c.get("n_single", 0) for c in cs) / tot if tot else 0.0

    allfams = sorted(families.values(), key=lambda cs: -sum(c["count"] for c in cs))
    fams = [cs for cs in allfams if single_pct(cs) >= MIN_SINGLE]
    return fams, allfams, df, single_pct


def family_members(cs):
    return [m for c in cs for m in c["members"]]


def induce(family_id):
    # 1-based, matching the #N labels in output/regroup.html
    cands = json.load(open(CANDS))["candidates"]
    fams, _, _, _ = build_families(cands)
    if not 1 <= family_id <= len(fams):
        raise SystemExit(f"family {family_id} out of range (1..{len(fams)})")
    cs = fams[family_id - 1]
    pat = long_generate(family_members(cs))
    if not pat:
        raise SystemExit("induction produced no safe pattern (too little shared)")
    sig = max(shingles(cs[0]["example"]), key=len, default="")[:40].strip()
    name = re.sub(r"[^a-z0-9]+", "-", sig).strip("-") or f"family-{family_id}"
    print(json.dumps({"name": name, "pattern": pat}, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--induce",
        type=int,
        metavar="FAMILY_ID",
        help="print a paste-ready anchor JSON entry for one family and exit",
    )
    args = ap.parse_args()
    if args.induce is not None:
        return induce(args.induce)

    cands = json.load(open(CANDS))["candidates"]
    fams, allfams, df, single_pct = build_families(cands)
    print(
        f"{len(cands)} candidates -> {len(allfams)} families "
        f"({sum(1 for f in allfams if len(f) > 1)} merged >1 cluster); "
        f"hid {len(allfams) - len(fams)} mostly-multi-turn (real-user) families"
    )

    out = []
    for i, cs in enumerate(fams):
        out.append(
            {
                "signature": max(
                    shingles(cs[0]["example"]), key=lambda s: df[s], default=""
                ),
                "n_clusters": len(cs),
                "count": sum(c["count"] for c in cs),
                "single_turn_pct": round(single_pct(cs), 3),
                "example": cs[0]["example"],
                "pattern": long_generate(family_members(cs)) if i < GEN_CAP else "",
            }
        )

    json.dump(out, open(OUTFILE, "w"), indent=2)
    print("\ntop families (count | #clusters merged | signature):")
    for r in out[:20]:
        print(f"  {r['count']:>6,}  x{r['n_clusters']:<3} {r['signature'][:70]}")
    print("\nwrote -> regroup_candidates.json")


if __name__ == "__main__":
    main()
