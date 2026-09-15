# WildChat data curation

WildChat's late shards are swamped by automated bots. The monitor's false-positive tuning needs _real human conversations_, not bot exhaust, so we filter the full dataset down to English,
de-botted prompts: **4.8M → ~1.2M** conversations.

**The filter already exists and can be run as-is**: `regex_anchors.json` ships with the 87
anchors accumulated over our passes. To just apply it — no discovery, no review — download
the shards and run `python sweep_shards.py`, or import it directly:

```python
from anchors import anchor_hit
anchor_hit(first_user_message)   # -> anchor name, or None if clean
```

The loop below is only needed to _extend_ the anchor set.

## The loop

Removal is **only ever** done by _anchors_ — long, highly-specific regexes (hundreds of
chars of invariant) that essentially never match a real one-off prompt. Everything else is
discovery: it surfaces candidate bot families to a human, who decides what becomes an anchor.
We only ever _add_ anchors.

```
  sweep  ──▶  regroup  ──▶  human review  ──▶  promote induced regex to anchors
    ▲                                                     │
    └──────────────  REAPPLY re-sweep  ◀──────────────────┘   (repeat)
```

The sweep drops known bots and accumulates repeated prefixes as candidates. Regroup reunites
bot families whose fronts vary. A human reviews the families (`view_regroup.py`), promotes the
good ones by pasting the _induced_ regex (`regroup_candidates.py --induce`) into
`regex_anchors.json`, then re-sweeps with `REAPPLY=1` to apply the new anchors. Repeat until
clean. Per-anchor removal counts are not stored — they are regenerable from a **full** sweep's
printed tally.

## Files

| file                    | role                                                                                                                                            |
| ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `anchors.py`            | shared core: `norm()`, `load_anchors()`, `anchor_hit()`. Import this to just apply the filter.                                                  |
| `regex_anchors.json`    | the anchor rule set — `{name, pattern}` per entry. The only removal rule.                                                                       |
| `download_all.sh`       | pull the 127 gated raw shards (needs `HF_KEY` in the repo-root `.env`).                                                                         |
| `sweep_shards.py`       | per-shard driver: drop anchor-hitting English convos, fold survivors into the candidate store, concat checkpoints → `wildchat_curated.parquet`. |
| `regroup_candidates.py` | re-unite front-variable bot families by shared interior content; induce a promotion-ready regex per family (`--induce`).                        |
| `view_regroup.py`       | human review surface → `output/regroup.html`.                                                                                                   |
| `validate_anchors.py`   | lint the anchor set (compiles, could-never-fire, min literal, subsumption, induction round-trip).                                               |
| `build_subset.py`       | sample an early-weighted toxic/benign working subset from the curated parquet.                                                                  |

## Environment / flags

`sweep_shards.py` is driven by env vars:

- `SHARDS=a.parquet,b.parquet` — process only these (default: all found).
- `FORCE=1` — full rebuild from the raw shards (rebuilds candidates, reprocesses everything).
- `REAPPLY=1` — re-filter the existing (already-cleaned) checkpoints with the current anchors
  instead of re-reading raw. **Valid only when anchors were ADDED** since the last full pass:
  anchors only ever remove rows, so a superset applied to the already-filtered set equals the
  same superset applied to raw. If an anchor was _removed_, use `FORCE` — the rows it deleted
  aren't in the checkpoints to restore.

## Running a pass

1. `./download_all.sh` — pull the 127 gated shards into `data/original/`.
2. `python sweep_shards.py` — drop known bots, accumulate candidates (resumable; skips
   already-processed shards). Prints per-anchor hit counts.
3. `python regroup_candidates.py` — regroup the candidate store into families →
   `data/regroup_candidates.json`.
4. `python view_regroup.py` — review the families in `output/regroup.html`; decide which are
   real bots. High cluster-merge counts are a _review prompt_, not a green light.
5. Promote: `python regroup_candidates.py --induce <family-id>` (the `#N` from
   `regroup.html`) prints a paste-ready anchor entry; add it to `regex_anchors.json`. Run
   `python validate_anchors.py`.
6. `REAPPLY=1 python sweep_shards.py` — re-filter the checkpoints with the new anchors.
7. Repeat 3–6 until the store is clean, or until none of the remaining clusters are worth removing.

Downstream: `python build_subset.py` samples an early-weighted working subset from
`data/curated/wildchat_curated.parquet` into `data/subsets/`.

## Layout

Code + `regex_anchors.json` are committed. Everything under `data/` and `output/` is
gitignored: `data/original/` (raw shards), `data/curated/` (survivors + combined parquet),
`data/candidates.json`, `data/regroup_candidates.json`, `data/subsets/`, `output/*.html`.
