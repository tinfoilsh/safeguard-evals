#!/usr/bin/env python3
"""Convert a rate_*.jsonl (run.py output) into the items_*.json shape policy_judges.py reads.

run.py writes the flat viewer feed {ref, source, category, prompt, response,
reasoning, asr, verdict, ok, ...}. policy_judges.py scores captured answers from an
items file ({ref, source, category, prompt, answer, judge: {name, complied}}), so a
new model's run has to be reshaped before the hard-no set can be judged. Rows whose
target call failed (ok=false / asr=null) are kept with judge.complied=null so the
policy judges still see them.

  python rate_to_items.py data/rate_glm-5-3.jsonl data/items_glm-5-3.json
"""

import json
import sys
from pathlib import Path


def main():
    if len(sys.argv) != 3:
        print("usage: rate_to_items.py <rate_*.jsonl> <out_items.json>")
        sys.exit(1)
    rows = [
        json.loads(line)
        for line in Path(sys.argv[1]).read_text().splitlines()
        if line.strip()
    ]
    out = Path(sys.argv[2])
    items = []
    errors = 0
    for r in rows:
        asr = r.get("asr")
        complied = None if asr is None else bool(asr)
        errors += complied is None
        items.append(
            {
                "ref": r["ref"],
                "source": r["source"],
                "category": r["category"],
                "prompt": r["prompt"],
                "answer": r.get("response", ""),
                "judge": {"name": f"{r['source']}_asr", "complied": complied},
            }
        )
    out.write_text(json.dumps(items, indent=1))
    print(f"wrote {len(items)} items, {errors} unjudged -> {out}")


if __name__ == "__main__":
    main()
