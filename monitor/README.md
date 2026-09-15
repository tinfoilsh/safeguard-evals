# monitor — runtime hard-no monitor

`run_monitor.py` is an async, resumable classifier. Given a full conversation, it
asks a model whether the **assistant** crossed one of the hard-no lines (the
5 categories in `HARD_NO_POLICY`).

You can swap in any model; we tried both gpt-oss and gpt-oss-safeguard.

```bash
CONVERSATIONS=convs.parquet TARGET=oss       BASE_URL=http://localhost:8000/v1 python run_monitor.py
CONVERSATIONS=convs.parquet TARGET=safeguard BASE_URL=http://localhost:8000/v1 python run_monitor.py
```

## Input format

A conversation is a list of `{"role", "content"}` messages. The monitor accepts:

- **`.jsonl`** — one conversation per line, either the raw message list or an
  object `{"id", "conversation": [...]}` (`id` optional; falls back to `conversation_hash`, else the row number).
- **`.parquet`** — WildChat-shaped: a `conversation` column of message lists, plus an
  optional `conversation_hash` id column. Override the column names with `CONV_COL` / `ID_COL`.

Only `role` and `content` are read, so extra message fields are ignored.

## Try it

A 3-conversation `sample.jsonl` is included so you can run end-to-end without any data:

```bash
CONVERSATIONS=sample.jsonl BASE_URL=http://localhost:8000/v1 python run_monitor.py
```

## Bring your own WildChat

WildChat Full is gated, but an ungated set without the toxic data is available at <https://huggingface.co/datasets/allenai/WildChat-4.8M>.

```python
from datasets import load_dataset

ds = load_dataset("allenai/WildChat-4.8M", split="train")
ds.select_columns(["conversation_hash", "conversation"]).to_parquet("wildchat.parquet")
```

Then `CONVERSATIONS=wildchat.parquet python run_monitor.py`.

## Second opinion

Empirically, oss-safeguard can get confused. We solve this by escalating to a larger model like Kimi-K3. We've found that priming Kimi with the judge response and asking it to re-examine works better than naively re-judging.

```bash
python build_flagged.py --conversations wildchat.parquet --monitor results/monitor_safeguard.jsonl
python run_second_pass.py       # -> results/second_pass_kimi-k3.jsonl
```

## Viewing / comparing runs

`viewer.py` builds a self-contained HTML page (markup in `template.html`) that shows one
conversation per page with each run's verdict as a column, over the full conversation.
Filter by ruling (both yes / split / both no), jump by category with Shift+J/K.

Each run is a positional `name=path` — the name is the column label, so use whatever you
like (`a=`, `b=`, `safeguard=`, …). `--base` supplies the conversation text (default
`results/flagged.jsonl`).

```bash
# compare two runs (two columns)
python viewer.py a=results/monitor_a.jsonl b=results/monitor_b.jsonl

# --judge also prepends the base file's original judge verdict (three columns)
python viewer.py --judge a=results/second_pass_kimi-k3.jsonl b=results/monitor_kimi.jsonl
```

`--out` sets the output path (default `viewer.html`). Any number of run columns works; the
ruling is `both yes` when all agree on a violation, `both no` when all agree it's clean.

## Where the conversations come from

The monitor is tuned on real WildChat conversations. Raw WildChat has a lot of bot conversations, so in [`data_curation/`](data_curation/)
we remove these.
