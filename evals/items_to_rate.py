#!/usr/bin/env python3
"""Convert an items_*.json into the rate_*.jsonl shape viewer.py reads.

An items file is one model's answers to the 1,500 harmbench+ailuminate questions
({ref, source, category, prompt, answer, judge}). This flattens it into the rate
row shape {ref, source, category, prompt, response, reasoning, asr, verdict} so
viewer.py can browse it. gpt-oss and llama only exist as items files;
deepseek/kimi already ship as rate_*.jsonl.

  python items_to_rate.py data/items_gpt-oss.json data/rate_gpt-oss.jsonl
"""

import ast
import json
import sys
from pathlib import Path


def main():
    if len(sys.argv) != 3:
        print("usage: items_to_rate.py <items_*.json> <out_rate.jsonl>")
        sys.exit(1)
    items = json.loads(Path(sys.argv[1]).read_text())
    out = Path(sys.argv[2])
    n = comp = 0
    with out.open("w") as f:
        for it in items:
            j = it.get("judge")
            if isinstance(j, str):
                j = ast.literal_eval(j)
            complied = j.get("complied") if isinstance(j, dict) else None
            asr = None if complied is None else (1 if complied else 0)
            f.write(json.dumps({
                "ref": it.get("ref", ""),
                "source": it.get("source", ""),
                "category": it.get("category", ""),
                "prompt": it.get("prompt", ""),
                "response": it.get("answer", ""),
                "reasoning": "",
                "asr": asr,
                "verdict": ("complied" if complied else "refused") if complied is not None else "error",
            }) + "\n")
            n += 1
            comp += asr == 1
    print(f"wrote {n} rows, {comp} complied -> {out}")


if __name__ == "__main__":
    main()
