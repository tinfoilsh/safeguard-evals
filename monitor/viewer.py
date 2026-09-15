#!/usr/bin/env python3
"""Self-contained HTML viewer for monitor / second-pass runs, side by side.

One conversation per page, each run's verdict as a column, over the full conversation.
Two shapes:

  # two judges compared (2 columns)
  python viewer.py oss=results/monitor_oss.jsonl safeguard=results/monitor_safeguard.jsonl

  # a judge + two comparisons (3 columns) — the second-pass setup
  python viewer.py --judge \
    second_pass=results/second_pass_kimi-k3.jsonl cold=results/monitor_kimi.jsonl

`--base` (default results/flagged.jsonl, from build_flagged.py) supplies the conversation
text and, with `--judge`, the original judge verdict as the first column. Runs are joined
to it by id; each is a column labelled `name=` (or its filename). The ruling filter
(both yes / split / both no) is computed over the two comparison columns.

Markup/CSS/JS live in template.html; this injects __DATA__ / __TITLE__.

Usage:
  python viewer.py [--base results/flagged.jsonl] [--judge] [name=]run.jsonl [more...] [--out viewer.html]
"""

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "template.html"
MSG_CAP = 6000


def load_jsonl(p):
    return [json.loads(x) for x in Path(p).read_text().splitlines() if x.strip()]


def cap(s):
    s = s or ""
    return (
        s
        if len(s) <= MSG_CAP
        else s[:MSG_CAP] + f"\n…[truncated {len(s) - MSG_CAP} chars]"
    )


def run_verdict(r):
    return {
        "violation": r.get("violation"),
        "categories": r.get("categories") or [],
        "reason": r.get("reason") or "",
        "reasoning": (r.get("reasoning") or "").strip(),
    }


def ruling(cols):
    """both-yes / split / both-no over the comparison columns' violation booleans."""
    vs = [c["violation"] for c in cols]
    if all(v is True for v in vs):
        return "yes"
    if all(v is False for v in vs):
        return "no"
    return "split"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--base",
        default=str(HERE / "results" / "flagged.jsonl"),
        help="flagged.jsonl (conversation text + judge verdict)",
    )
    ap.add_argument(
        "--judge",
        action="store_true",
        help="show the base file's own judge verdict as the first column",
    )
    ap.add_argument("--out", default=str(HERE / "viewer.html"))
    ap.add_argument(
        "runs",
        nargs="+",
        metavar="[name=]run.jsonl",
        help="comparison run jsonls (joined to base by id)",
    )
    args = ap.parse_args()

    # base: id -> conversation + judge verdict
    base = {}
    for o in load_jsonl(args.base):
        cid = str(o.get("id") or o.get("conversation_hash"))
        base[cid] = o

    # comparison runs, each a labelled column
    runs = []
    for spec in args.runs:
        label, _, path = spec.partition("=")
        if not path:
            path, label = label, Path(label).stem
        runs.append(
            (label, {str(r.get("id")): run_verdict(r) for r in load_jsonl(path)})
        )

    rows = []
    for cid, o in base.items():
        jcats = o.get("judge_categories") or []
        cols = []
        if args.judge:
            cols.append(
                {
                    "label": "judge",
                    "kind": "judge",
                    "present": True,
                    "violation": True,
                    "categories": jcats,
                    "reason": o.get("judge_reason") or "",
                    "reasoning": "",
                }
            )
        comparison = []
        for label, by_id in runs:
            v = by_id.get(cid)
            col = {
                "label": label,
                "kind": "run",
                "present": v is not None,
                **(
                    v
                    or {
                        "violation": None,
                        "categories": [],
                        "reason": "",
                        "reasoning": "",
                    }
                ),
            }
            cols.append(col)
            comparison.append(col)
        category = (
            jcats or (comparison[0]["categories"] if comparison else []) or ["(none)"]
        )[0]
        rows.append(
            {
                "id": cid,
                "turns": len(o.get("conversation") or []),
                "category": category,
                "ruling": ruling(comparison),
                "columns": cols,
                "conversation": [
                    {"role": m.get("role") or "?", "content": cap(m.get("content"))}
                    for m in (o.get("conversation") or [])
                ],
            }
        )

    rows.sort(key=lambda r: (r["category"], r["id"]))

    data_json = (
        json.dumps(rows)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    html = (
        TEMPLATE.read_text()
        .replace("__TITLE__", "Monitor viewer")
        .replace("__DATA__", data_json)
    )
    Path(args.out).write_text(html)
    cols_desc = ("judge + " if args.judge else "") + " + ".join(l for l, _ in runs)
    print(
        f"Wrote {args.out} — {len(rows)} conversations, columns: {cols_desc}, {len(html)/1e6:.1f} MB"
    )


if __name__ == "__main__":
    main()
