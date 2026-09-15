#!/usr/bin/env python3
"""Per-shard WildChat sweep: drop known bots, accumulate candidate clusters. Resumable.

For each shard (checkpointed):
  1. DROP  — English conversations whose first user turn hits an anchor are removed; the
     rest are written to data/curated/<shard>.parquet (all columns preserved).
  2. FOLD  — kept first-user turns are clustered by normalized prefix and folded into a
     persistent candidate store (data/candidates.json). A prefix that repeats >= 2x in a
     shard starts a candidate; one-off prompts are never stored, so the store stays bounded.

Ends by concatenating every checkpoint into data/curated/wildchat_curated.parquet.
Prints per-anchor hit counts for the run (counts are always regenerable this way).

    python sweep_shards.py                       # all shards, resumable (skips done ones)
    SHARDS=train-00125-of-00127.parquet python sweep_shards.py
    FORCE=1 python sweep_shards.py               # full rebuild from raw shards
    REAPPLY=1 python sweep_shards.py             # re-filter existing checkpoints
"""

import glob
import json
import os
from collections import Counter, defaultdict
from difflib import SequenceMatcher

import pyarrow as pa
import pyarrow.parquet as pq
from anchors import anchor_hit, norm

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
RAW_DIR = os.path.join(DATA, "original")
CKPT_DIR = os.path.join(DATA, "curated")
STATE = os.path.join(DATA, "candidates.json")
OUT = os.path.join(CKPT_DIR, "wildchat_curated.parquet")

FORCE = os.environ.get("FORCE", "0") == "1"
# REAPPLY re-filters the already-cleaned checkpoints (small) with the current anchors
# instead of re-reading the raw shards. Valid ONLY when anchors were ADDED since the last
# full pass: anchors only ever remove rows, so a superset applied to the already-filtered
# set == applied to raw. If an anchor was REMOVED, use FORCE — the rows it deleted are not
# in the checkpoints to restore.
REAPPLY = os.environ.get("REAPPLY", "0") == "1"
FRESH = FORCE or REAPPLY  # both rebuild candidates + reprocess every shard

BLOCK_LEN = 40  # prefix length used to shortlist existing candidates
MEMBER_CAP = 25  # sample members kept per candidate (for later induction)
MEMBER_TRUNC = 400  # chars stored per sample member
FREQ_KEY_LEN = 200  # prefix length candidates are keyed on
FREQ_MIN_LEN = 150  # ignore prompts shorter than this
FREQ_SIM = 0.95  # fuzzy-merge threshold for near-identical keys

HITS = Counter()


def shard_list():
    env = os.environ.get("SHARDS")
    if env:
        return [s.strip() for s in env.split(",") if s.strip()]
    base = CKPT_DIR if REAPPLY else RAW_DIR
    return sorted(
        os.path.basename(p)
        for p in glob.glob(os.path.join(base, "train-*-of-*.parquet"))
    )


def first_user(conv):
    if conv and conv[0].get("role") == "user":
        return str(conv[0].get("content") or "")
    return ""


def load_state():
    if os.path.exists(STATE) and not FRESH:
        with open(STATE) as f:
            return json.load(f)
    return {"processed": [], "candidates": []}


def save_state(state):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE)


def update_candidates(kept, shard, cands):
    """Fold a shard's kept (first_user, turn_count) pairs into the candidate store.
    Returns (new, grown) records for reporting."""
    block = defaultdict(list)
    for rec in cands:
        block[rec["key"][:BLOCK_LEN]].append(rec)

    buckets = defaultdict(list)
    for text, turn in kept:
        n = norm(text)
        if len(n) >= FREQ_MIN_LEN:
            buckets[n[:FREQ_KEY_LEN]].append((text, turn))

    new, grown = [], []
    for key, members in buckets.items():
        texts = [t for t, _ in members]
        n_single = sum(1 for _, turn in members if turn == 1)  # bots are single-turn
        match = None
        for rec in block.get(key[:BLOCK_LEN], []):
            if (
                key == rec["key"]
                or SequenceMatcher(None, key, rec["key"]).ratio() >= FREQ_SIM
            ):
                match = rec
                break
        if match:
            match["count"] += len(texts)
            match["n_single"] = match.get("n_single", 0) + n_single
            match["shards"][shard] = match["shards"].get(shard, 0) + len(texts)
            for t in texts[: MEMBER_CAP - len(match["members"])]:
                match["members"].append(t[:MEMBER_TRUNC])
            grown.append(match)
        elif len(texts) >= 2:  # only repeats become candidates; singletons dropped
            rec = {
                "key": key,
                "example": texts[0][:MEMBER_TRUNC],
                "count": len(texts),
                "n_single": n_single,
                "shards": {shard: len(texts)},
                "members": [t[:MEMBER_TRUNC] for t in texts[:MEMBER_CAP]],
            }
            cands.append(rec)
            block[key[:BLOCK_LEN]].append(rec)
            new.append(rec)
    return new, grown


def process_shard(name, state):
    ckpt = os.path.join(CKPT_DIR, name)
    if name in state["processed"] and os.path.exists(ckpt) and not FRESH:
        print(f"{name}: already processed (skip)")
        return
    t = pq.read_table(ckpt if REAPPLY else os.path.join(RAW_DIR, name))
    rows = t.to_pylist()
    firsts = [first_user(r["conversation"]) for r in rows]
    hits = [
        anchor_hit(f) if r["language"] == "English" else None
        for r, f in zip(rows, firsts)
    ]
    for h in hits:
        if h:
            HITS[h] += 1
    mask = [r["language"] == "English" and h is None for r, h in zip(rows, hits)]
    filt = t.filter(pa.array(mask))
    os.makedirs(CKPT_DIR, exist_ok=True)
    tmp = ckpt + ".tmp"
    pq.write_table(filt, tmp)
    os.replace(tmp, ckpt)

    kept = [(f, r["turn"]) for m, f, r in zip(mask, firsts, rows) if m]
    new, grown = update_candidates(kept, name, state["candidates"])
    if name not in state["processed"]:
        state["processed"].append(name)
    save_state(state)

    n_eng = sum(1 for r in rows if r["language"] == "English")
    n_anchor = sum(1 for h in hits if h)
    print(
        f"{name}: {len(rows)} rows -> {n_eng} English, {n_anchor} removed by anchors"
        f" -> {filt.num_rows} kept"
    )
    print(
        f"   candidates: {len(new)} new, {len(grown)} grown (store now {len(state['candidates'])})"
    )
    for rec in sorted(new, key=lambda r: -r["count"])[:5]:
        print(f"     NEW  x{rec['count']:<4} {rec['key'][:90]}")


def main():
    shards = shard_list()
    state = load_state()
    tag = " (FORCE)" if FORCE else " (REAPPLY)" if REAPPLY else ""
    print(
        f"{len(shards)} shard(s){tag} | already processed: {len(state['processed'])}\n"
    )
    for s in shards:
        process_shard(s, state)

    if HITS:
        print("\nper-anchor hits this run:")
        for name, n in HITS.most_common():
            print(f"  {n:>8,}  {name}")
        print(f"  {'':>8}  total removed {sum(HITS.values()):,}")

    ckpts = sorted(glob.glob(os.path.join(CKPT_DIR, "train-*.parquet")))
    out = pa.concat_tables([pq.read_table(p) for p in ckpts])
    tox = sum(1 for x in out.column("toxic").to_pylist() if x)
    pq.write_table(out, OUT)
    print(f"\ncurated: {out.num_rows} convos ({tox} toxic) from {len(ckpts)} shard(s)")
    print(f"candidates tracked: {len(state['candidates'])}  ->  candidates.json")


if __name__ == "__main__":
    main()
