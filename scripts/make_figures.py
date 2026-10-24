#!/usr/bin/env python
"""Figures for the report, built only from saved evaluation output.

Reads reports/evaluation_results.json and writes PNGs to reports/figures/.
Recomputes nothing, so a figure can never disagree with the numbers in the
report.

Usage:
    python scripts/make_figures.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.config import load_config  # noqa: E402
from src.core.manifest import setup_logging  # noqa: E402

INK = "#24292f"
MUTED = "#57606a"
BAND_COLOUR = {"HIGH": "#1a7f37", "MEDIUM": "#bf8700", "LOW": "#cf222e"}


def style(ax):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.grid(axis="y", color="#d0d7de", linewidth=0.6, alpha=0.7)
    ax.set_axisbelow(True)


def fig_reliability(ev, out_dir, logger):
    targets = list(ev["targets"])
    fig, axes = plt.subplots(1, len(targets), figsize=(5.2 * len(targets), 4.4))
    if len(targets) == 1:
        axes = [axes]

    for ax, target in zip(axes, targets):
        rel = ev["targets"][target]["reliability"]
        pts = [(b["mean_predicted"], b["observed_frequency"], b["n"])
               for b in rel["bins"] if b["n"]]
        if not pts:
            continue
        xs, ys, ns = zip(*pts)

        ax.plot([0, 1], [0, 1], linestyle="--", color=MUTED, linewidth=1,
                label="perfect calibration")
        sizes = [20 + 200 * (n / max(ns)) for n in ns]
        ax.scatter(xs, ys, s=sizes, color="#0969da", alpha=0.75, zorder=3,
                   label="observed")
        ax.plot(xs, ys, color="#0969da", linewidth=1.2, alpha=0.6)

        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.set_xlabel("predicted probability of being wrong", fontsize=9)
        ax.set_ylabel("observed frequency", fontsize=9)
        ax.set_title("%s  (ECE %.3f)"
                     % (target.replace("_", " "),
                        rel["expected_calibration_error"]),
                     fontsize=11, color=INK)
        ax.legend(frameon=False, fontsize=8, loc="upper left")
        style(ax)

    fig.suptitle("Reliability after calibration — point size is bin count",
                 fontsize=12, color=INK)
    fig.tight_layout()
    path = out_dir / "reliability.png"
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    logger.info("wrote %s", path.name)


def fig_trust_bands(ev, out_dir, logger):
    targets = list(ev["targets"])
    fig, axes = plt.subplots(1, len(targets), figsize=(5.2 * len(targets), 4.4))
    if len(targets) == 1:
        axes = [axes]

    for ax, target in zip(axes, targets):
        bands = ev["targets"][target]["trust_bands"]
        names = ["HIGH", "MEDIUM", "LOW"]
        rates = [100 * bands[b]["error_rate"] if bands[b]["n"] else 0
                 for b in names]
        counts = [bands[b]["n"] for b in names]

        bars = ax.bar(names, rates, color=[BAND_COLOUR[b] for b in names],
                      width=0.6)
        for bar, rate, n in zip(bars, rates, counts):
            label = ("n=%s\n%.1f%%" % (format(n, ","), rate) if n
                     else "empty band")
            ax.text(bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + max(rates) * 0.03 + 0.4,
                    label, ha="center", fontsize=8.5, color=INK)

        ax.set_ylabel("observed error rate (%)", fontsize=9)
        ax.set_title(target.replace("_", " "), fontsize=11, color=INK)
        ax.set_ylim(0, max(max(rates) * 1.3, 5))
        style(ax)

    fig.suptitle("Error rate within each trust band", fontsize=12, color=INK)
    fig.tight_layout()
    path = out_dir / "trust_bands.png"
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    logger.info("wrote %s", path.name)


def fig_skill(ev, out_dir, logger):
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    labels, values, colours = [], [], []
    for target, d in ev["targets"].items():
        for variant in ("reference", "uncalibrated", "calibrated"):
            m = d["metrics"].get(variant)
            if not m:
                continue
            labels.append("%s\n%s" % (target.replace("_", " "), variant))
            v = m["brier_skill_vs_reference"]
            values.append(v)
            colours.append("#1a7f37" if v > 0 else
                           ("#57606a" if v == 0 else "#cf222e"))

    bars = ax.bar(range(len(values)), values, color=colours, width=0.62)
    ax.axhline(0, color=MUTED, linewidth=1)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylabel("Brier skill score vs climatology", fontsize=9)

    # One bar (the uncalibrated miss model) is far off the scale. Clip the
    # axis so the others stay readable, then place every label *inside* the
    # visible range - a label drawn outside it makes bbox_inches="tight"
    # expand the whole figure into empty space.
    low = min(values)
    floor = -1.2 if low < -1 else min(low * 1.4, -0.1)
    top = max(max(values) * 1.6, 0.5)
    ax.set_ylim(floor, top)

    for bar, v in zip(bars, values):
        if v < floor:                      # clipped bar: label just above the floor
            y, va = floor * 0.82, "bottom"
            text = "%+.2f (off scale)" % v
        elif v >= 0:
            y, va = v + top * 0.04, "bottom"
            text = "%+.3f" % v
        else:
            y, va = v - top * 0.04, "top"
            text = "%+.3f" % v
        ax.text(bar.get_x() + bar.get_width() / 2, y, text,
                ha="center", va=va, fontsize=8.5, color=INK)

    if low < floor:
        ax.text(0.30, 0.06,
                "calibration moves the miss model from %+.2f to %+.3f"
                % (low, ev["targets"]["miss"]["metrics"]["calibrated"]
                   ["brier_skill_vs_reference"]),
                transform=ax.transAxes, ha="center", fontsize=8, color=MUTED,
                style="italic")

    ax.set_title("Positive is better than climatology; negative is worse",
                 fontsize=11, color=INK)
    style(ax)
    fig.tight_layout()
    path = out_dir / "brier_skill.png"
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    logger.info("wrote %s", path.name)


def fig_verification(ev, out_dir, logger):
    verif = ev.get("forecast_verification_test")
    if not verif:
        return
    levels = sorted(verif, key=float)
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    for metric, colour, label in (
            ("probability_of_detection", "#1a7f37", "POD (detection)"),
            ("false_alarm_ratio", "#cf222e", "FAR (false alarms)"),
            ("critical_success_index", "#0969da", "CSI (overall)")):
        ax.plot([float(x) * 100 for x in levels],
                [verif[x][metric] for x in levels],
                marker="o", color=colour, label=label, linewidth=1.6)

    ax.set_xlabel("signal level: % of members over threshold", fontsize=9)
    ax.set_ylabel("score", fontsize=9)
    ax.set_ylim(0, 1)
    ax.legend(frameon=False, fontsize=9)
    ax.set_title("Raising the signal level trades detection for precision",
                 fontsize=11, color=INK)
    style(ax)
    fig.tight_layout()
    path = out_dir / "verification_by_signal_level.png"
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    logger.info("wrote %s", path.name)


def main() -> int:
    cfg = load_config()
    logger = setup_logging("make_figures")

    results = Path(cfg.get_path("figures")).parent / "evaluation_results.json"
    if not results.exists():
        logger.error("no %s - run scripts/run_evaluation.py first", results)
        return 1

    ev = json.loads(results.read_text(encoding="utf-8"))
    out_dir = cfg.get_path("figures")
    logger.info("figures from split train=%s calibrate=%s test=%s",
                ev["split"]["train"], ev["split"]["calibration"],
                ev["split"]["test"])

    fig_reliability(ev, out_dir, logger)
    fig_trust_bands(ev, out_dir, logger)
    fig_skill(ev, out_dir, logger)
    fig_verification(ev, out_dir, logger)
    logger.info("done - %d figures in %s",
                len(list(out_dir.glob("*.png"))), out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
