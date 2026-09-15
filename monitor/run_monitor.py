#!/usr/bin/env python3
"""Hard-no-violation monitor over conversations.

Runs a set of conversations through an OpenAI-compatible model (e.g. gpt-oss or
gpt-oss-safeguard) and asks, given the FULL conversation, whether it violates our
Acceptable Use Policy. Async + heavily batched. Resumable: each verdict is
appended to a JSONL as it lands, and re-running skips conversations already
recorded.

Configurable so the same script runs against either model — pick with TARGET, or
override BASE_URL/MODEL directly.

Env:
  CONVERSATIONS  path to a .parquet (WildChat-shaped) or .jsonl of conversations (required)
  TARGET         oss | safeguard | kimi  (preset MODEL name; default oss)
  BASE_URL       OpenAI-compatible endpoint (default localhost:8000/v1; kimi -> Tinfoil API)
  MODEL          override the served model name (default from TARGET)
  CONCURRENCY    max in-flight requests — batch hard (default 64)
  MAX_TOKENS     output budget incl. reasoning (default 4096)
  LIMIT          cap number of conversations (default 0 = all)
  TIMEOUT        per-request seconds (default 180)
  RATE_S         min seconds between dispatches, caps req/min (default 0=off; kimi -> 10 = 6/min)
  CONV_COL       parquet column holding the conversation list (default conversation)
  ID_COL         parquet column for a stable id (default conversation_hash)
  OUT_NAME       results/<OUT_NAME> jsonl (default monitor_<TARGET>.jsonl)

  CONVERSATIONS=convs.parquet TARGET=oss       BASE_URL=http://localhost:8000/v1 python run_monitor.py
  CONVERSATIONS=convs.parquet TARGET=safeguard BASE_URL=http://localhost:8000/v1 python run_monitor.py
"""

import asyncio
import json
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv
from hard_no import HARD_NO_CATEGORIES, HARD_NO_POLICY
from openai import AsyncOpenAI

HERE = Path(__file__).resolve().parent
load_dotenv(HERE.parent / ".env")  # repo-root .env (optional)

# TARGET selects the served model name; point BASE_URL at your own
# OpenAI-compatible endpoint serving it (e.g. a local vLLM on :8000). kimi-k3 runs
# through the Tinfoil API (needs TINFOIL_API_KEY) and defaults its endpoint + a
# per-minute dispatch cap; override either with BASE_URL / RATE_S.
TARGETS = {
    "oss": "gpt-oss-120b",
    "safeguard": "gpt-oss-safeguard-120b",
    "kimi": "kimi-k3",
}
TARGET_BASE = {"kimi": "https://inference.tinfoil.sh/v1"}
TARGET_RATE_S = {"kimi": 10.0}  # 6 req/min — the Tinfoil API cap

TARGET = os.environ.get("TARGET", "oss")
BASE_URL = os.environ.get(
    "BASE_URL", TARGET_BASE.get(TARGET, "http://localhost:8000/v1")
)
MODEL = os.environ.get("MODEL", TARGETS.get(TARGET, TARGETS["oss"]))
CONCURRENCY = int(os.environ.get("CONCURRENCY", "64"))
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "4096"))
LIMIT = int(os.environ.get("LIMIT", "0"))
TIMEOUT = float(os.environ.get("TIMEOUT", "180"))
# min seconds between dispatches; a fixed launch cadence that caps requests/min
# regardless of concurrency (0 = off). Defaults per target (kimi -> 6/min).
RATE_S = float(os.environ.get("RATE_S", TARGET_RATE_S.get(TARGET, 0.0)))
CONV_COL = os.environ.get("CONV_COL", "conversation")
ID_COL = os.environ.get("ID_COL", "conversation_hash")
CONVERSATIONS = os.environ.get("CONVERSATIONS", "")
OUT = HERE / "results" / os.environ.get("OUT_NAME", f"monitor_{TARGET}.jsonl")


# ---------------------------------------------------------------------------
# The monitor prompt. THIS IS THE PART WE ITERATE ON.
# ---------------------------------------------------------------------------
def format_conversation(conv):
    """Render a conversation (list of {role, content}) as plain text."""
    parts = []
    for m in conv or []:
        role = (m.get("role") or "").strip().capitalize() or "?"
        content = m.get("content")
        parts.append(f"{role}: {'' if content is None else content}")
    return "\n\n".join(parts)


def build_messages(conv):
    return [
        {"role": "system", "content": HARD_NO_POLICY},
        {"role": "user", "content": "CONVERSATION:\n\n" + format_conversation(conv)},
    ]


def parse_verdict(text):
    """Pull {violation, categories, reason} out of the model's reply, leniently."""
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if m:
        try:
            d = json.loads(m.group(0))
            cats = [c for c in (d.get("categories") or []) if c in HARD_NO_CATEGORIES]
            return {
                "violation": bool(d.get("violation")),
                "categories": cats,
                "reason": str(d.get("reason", "")),
                "parsed": True,
            }
        except Exception:
            pass
    # No parseable JSON (usually a truncated reply).  Mark it unknown so it is
    # excluded and retried, not counted.
    return {"violation": None, "categories": [], "reason": "", "parsed": False}


def _reasoning_of(msg):
    r = getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None)
    if r is None and getattr(msg, "model_extra", None):
        r = msg.model_extra.get("reasoning_content") or msg.model_extra.get("reasoning")
    return r or ""


def load_conversations(path):
    """Return [(id, conversation_list)] from a WildChat-shaped parquet or a jsonl.
    jsonl lines may be a raw list (the conversation) or an object with CONV_COL."""
    p = Path(path)
    rows = []
    if p.suffix == ".parquet":
        import pyarrow.parquet as pq

        cols = [CONV_COL] + ([ID_COL] if ID_COL else [])
        f = pq.ParquetFile(str(p))
        names = f.schema_arrow.names
        cols = [c for c in cols if c in names]
        for i, rec in enumerate(_iter_parquet(f, cols)):
            cid = rec.get(ID_COL) or f"row{i}"
            rows.append((str(cid), rec.get(CONV_COL)))
    else:  # jsonl / json
        text = p.read_text()
        lines = (
            json.loads(text)
            if p.suffix == ".json"
            else [json.loads(x) for x in text.splitlines() if x.strip()]
        )
        for i, obj in enumerate(lines):
            if isinstance(obj, list):
                rows.append((f"row{i}", obj))
            else:
                cid = obj.get(ID_COL) or obj.get("id") or f"row{i}"
                rows.append((str(cid), obj.get(CONV_COL) or obj.get("conversation")))
    if LIMIT:
        rows = rows[:LIMIT]
    return rows


def _iter_parquet(f, cols):
    for b in f.iter_batches(columns=cols, batch_size=2000):
        yield from b.to_pylist()


def iter_convs(path):
    """Stream (id, conversation) one row at a time so we never hold the whole
    dataset in memory — required for large parquets on a small box."""
    p = Path(path)
    if p.suffix == ".parquet":
        import pyarrow.parquet as pq

        f = pq.ParquetFile(str(p))
        names = f.schema_arrow.names
        cols = [c for c in ([CONV_COL] + ([ID_COL] if ID_COL else [])) if c in names]
        i = 0
        for b in f.iter_batches(columns=cols, batch_size=1000):
            for rec in b.to_pylist():
                cid = rec.get(ID_COL) or f"row{i}"
                yield str(cid), rec.get(CONV_COL)
                i += 1
    else:
        text = p.read_text()
        lines = (
            json.loads(text)
            if p.suffix == ".json"
            else [json.loads(x) for x in text.splitlines() if x.strip()]
        )
        for i, obj in enumerate(lines):
            if isinstance(obj, list):
                yield f"row{i}", obj
            else:
                cid = obj.get(ID_COL) or obj.get("id") or f"row{i}"
                yield str(cid), obj.get(CONV_COL) or obj.get("conversation")


async def classify(client, cid, conv, out_fh, wlock):
    rec = {"id": cid, "turns": len(conv or [])}
    t = time.monotonic()
    try:
        r = await client.chat.completions.create(
            model=MODEL,
            messages=build_messages(conv),
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
    if not CONVERSATIONS:
        raise SystemExit("set CONVERSATIONS=<path to .parquet or .jsonl>")

    # resume set: stream the output, hold only ids
    done = set()
    if OUT.exists():
        with open(OUT) as f:
            for line in f:
                line = line.strip()
                if line:
                    r = json.loads(line)
                    if r.get("violation") is not None:  # retry incompletes/errors
                        done.add(r["id"])

    print(
        f"target={TARGET}  model={MODEL} @ {BASE_URL}\n"
        f"already recorded: {len(done)} | concurrency={CONCURRENCY} max_tokens={MAX_TOKENS}\n"
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
    ctr = {"done": 0, "flagged": 0, "err": 0}
    t0 = time.monotonic()

    async def worker():
        while True:
            item = await queue.get()
            try:
                if item is None:
                    return
                cid, conv = item
                rec = await classify(client, cid, conv, out_fh, wlock)
                ctr["done"] += 1
                if rec.get("violation"):
                    ctr["flagged"] += 1
                if not rec.get("ok"):
                    ctr["err"] += 1
                if ctr["done"] % 500 == 0:
                    el = time.monotonic() - t0
                    print(
                        f"  {ctr['done']} done ({ctr['flagged']} flagged, {ctr['err']} err) "
                        f"{ctr['done'] / el:.1f}/s",
                        flush=True,
                    )
            finally:
                queue.task_done()

    workers = [asyncio.create_task(worker()) for _ in range(CONCURRENCY)]

    # producer: stream rows, skip already-done, feed the bounded queue (backpressure)
    loop = asyncio.get_running_loop()
    it = iter_convs(CONVERSATIONS)
    seen = 0
    while True:
        item = await loop.run_in_executor(None, next, it, None)
        if item is None:
            break
        if LIMIT and seen >= LIMIT:
            break
        seen += 1
        cid, conv = item
        if cid in done:
            continue
        await queue.put((cid, conv))
        if RATE_S:
            await asyncio.sleep(RATE_S)  # fixed dispatch cadence (per-minute rate cap)
    for _ in workers:
        await queue.put(None)

    await asyncio.gather(*workers)
    out_fh.close()
    print(
        f"ran {ctr['done']} in {time.monotonic() - t0:.1f}s "
        f"({ctr['flagged']} flagged, {ctr['err']} err) -> {OUT}",
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
