#!/usr/bin/env python3
"""Chop a monitor run down to just its flagged conversations, with full text.

Takes a monitor results jsonl (the hard-no judge's output) and the conversations it was
run on, keeps every conversation the judge flagged (violation=true), joins the full
text back in, and writes one jsonl carrying both the conversation and the judge's
verdict. 

  run_second_pass.py FLAGGED=<this file>   # kimi reviews the judge's verdict (primed)

Output line: {id, conversation:[{role,content}], judge_reason, judge_categories, turns}


Usage:
  python build_flagged.py \
    --conversations convs.parquet \
    [--monitor results/monitor_safeguard.jsonl] [--out results/flagged.jsonl]
"""

import argparse
import json
from pathlib import Path

from run_monitor import iter_convs

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--conversations",
        required=True,
        help="the .parquet/.jsonl the monitor ran on (for the conversation text)",
    )
    ap.add_argument(
        "--monitor",
        default=str(HERE / "results" / "monitor_safeguard.jsonl"),
        help="monitor results jsonl to pull flags from",
    )
    ap.add_argument("--out", default=str(HERE / "results" / "flagged.jsonl"))
    args = ap.parse_args()

    flagged = {}
    for line in Path(args.monitor).read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("violation") is True:
            flagged[r["id"]] = {
                "reason": r.get("reason") or "",
                "categories": r.get("categories") or [],
            }
    print(f"flagged in {Path(args.monitor).name}: {len(flagged)}")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    written = 0
    remaining = set(flagged)
    with open(args.out, "w") as out_fh:
        for cid, conv in iter_convs(args.conversations):
            if cid not in remaining:
                continue
            v = flagged[cid]
            out_fh.write(
                json.dumps(
                    {
                        "id": cid,
                        "conversation": [
                            {
                                "role": m.get("role") or "?",
                                "content": m.get("content") or "",
                            }
                            for m in (conv or [])
                        ],
                        "judge_reason": v["reason"],
                        "judge_categories": v["categories"],
                        "turns": len(conv or []),
                    }
                )
                + "\n"
            )
            remaining.discard(cid)
            written += 1
            if not remaining:
                break

    print(
        f"wrote {written} flagged conversations -> {args.out}"
        + (
            f"  [{len(remaining)} flagged ids not found in conversations]"
            if remaining
            else ""
        )
    )


if __name__ == "__main__":
    main()
