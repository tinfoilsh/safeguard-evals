#!/usr/bin/env python3
"""Bar chart of hard-no policy-judge fail rates per model.

Reads the per-model `data/judge_results_*.json` files (the specific-judge verdicts
over the 297 hard-no questions) and renders the fail rate per model, split into
three broad hard-no groups (Self-harm / Mass Violence & Terrorism / Child Abuse).

Error bars are Wilson 95% confidence intervals. A question tagged with several
hard-no categories counts toward every group it belongs to.

Usage:
  python plot.py [output.png]        # default: figures/hardno_fail_rates.png
"""

import json
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.offsetbox import AnnotationBbox, OffsetImage
from viewer import DATA, JUDGE_FILES

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE / "figures" / "hardno_fail_rates.png"
# Square transparent PNGs named <model>.png, copied from the marketing site's model icons.
LOGOS = HERE / "figures" / "logos"

# Display order for the models, matching the README.
MODELS = [
    "gemma4-31b",
    "gpt-oss",
    "llama3-3-70b",
    "kimi-k3",
    "deepseek-v4-flash",
    "deepseek-v4-1-flash",
    "glm-5-3",
    "glm-5-3-flash",
]

# Keep enlarged labels legible without widening the figure (which would make
# all text look smaller again when the chart is displayed at article width).
MODEL_LABELS = {
    "gemma4-31b": "gemma4-\n31b",
    "llama3-3-70b": "llama3-3-\n70b",
    "deepseek-v4-flash": "deepseek-\nv4-flash",
    "deepseek-v4-1-flash": "deepseek-\nv4-1-flash",
    "glm-5-3-flash": "glm-5-3-\nflash",
}

# Fine-grained hard-no categories (the `hardnos` field) -> broad display group.
GROUPS = {
    "Self-harm": ["self_harm"],
    "Mass Violence & Terrorism": ["cbrn", "mass_violence"],
    "Child Abuse": ["child_endangerment", "csam"],
}

Z_95 = 1.959964
GROUP_COLORS = ["#1f3f6e", "#3b6db3", "#7fa6d6"]
Y_MAX_PCT = 100.0
DPI = 200
TEXT_SCALE = 1.5
BASE_FONT_PT = 10
LOGO_PT = 22
# Vertical offset of the logo centre below the x-axis, in points; the model name
# sits beneath it, so the tick label pad must clear the logo.
LOGO_Y_OFFSET_PT = -18
XLABEL_PAD_PT = 32


def wilson(fails, n):
    """Return (rate, lower, upper) as percentages for a binomial proportion."""
    if n == 0:
        return 0.0, 0.0, 0.0
    p = fails / n
    denom = 1 + Z_95**2 / n
    center = (p + Z_95**2 / (2 * n)) / denom
    half = Z_95 * math.sqrt(p * (1 - p) / n + Z_95**2 / (4 * n * n)) / denom
    return 100 * p, 100 * max(0.0, center - half), 100 * min(1.0, center + half)


def load_results():
    results = {}
    for model in MODELS:
        path = DATA / JUDGE_FILES[model]
        if not path.exists():
            print(f"missing {path}, skipping {model}")
            continue
        results[model] = json.loads(path.read_text())
    return results


def overall_stats(rows):
    n = len(rows)
    fails = sum(1 for r in rows.values() if r["fail"])
    return wilson(fails, n)


def group_stats(rows, categories):
    members = [r for r in rows.values() if set(r["hardnos"]) & set(categories)]
    fails = sum(1 for r in members if r["fail"])
    return wilson(fails, len(members))


def errorbars(stats):
    rates = [s[0] for s in stats]
    lower = [s[0] - s[1] for s in stats]
    upper = [s[2] - s[0] for s in stats]
    return rates, [lower, upper]


def label_bars(ax, xs, stats):
    for xi, (rate, _, upper) in zip(xs, stats):
        ax.text(
            xi,
            upper,
            f"{rate:.1f}%",
            ha="center",
            va="bottom",
            fontsize=8 * TEXT_SCALE,
        )


def add_logos(ax, xs, models):
    for xi, model in zip(xs, models):
        path = LOGOS / f"{model}.png"
        if not path.exists():
            print(f"missing logo {path}")
            continue
        img = plt.imread(path)
        # OffsetImage is dpi-corrected, so zoom maps source pixels to points.
        zoom = LOGO_PT / max(img.shape[:2])
        box = AnnotationBbox(
            OffsetImage(img, zoom=zoom),
            (xi, 0),
            xybox=(0, LOGO_Y_OFFSET_PT),
            xycoords=("data", "axes fraction"),
            boxcoords="offset points",
            frameon=False,
            annotation_clip=False,
        )
        ax.add_artist(box)


def style_axis(ax, title):
    ax.set_title(title, fontsize=13 * TEXT_SCALE, fontweight="normal", pad=14)
    ax.set_ylabel("Fail rate %", fontsize=BASE_FONT_PT * TEXT_SCALE)
    ax.tick_params(axis="both", labelsize=BASE_FONT_PT * TEXT_SCALE)
    ax.set_ylim(0, Y_MAX_PCT)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.yaxis.grid(True, color="#dddddd")
    ax.set_axisbelow(True)


def plot(results, out_path):
    models = list(results)
    # width scales with the model count so x labels never collide
    fig, ax_b = plt.subplots(figsize=(max(9, 1.7 * len(models)), 5))

    n_groups = len(GROUPS)
    width = 0.8 / n_groups
    x = list(range(len(models)))
    for gi, (label, categories) in enumerate(GROUPS.items()):
        stats = [group_stats(results[m], categories) for m in models]
        rates, err = errorbars(stats)
        offsets = [xi + (gi - (n_groups - 1) / 2) * width for xi in x]
        ax_b.bar(
            offsets,
            rates,
            yerr=err,
            width=width,
            color=GROUP_COLORS[gi],
            capsize=3,
            label=label,
        )
        label_bars(ax_b, offsets, stats)
    ax_b.set_xticks(x)
    ax_b.set_xticklabels([MODEL_LABELS.get(model, model) for model in models])
    ax_b.tick_params(axis="x", length=0, pad=XLABEL_PAD_PT)
    add_logos(ax_b, x, models)
    style_axis(ax_b, "Hard-no fail rate by category")
    ax_b.legend(
        frameon=False,
        loc="upper left",
        fontsize=BASE_FONT_PT * TEXT_SCALE,
    )

    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=DPI)
    print(f"Wrote {out_path}")


def main():
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    results = load_results()
    if not results:
        print("no judge results found")
        sys.exit(1)
    for model, rows in results.items():
        rate, lo, hi = overall_stats(rows)
        print(f"{model:>20}: {rate:5.2f}%  [{lo:.2f}, {hi:.2f}]  n={len(rows)}")
    plot(results, out_path)


if __name__ == "__main__":
    main()
