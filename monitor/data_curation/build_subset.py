#!/usr/bin/env python3
"""Sample a fixed working subset out of the curated WildChat parquet.

Composition:
  - n-toxic + n-benign rows (by the `toxic` column)
  - chronologically early-weighted: each stratum is sampled with density w(u) = exp(-k*u)
    over chronological rank u in [0,1], k set so ~early_frac of the mass lands in the first
    half of the data.

Outputs (data/subsets/, gitignored):
  - <stem>.parquet           full rows, same schema, ascending orig-idx order
  - <stem>.manifest.json     params + per-bucket counts + orig_idx (row i == orig_idx[i])
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
CURATED = HERE / "data" / "curated" / "wildchat_curated.parquet"
OUTDIR = HERE / "data" / "subsets"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-toxic", type=int, default=20000)
    ap.add_argument("--n-benign", type=int, default=30000)
    ap.add_argument("--early-frac", type=float, default=0.8,
                    help="fraction of each stratum from the earlier half")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default=None,
                    help="output stem (default wildchat_<total>k)")
    args = ap.parse_args()
    stem = args.out or f"wildchat_{(args.n_toxic + args.n_benign) // 1000}k"

    pf = pq.ParquetFile(str(CURATED))
    n = pf.metadata.num_rows

    # pass 1 (light): timestamp + toxic for every row
    ts_parts, tox_parts = [], []
    for b in pf.iter_batches(columns=["timestamp", "toxic"], batch_size=131072):
        ts_parts.append(b.column("timestamp").cast("int64").to_numpy(zero_copy_only=False))
        tox_parts.append(b.column("toxic").to_numpy(zero_copy_only=False))
    ts = np.concatenate(ts_parts)
    tox = np.concatenate(tox_parts).astype(bool)
    idx = np.arange(n)

    # chronological position u in [0,1] by RANK (empirical CDF over timestamp), so the
    # "first half of the data" is the earlier half of rows by count, not by clock.
    order = np.argsort(ts, kind="stable")
    u = np.empty(n)
    u[order] = np.arange(n) / (n - 1)

    # early-weighting: w(u) = exp(-k*u); k chosen so mass over the first half integrates
    # to early_frac:  integral_0^0.5 w / integral_0^1 w = 1/(1+e^{-k/2}) = f
    #   -> k = 2 ln(f/(1-f)).
    f = args.early_frac
    k = 2.0 * np.log(f / (1.0 - f))
    w = np.exp(-k * u)
    print(f"{n} rows | toxic={tox.sum()} benign={(~tox).sum()} | "
          f"curve k={k:.3f} (target {f:.0%} mass in first half)")

    rng = np.random.default_rng(args.seed)

    def pick(mask, want):
        pool = idx[mask]
        if len(pool) <= want:
            print(f"  WARN: wanted {want}, only {len(pool)} available -> taking all")
            return pool
        p = w[mask].astype("float64")
        p /= p.sum()
        return rng.choice(pool, size=want, replace=False, p=p)

    stats, sel = {}, []
    for name, tmask, total in [("toxic", tox, args.n_toxic), ("benign", ~tox, args.n_benign)]:
        chosen = pick(tmask, total)
        first_half = float((u[chosen] < 0.5).mean())
        stats[name] = {"n": int(len(chosen)), "pct_first_half": round(first_half, 3)}
        sel.extend(chosen.tolist())
        print(f"  {name}: n={len(chosen)} first-half share={first_half:.1%}")

    selected = sorted(set(sel))
    wset = set(selected)
    print(f"selected {len(selected)} conversations")

    # pass 2 (heavy): copy full rows for the selected idxs, in ascending idx order
    OUTDIR.mkdir(parents=True, exist_ok=True)
    out_parquet = OUTDIR / f"{stem}.parquet"
    writer = pq.ParquetWriter(str(out_parquet), pf.schema_arrow)
    off = 0
    for b in pf.iter_batches(batch_size=8192):
        local = [g - off for g in range(off, off + b.num_rows) if g in wset]
        if local:
            writer.write_table(pa.Table.from_batches([b.take(pa.array(local, pa.int64()))]))
        off += b.num_rows
    writer.close()

    manifest = {
        "source": str(CURATED),
        "seed": args.seed,
        "early_frac": args.early_frac,
        "n_toxic": args.n_toxic,
        "n_benign": args.n_benign,
        "n_total": len(selected),
        "curve_k": float(k),
        "stats": stats,
        "orig_idx": selected,  # parquet row i == curated row orig_idx[i]
    }
    (OUTDIR / f"{stem}.manifest.json").write_text(json.dumps(manifest))
    print(f"wrote {out_parquet} ({len(selected)} rows) + {stem}.manifest.json")


if __name__ == "__main__":
    main()
