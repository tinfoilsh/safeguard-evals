#!/usr/bin/env python3
"""Self-contained HTML viewer for run.py JSONL outputs. The main results viewer.

Each row: {ref, source, category, prompt, response, reasoning, asr, verdict, ...}.
Single-card J/K browse, benchmark + COMPLIED/refused badges, category filter, and
the reasoning trace in a <details> collapsed by default (click to expand).

Also tags each prompt against the hard-no set (joined by ref): a red HARD-NO
badge (with its uplift/agreeableness/sexmc tags) for the 297 hard-no questions.

Pass several rate files (or a directory) to embed them all under a model
dropdown — switching models keeps the current filters and just swaps the data.

The markup/CSS/JS live in template.html; this script just injects the data
(__DATA__) and title (__TITLE__) and writes a self-contained page.

Usage:
  python viewer.py <rate_model.jsonl> [more.jsonl ...] [output.html]
  python viewer.py data/ viewer.html          # all rate_*.jsonl in a dir
"""

import json
import sys
from pathlib import Path

# hardno.json: the refs kept as hard-no, tagged uplift/agreeableness/sexmc.
HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
HARDNO = DATA / "hardno.json"
TEMPLATE = HERE / "template.html"

# per-axis "specific judge" results (gpt-4.1-mini) for the kept hard-no set, per
# model. {ref: {tags, verdicts: {axis: {fail, reason}}, fail, old_judge_complied}}.
# The generic asr judge only asks "did it answer"; the specific judge asks whether
# it crossed the specific hard-no line (uplift/agreeableness/sexmc).
JUDGE_FILES = {
    "gemma4-31b": "judge_results_gpt-4.1-mini.json",
    "gpt-oss": "judge_results_gpt-4.1-mini_gpt-oss.json",
    "llama3-3-70b": "judge_results_gpt-4.1-mini_llama.json",
    "deepseek-v4-flash": "judge_results_gpt-4.1-mini_deepseek-v4-flash.json",
    "deepseek-v4-1-flash": "judge_results_gpt-4.1-mini_deepseek-v4-1-flash.json",
    "kimi-k3": "judge_results_gpt-4.1-mini_kimi-k3.json",
    "glm-5-3": "judge_results_gpt-4.1-mini_glm-5-3.json",
    "glm-5-3-flash": "judge_results_gpt-4.1-mini_glm-5-3-flash.json",
}


def load_judge_results(model):
    fn = JUDGE_FILES.get(model)
    if not fn or not (DATA / fn).exists():
        return {}
    return json.loads((DATA / fn).read_text())


def load_hardno():
    if not HARDNO.exists():
        return {}
    return {
        ref: {"hardno": True, "hardno_tags": tags}
        for ref, tags in json.loads(HARDNO.read_text()).items()
    }


def load(jsonl_path, model=""):
    rows = [
        json.loads(x) for x in Path(jsonl_path).read_text().splitlines() if x.strip()
    ]
    hardno = load_hardno()
    judges = load_judge_results(model)
    out = []
    for r in rows:
        asr = r.get("asr")
        ref = r.get("ref", "")
        t = hardno.get(ref, {})
        jr = judges.get(ref) or {}
        specific = [
            {"axis": axis, "fail": bool(v.get("fail")), "reason": v.get("reason", "")}
            for axis, v in (jr.get("verdicts") or {}).items()
        ]
        out.append(
            {
                "ref": ref,
                "source": "HarmBench"
                if r.get("source") == "harmbench"
                else "AILuminate",
                "category": r.get("category", ""),
                "prompt": r.get("prompt", ""),
                "response": r.get("response", ""),
                "reasoning": r.get("reasoning", ""),
                "verdict": r.get("verdict", ""),
                "status": "error"
                if asr is None
                else ("COMPLIED" if asr == 1 else "refused"),
                "complied": asr == 1,
                "hardno": bool(t.get("hardno")),
                "hardno_tags": t.get("hardno_tags", []),
                "specific_judges": specific,
                "specific_fail": (bool(jr.get("fail")) if jr else None),
            }
        )
    # category-contiguous (so the category chips can jump); complied first within
    out.sort(key=lambda r: (r["source"], r["category"], not r["complied"], r["ref"]))
    return out


def main():
    args = sys.argv[1:]
    if not args:
        print("Usage: viewer.py <rate_*.jsonl | dir> ... [output.html]")
        sys.exit(1)
    out_path = None
    if args[-1].endswith(".html"):
        out_path, args = args[-1], args[:-1]
    paths = []
    for a in args:
        p = Path(a)
        if p.is_dir():
            paths += sorted(p.glob("rate_*.jsonl"))
        elif p.suffix == ".jsonl":
            paths.append(p)
    if not paths:
        print("no rate_*.jsonl found")
        sys.exit(1)
    models = {}
    for p in sorted(paths):
        name = p.stem.replace("rate_", "")
        models[name] = load(p, name)
    title = (
        f"response viewer ({len(models)} model"
        + ("" if len(models) == 1 else "s")
        + ")"
    )

    data_json = (
        json.dumps(models)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    html = (
        TEMPLATE.read_text().replace("__TITLE__", title).replace("__DATA__", data_json)
    )
    if out_path:
        Path(out_path).write_text(html)
        print(f"Wrote {out_path} ({len(models)} models: {', '.join(models)})")
    else:
        print(html)


if __name__ == "__main__":
    main()
