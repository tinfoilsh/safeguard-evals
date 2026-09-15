#!/usr/bin/env python3
"""Shared filtering core. Import this to just apply the filter.

    from anchors import anchor_hit, norm
    if anchor_hit(first_user_turn): ...   # -> anchor name, or None
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ANCHOR_FILE = os.path.join(HERE, "regex_anchors.json")

# Anchors are induced from, and matched against, normalized text: lowercased, whitespace
# collapsed to single spaces, stripped. MATCH_HEAD keeps matching O(1) per message and is
# deep enough to reach interior-invariant anchors (see regroup_candidates.long_generate).
MATCH_HEAD = 2000


def norm(s):
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def load_anchors(path=ANCHOR_FILE):
    with open(path) as f:
        return [(a["name"], re.compile(a["pattern"])) for a in json.load(f)]


ANCHORS = load_anchors()


def anchor_hit(text):
    """Name of the first anchor matching `text` (head only), else None."""
    if not text:
        return None
    head = norm(text[:MATCH_HEAD])
    for name, rx in ANCHORS:
        if rx.search(head):
            return name
    return None
