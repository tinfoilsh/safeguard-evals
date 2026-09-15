#!/usr/bin/env python3
"""Render data/regroup_candidates.json (bot families re-united by shared interior content)
into a self-contained review page at output/regroup.html. Each card shows the total count,
how many prefix-clusters were merged, single-turn share, the shared signature shingle, an
example, and the flexible regex long_generate produced. N = env TOP (default 30).

    python view_regroup.py
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
INFILE = os.path.join(HERE, "data", "regroup_candidates.json")
OUTFILE = os.path.join(HERE, "output", "regroup.html")
TOP = int(os.environ.get("TOP", "30"))


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def main():
    fams = json.load(open(INFILE))[:TOP]
    cards = []
    for i, r in enumerate(fams, 1):
        pat = r.get("pattern") or "(not induced — below GEN_CAP)"
        st = r.get("single_turn_pct", 0)
        stcls = "st-hi" if st >= 0.99 else "st-mid" if st >= 0.9 else "st-lo"
        cards.append(f"""
<div class=card>
  <div class=hd>
    <span class=rank>#{i}</span>
    <span class=count>{r['count']:,}</span>
    <span class=merge>{r['n_clusters']} clusters merged</span>
    <span class="st {stcls}">{st:.0%} single-turn</span>
    <span class=sig>{esc(r['signature'][:90])}</span>
  </div>
  <div class=lbl>example</div><div class=ex>{esc(r['example'])}</div>
  <div class=lbl>induced regex (long_generate)</div><div class=rx>{esc(pat)}</div>
</div>""")
    doc = f"""<!doctype html><meta charset=utf-8><title>Regrouped bot families</title>
<style>
 body{{background:#0d1117;color:#c9d1d9;font:14px/1.55 -apple-system,sans-serif;margin:0;padding:22px 26px}}
 h1{{font-size:16px;color:#e6edf3;margin:0 0 4px}} p.sub{{color:#8b949e;margin:0 0 18px}}
 .card{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px 16px;margin-bottom:14px}}
 .hd{{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:8px}}
 .rank{{color:#6e7681;font-weight:700}} .count{{font-size:20px;font-weight:700;color:#ff7b72}}
 .merge{{font-size:12px;color:#d2a8ff;background:#1c1626;border:1px solid #3a2b52;border-radius:6px;padding:2px 8px}}
 .st{{font-size:12px;border-radius:6px;padding:2px 8px;border:1px solid #30363d}}
 .st-hi{{color:#7ee787;background:#0f2417}} .st-mid{{color:#e3b341;background:#241c0f}} .st-lo{{color:#ff9b9b;background:#2d1517}}
 .sig{{font-family:ui-monospace,Menlo,monospace;font-size:12px;color:#7ee787;margin-left:auto;max-width:55%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
 .lbl{{font-size:10.5px;text-transform:uppercase;letter-spacing:.5px;color:#6e7681;margin:10px 0 4px}}
 .ex{{font-family:ui-monospace,Menlo,monospace;font-size:12.5px;color:#d7dde3;background:#0d1117;border:1px solid #21262d;border-radius:6px;padding:8px 10px;white-space:pre-wrap;word-break:break-word;max-height:170px;overflow:auto}}
 .rx{{font-family:ui-monospace,Menlo,monospace;font-size:12px;color:#e6d9ff;background:#1c1626;border:1px solid #3a2b52;border-radius:6px;padding:8px 10px;white-space:pre-wrap;word-break:break-word;max-height:150px;overflow:auto}}
</style>
<h1>Regrouped bot families — top {len(fams)}</h1>
<p class=sub>Prefix-clusters re-united by shared interior shingle. Count = total occurrences;
"clusters merged" = how many prefix-buckets this one family had been shattered into. High
merge counts = front-variable bots that were invisible before. Watch for real-user repeats
(e.g. the "write an extremely long story" saga) — do not promote those.</p>
{''.join(cards)}
"""
    os.makedirs(os.path.dirname(OUTFILE), exist_ok=True)
    open(OUTFILE, "w").write(doc)
    print(f"wrote {len(fams)} families -> {OUTFILE}")


if __name__ == "__main__":
    main()
