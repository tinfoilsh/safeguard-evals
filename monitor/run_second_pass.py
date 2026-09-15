#!/usr/bin/env python3
"""Sanity reviewer — a second model second-guesses the hard-no judge's flags.

Given each conversation the monitor flagged, a reviewer model (kimi-k3 via the
Tinfoil API by default) is TOLD what the judge concluded (categories + reason),
then given the exact policy, and asked to independently validate — returning what, if
anything, the conversation actually violates. Comparing the reviewer's verdict to
the judge's surfaces likely false positives.

Input is the flagged eval set from build_flagged.py (conversation text + judge
verdict). Async + bounded worker pool with a per-minute dispatch cap; resumable
(verdicts appended as they land, re-running skips ids already recorded).

Env:
  FLAGGED       flagged jsonl from build_flagged.py (default results/flagged.jsonl)
  BASE_URL      endpoint (default https://inference.tinfoil.sh/v1)
  MODEL         served model name (default kimi-k3)
  CONCURRENCY   max in-flight requests (default 8)
  MAX_TOKENS    output budget incl. reasoning (default 16000 — the reviewer reasons inline)
  TIMEOUT       per-request seconds (default 180)
  RATE_S        min seconds between dispatches (default 10 => 6/min, the Tinfoil API cap; 0=off)
  LIMIT         cap number of conversations (default 0 = all)
  OUT_NAME      results/<OUT_NAME> jsonl (default second_pass_<MODEL>.jsonl)

  python run_second_pass.py
"""

import asyncio
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from hard_no import HARD_NO_POLICY, REVIEW_PREAMBLE
from openai import AsyncOpenAI
from run_monitor import _reasoning_of, format_conversation, parse_verdict

HERE = Path(__file__).resolve().parent
load_dotenv(HERE.parent / ".env")  # repo-root .env (TINFOIL_API_KEY)

FLAGGED = os.environ.get("FLAGGED", str(HERE / "results" / "flagged.jsonl"))
BASE_URL = os.environ.get("BASE_URL", "https://inference.tinfoil.sh/v1")
MODEL = os.environ.get("MODEL", "kimi-k3")
CONCURRENCY = int(os.environ.get("CONCURRENCY", "8"))
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "16000"))
TIMEOUT = float(os.environ.get("TIMEOUT", "180"))
RATE_S = float(os.environ.get("RATE_S", "10"))  # 10 => 6/min (Tinfoil API cap); 0=off
LIMIT = int(os.environ.get("LIMIT", "0"))
OUT = HERE / "results" / os.environ.get("OUT_NAME", f"second_pass_{MODEL}.jsonl")


def build_messages(conv, judge_reason, judge_categories):
    cats = ", ".join(judge_categories) if judge_categories else "(none listed)"
    system = (
        REVIEW_PREAMBLE.format(categories=cats, reason=judge_reason or "(none given)")
        + "\n\n"
        + HARD_NO_POLICY
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "CONVERSATION:\n\n" + format_conversation(conv)},
    ]


def iter_flagged(path):
    for i, line in enumerate(Path(path).read_text().splitlines()):
        if not line.strip():
            continue
        o = json.loads(line)
        yield (
            str(o.get("id") or o.get("conversation_hash") or f"row{i}"),
            o.get("conversation") or [],
            o.get("judge_reason") or "",
            o.get("judge_categories") or [],
        )


async def review(client, item, out_fh, wlock):
    cid, conv, jr, jc = item
    rec = {
        "id": cid,
        "turns": len(conv or []),
        "judge_reason": jr,
        "judge_categories": jc,
    }
    t = time.monotonic()
    try:
        r = await client.chat.completions.create(
            model=MODEL,
            messages=build_messages(conv, jr, jc),
            max_tokens=MAX_TOKENS,
            temperature=0,
            timeout=TIMEOUT,
        )
        msg = r.choices[0].message
        content = msg.content or ""
        rec.update(parse_verdict(content))
        rec["raw"] = content
        rec["reasoning"] = _reasoning_of(msg)
        rec["ok"] = bool(rec.get("parsed"))  # unparseable reply = not a usable verdict
    except Exception as e:
        rec.update(
            {
                "violation": None,
                "categories": [],
                "reason": "",
                "raw": f"__ERROR__ {type(e).__name__}: {e}",
                "reasoning": "",
                "ok": False,
            }
        )
    rec["latency_s"] = round(time.monotonic() - t, 2)
    async with wlock:
        out_fh.write(json.dumps(rec) + "\n")
        out_fh.flush()
    return rec


async def main():
    if not Path(FLAGGED).exists():
        raise SystemExit(
            f"flagged set not found: {FLAGGED} (run build_flagged.py first)"
        )

    done = set()
    if OUT.exists():
        with open(OUT) as f:
            for line in f:
                line = line.strip()
                if line:
                    r = json.loads(line)
                    if r.get("violation") is not None:  # retry errored/incomplete rows
                        done.add(r["id"])

    print(
        f"second-pass reviewer  model={MODEL} @ {BASE_URL}\n"
        f"flagged set: {FLAGGED} | already recorded: {len(done)} | concurrency={CONCURRENCY} rate_s={RATE_S}\n"
        f"out (resumable): {OUT}\n",
        flush=True,
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    client = AsyncOpenAI(
        base_url=BASE_URL, api_key=os.environ.get("TINFOIL_API_KEY", "none")
    )
    wlock = asyncio.Lock()
    queue: asyncio.Queue = asyncio.Queue(maxsize=CONCURRENCY * 2)
    out_fh = open(OUT, "a")
    ctr = {"done": 0, "upheld": 0, "err": 0}
    t0 = time.monotonic()

    async def worker():
        while True:
            item = await queue.get()
            try:
                if item is None:
                    return
                rec = await review(client, item, out_fh, wlock)
                ctr["done"] += 1
                if rec.get("violation"):
                    ctr["upheld"] += 1
                if not rec.get("ok"):
                    ctr["err"] += 1
                if ctr["done"] % 50 == 0:
                    el = time.monotonic() - t0
                    print(
                        f"  {ctr['done']} done ({ctr['upheld']} upheld, {ctr['err']} err) "
                        f"{ctr['done'] / el:.1f}/s",
                        flush=True,
                    )
            finally:
                queue.task_done()

    workers = [asyncio.create_task(worker()) for _ in range(CONCURRENCY)]

    seen = 0
    for item in iter_flagged(FLAGGED):
        if LIMIT and seen >= LIMIT:
            break
        seen += 1
        if item[0] in done:
            continue
        await queue.put(item)
        if RATE_S:
            await asyncio.sleep(RATE_S)  # fixed dispatch cadence (per-minute rate cap)
    for _ in workers:
        await queue.put(None)

    await asyncio.gather(*workers)
    out_fh.close()
    print(
        f"reviewed {ctr['done']} in {time.monotonic() - t0:.1f}s "
        f"({ctr['upheld']} upheld, {ctr['err']} err) -> {OUT}",
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
