#!/usr/bin/env python3
"""Validate the anchor set. Hard failures exit nonzero; softer issues print as warnings.

Checks:
  - every pattern compiles
  - no anchor could-never-fire against norm()-ed text (norm lowercases, collapses whitespace
    to single spaces, strips — so an uppercase, tab/newline, or multi-space literal can never
    appear in a match target)
  - each anchor carries enough invariant literal (warn under MIN_LITERAL chars)
  - subsumption: one anchor's distinctive literal core contained in another's
  - round-trip of the induction code via long_generate (the real --induce path): induce a
    synthetic family, assert the pattern matches every member and neither benign control

Literal extraction assumes induced-anchor grammar (re.escape'd fragments joined by .*?).
A hand-written pattern using regex constructs like (a|b) may trip a spurious failure.

    python validate_anchors.py
"""

import json
import os
import re
import sys

from anchors import ANCHOR_FILE, norm
from regroup_candidates import long_generate

MIN_LITERAL = (
    80  # chars of invariant literal below which an anchor is suspiciously loose
)


def literals(pattern):
    """Unescape the fixed fragments (the non-wildcard runs) of an induced pattern."""
    return [re.sub(r"\\(.)", r"\1", f) for f in pattern.split(".*?") if f]


def norm_violation(lit):
    if lit != lit.lower():
        return "uppercase literal (norm lowercases)"
    if re.search(r"[^\S ]", lit):
        return "tab/newline literal (norm collapses to single space)"
    if "  " in lit:
        return "multi-space literal (norm collapses to single space)"
    return None


def check_induction():
    fam = [
        "SYSTEM: You are ModBot v2. Evaluate the following message for policy violations "
        "and return TRUE or FALSE. Message: hello there, how are you doing today",
        "SYSTEM: You are ModBot v2. Evaluate the following message for policy violations "
        "and return TRUE or FALSE. Message: check out my brand new online store",
        "SYSTEM: You are ModBot v2. Evaluate the following message for policy violations "
        "and return TRUE or FALSE. Message: what is the weather like right now",
    ]
    controls = [
        "please write me a short poem about the ocean at sunrise",
        "what is the capital of france and how many people live there",
    ]
    pat = long_generate(fam)
    if not pat:
        return "induction produced no pattern from a clearly-templated family"
    rx = re.compile(pat)
    if not all(rx.search(norm(m)) for m in fam):
        return "induced pattern failed to match a family member"
    if any(rx.search(norm(c)) for c in controls):
        return "induced pattern matched a benign control string"
    return None


def main():
    anchors = json.load(open(ANCHOR_FILE))
    fails, warns = [], []

    cores = {}  # name -> longest literal fragment
    lit_by_name = {}
    for a in anchors:
        name, pat = a["name"], a["pattern"]
        try:
            re.compile(pat)
        except re.error as e:
            fails.append(f"{name}: does not compile ({e})")
            continue
        lits = literals(pat)
        lit_by_name[name] = lits
        for lit in lits:
            v = norm_violation(lit)
            if v:
                fails.append(f"{name}: could never fire — {v}: {lit[:50]!r}")
        litlen = sum(len(x) for x in lits)
        if litlen < MIN_LITERAL:
            warns.append(
                f"{name}: only {litlen} chars of invariant literal (< {MIN_LITERAL})"
            )
        cores[name] = max(lits, key=len) if lits else ""

    # subsumption: a distinctive core contained in another anchor's core
    names = list(cores)
    for i, a in enumerate(names):
        for b in names:
            if (
                a != b
                and cores[a]
                and cores[a] in cores[b]
                and len(cores[a]) < len(cores[b])
            ):
                warns.append(f"{a}: core subsumed by {b} ({cores[a][:40]!r})")
                break

    ind = check_induction()
    if ind:
        fails.append(f"induction round-trip: {ind}")

    print(f"validated {len(anchors)} anchors")
    for w in warns:
        print(f"  WARN  {w}")
    for f in fails:
        print(f"  FAIL  {f}")
    if fails:
        print(f"\n{len(fails)} hard failure(s), {len(warns)} warning(s)")
        sys.exit(1)
    print(f"\nOK — 0 hard failures, {len(warns)} warning(s)")


if __name__ == "__main__":
    main()
