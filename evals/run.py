#!/usr/bin/env python3
"""Rate-limited async benchmark runner — resumable. The single runner.

Dispatches one new request every RATE_S seconds and collects responses
asynchronously — so many requests can be in flight at once, but the launch rate
stays fixed and gentle on prod. Each response is judged inline with the judge
prompts in standard_judges.py (HarmBench routes contextual behaviors to the contextual
classifier) and appended to a JSONL as it completes — so a crash loses only
in-flight items, and re-running the same command resumes from where it stopped
(already-recorded refs are skipped).

Questions come from data/items.json (1,200 AILuminate + 300 HarmBench). Target
defaults to DeepSeek-V4-Flash on prod with reasoning on; the chain-of-thought
(reasoning_content) is captured alongside the answer. Output is
data/rate_<MODEL>.jsonl — the shape viewer.py reads.

Env:
  N          number of questions; >= dataset size (or 0) = the full set (default 10)
  RATE_S     seconds between dispatches (default 10)
  MODEL      served model name (default deepseek-v4-flash)
  BASE_URL   OpenAI-compatible base (default https://inference.tinfoil.sh/v1)
  MAX_TOKENS output budget incl. reasoning (default 16384)
  REASONING  reasoning effort: low|medium|high|"" to disable (default high)
  TIMEOUT    per-request timeout seconds (default 300)
  SEED       sampling seed for partial runs (default 42; full set ignores it)
  OUT_NAME   data/<OUT_NAME> jsonl (default rate_<MODEL>.jsonl)

  # full resumable run:
  N=1500 RATE_S=10 python run.py
"""

import asyncio
import json
import os
import random
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI

HERE = Path(__file__).resolve().parent
load_dotenv(HERE.parent / ".env")

sys.path.insert(0, str(HERE))
from standard_judges import (  # noqa: E402  # type: ignore[import-not-found]
    COMPLIANCE_JUDGE,
    _yes,
    harmbench_judge_messages,
)

N = int(os.environ.get("N", "10"))
RATE_S = float(os.environ.get("RATE_S", "10"))
MODEL = os.environ.get("MODEL", "deepseek-v4-flash")
BASE_URL = os.environ.get("BASE_URL", "https://inference.tinfoil.sh/v1")
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "16384"))
REASONING = os.environ.get("REASONING", "high")
TIMEOUT = float(os.environ.get("TIMEOUT", "300"))
SEED = int(os.environ.get("SEED", "42"))
JUDGE_MODEL = os.environ.get("SG_JUDGE_MODEL", "gpt-4.1-mini").replace("openai/", "")

ITEMS = HERE / "data" / "items.json"
OUT = HERE / "data" / os.environ.get("OUT_NAME", f"rate_{MODEL}.jsonl")


def _reasoning_of(msg):
    """DeepSeek-V4 (reasoning-parser deepseek_v4) returns the chain-of-thought in
    a non-standard `reasoning_content` field — grab it wherever the SDK stashes it."""
    r = getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None)
    if r is None and getattr(msg, "model_extra", None):
        r = msg.model_extra.get("reasoning_content") or msg.model_extra.get("reasoning")
    return r or ""


async def judge_response(jclient, sem, item, response):
    """Judge one response with the matching prompt. Returns (asr, verdict).
    AILuminate -> COMPLIANCE_JUDGE; HarmBench -> standard/contextual classifier."""
    async with sem:
        if item["source"] == "harmbench":
            bid = item.get("id") or (
                item["ref"][2:] if item["ref"].startswith("H:") else item["ref"]
            )
            sys_msg, user_msg = harmbench_judge_messages(bid, response)
            msgs = [
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": user_msg},
            ]
        else:
            msgs = [
                {
                    "role": "user",
                    "content": COMPLIANCE_JUDGE.format(
                        request=item["prompt"], response=response
                    ),
                }
            ]
        out = await jclient.chat.completions.create(
            model=JUDGE_MODEL, messages=msgs, temperature=0, max_tokens=16, timeout=60
        )
        v = (out.choices[0].message.content or "").strip()
        return (1 if _yes(v) else 0), v


async def process(client, jclient, jsem, item, t0, out_fh, wlock):
    """Target call -> judge -> append complete record to the JSONL (under lock)."""
    disp = round(time.monotonic() - t0, 2)
    rec = {
        "ref": item["ref"],
        "source": item["source"],
        "category": item["category"],
        "prompt": item["prompt"],
        "dispatch_s": disp,
    }
    extra = {"reasoning_effort": REASONING} if REASONING else {}
    try:
        r = await client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": item["prompt"]}],
            max_tokens=MAX_TOKENS,
            temperature=0,
            timeout=TIMEOUT,
            extra_body=extra,
        )
        msg = r.choices[0].message
        rec["response"] = msg.content or ""
        rec["reasoning"] = _reasoning_of(msg)
        rec["ok"] = True
    except Exception as e:
        rec["response"] = f"__ERROR__ {type(e).__name__}: {e}"
        rec["reasoning"] = ""
        rec["ok"] = False
    rec["finish_s"] = round(time.monotonic() - t0, 2)
    rec["latency_s"] = round(rec["finish_s"] - rec["dispatch_s"], 2)

    if rec["ok"]:
        try:
            rec["asr"], rec["verdict"] = await judge_response(
                jclient, jsem, item, rec["response"]
            )
        except Exception as e:
            rec["asr"], rec["verdict"] = (
                None,
                f"__JUDGE_ERROR__ {type(e).__name__}: {e}",
            )
    else:
        rec["asr"], rec["verdict"] = None, "(target error — not judged)"

    async with wlock:
        out_fh.write(json.dumps(rec) + "\n")
        out_fh.flush()
    st = "ok " if rec["ok"] else "ERR"
    asr = "-" if rec["asr"] is None else rec["asr"]
    print(
        f"  done @ {rec['finish_s']:6.1f}s {st} asr={asr} {rec['source'][:4]}/{rec['category'][:12]:12s}"
        f" disp@{rec['dispatch_s']:5.1f}s lat {rec['latency_s']:5.1f}s ans {len(rec['response'])}c"
        f" cot {len(rec['reasoning'])}c",
        flush=True,
    )
    return rec


def _asr(rows):
    scored = [r for r in rows if r.get("asr") is not None]
    return sum(r["asr"] for r in scored), len(scored)


async def main():
    items = json.loads(ITEMS.read_text())
    if N <= 0 or N >= len(items):
        selection = items  # full set, dataset order (deterministic resume)
    else:
        selection = random.Random(SEED).sample(items, N)

    # resume: skip refs already fully recorded in OUT
    done = {}
    if OUT.exists():
        for line in OUT.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                done[r["ref"]] = r
    todo = [i for i in selection if i["ref"] not in done]

    print(
        f"model={MODEL} @ {BASE_URL}\n"
        f"dispatch: 1 req / {RATE_S}s (async), reasoning={REASONING or 'off'}, max_tokens={MAX_TOKENS}\n"
        f"target set: {len(selection)} | already done: {len(done)} | to run: {len(todo)}\n"
        f"out (resumable jsonl): {OUT}\n"
    )
    if not todo:
        print("nothing to do — set already complete.")
    else:
        client = AsyncOpenAI(base_url=BASE_URL, api_key=os.environ["TINFOIL_API_KEY"])
        jclient = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
        jsem = asyncio.Semaphore(16)
        wlock = asyncio.Lock()
        OUT.parent.mkdir(parents=True, exist_ok=True)
        out_fh = open(OUT, "a")
        t0 = time.monotonic()
        tasks = []
        for i, item in enumerate(todo):
            if i > 0:
                await asyncio.sleep(RATE_S)  # fixed dispatch cadence
            print(
                f"dispatch @ {time.monotonic() - t0:6.1f}s [{i + 1}/{len(todo)}] "
                f"{item['ref']} ({item['source']}/{item['category']})",
                flush=True,
            )
            tasks.append(
                asyncio.create_task(
                    process(client, jclient, jsem, item, t0, out_fh, wlock)
                )
            )
        await asyncio.gather(*tasks)
        out_fh.close()
        print(f"\nwall this run: {time.monotonic() - t0:.1f}s")

    # ASR over the FULL recorded set (resume-safe: re-read OUT)
    allrecs = [json.loads(l) for l in OUT.read_text().splitlines() if l.strip()]
    ail = _asr([r for r in allrecs if r["source"] == "ailuminate"])
    hb = _asr([r for r in allrecs if r["source"] == "harmbench"])
    errs = sum(1 for r in allrecs if not r.get("ok"))

    def pct(f, n):
        return f"{f}/{n} = {100 * f / n:.1f}%" if n else "n/a"

    print(
        f"\n=== ASR ({MODEL}, judged by {JUDGE_MODEL}) — {len(allrecs)} recorded ===\n"
        f"  overall     {pct(*_asr(allrecs))}\n"
        f"  AILuminate  {pct(*ail)}\n"
        f"  HarmBench   {pct(*hb)}\n"
        f"  target errors: {errs}\n"
        f"-> {OUT}"
    )


if __name__ == "__main__":
    asyncio.run(main())
