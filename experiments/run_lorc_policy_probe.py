#!/usr/bin/env python3
"""Build and run the synthetic LORC replacement-policy probe."""

from __future__ import annotations

import csv
import subprocess
from pathlib import Path

import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
PAPER = ROOT.parent / "68368b2dccae04737d71ce11"
SRC = ROOT / "experiments" / "lorc_policy_probe.cc"
BIN = ROOT / "experiments" / "lorc_policy_probe"
OUT = PAPER / "figures" / "experiments" / "lorc_policy_probe_summary.csv"
FIG = PAPER / "figures" / "experiments" / "eval_lru_policy_probe.pdf"


def build() -> None:
    flags = subprocess.check_output(
        [
            "bash",
            "-lc",
            "PKG_CONFIG_PATH=/home/gjr/mylibs/lorcdb_release/lib/pkgconfig "
            "pkg-config --cflags --libs rocksdb",
        ],
        text=True,
    ).strip().split()
    cmd = [
        "g++",
        "-std=c++17",
        "-O2",
        str(SRC),
        "-o",
        str(BIN),
        "-Wl,-rpath,/home/gjr/mylibs/lorcdb_release/lib",
        *flags,
    ]
    subprocess.run(cmd, cwd=ROOT, check=True)


def run() -> list[dict[str, str]]:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    text = subprocess.check_output([str(BIN)], cwd=ROOT, text=True)
    OUT.write_text(text)
    return list(csv.DictReader(text.splitlines()))


def plot(rows: list[dict[str, str]]) -> None:
    plt.rcParams.update(
        {
            "font.size": 8.2,
            "axes.labelsize": 8.2,
            "legend.fontsize": 8,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "figure.dpi": 180,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "axes.linewidth": 0.8,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.spines.left": False,
        }
    )
    labels = ["Boundary-LRU", "Physical LRU", "Shortest-range"]
    policy_order = ["boundary_lru", "physical_lru", "shortest_range"]
    by_policy = {r["policy"]: r for r in rows}
    colors = ["#3B6EA8", "#C84E4E", "#5D9A55"]
    hatches = ["", "///", "..."]

    fig, axes = plt.subplots(
        1,
        3,
        figsize=(7.35, 2.05),
        gridspec_kw={"width_ratios": [1.2, 1.05, 1.05]},
    )

    io_scan_pct = [
        100.0
        * int(by_policy[p]["future_io_scan_count"])
        / int(by_policy[p]["future_query_count"])
        for p in policy_order
    ]
    gaps_per_k = [
        100.0 * int(by_policy[p]["future_gap_parts"]) / int(by_policy[p]["future_query_count"])
        for p in policy_order
    ]
    missed_per_scan = [
        (
            int(by_policy[p]["future_query_records"])
            - int(by_policy[p]["future_hit_records"])
        )
        / int(by_policy[p]["future_query_count"])
        for p in policy_order
    ]
    chunk_records = 128
    storage_work_per_scan = [
        missed + chunk_records * (gaps / 100.0)
        for missed, gaps in zip(missed_per_scan, gaps_per_k)
    ]
    relative_work = [v / storage_work_per_scan[0] for v in storage_work_per_scan]

    panels = [
        ("Scans touching storage\n(lower is better)", io_scan_pct, "%"),
        ("Storage-gap opens", gaps_per_k, "/100 scans"),
        ("Storage-work proxy", relative_work, "x Boundary"),
    ]
    for ax, (title, values, unit) in zip(axes, panels):
        y_pos = list(reversed(range(len(policy_order))))
        bars = ax.barh(
            y_pos,
            values,
            color=colors,
            edgecolor="#222222",
            linewidth=0.45,
            height=0.56,
        )
        for bar, hatch in zip(bars, hatches):
            bar.set_hatch(hatch)
        xmax = max(values + [1])
        for i, value in enumerate(values):
            if unit == "%":
                label = f"{value:.1f}%"
            elif unit == "/100 scans":
                label = f"{value:.1f}"
            else:
                label = f"{value:.2f}x"
            ax.text(
                value + xmax * 0.035,
                y_pos[i],
                label,
                ha="left",
                va="center",
                fontsize=7.2,
            )
        ax.set_title(title, fontsize=8.4, pad=4)
        ax.set_yticks(y_pos)
        if ax is axes[0]:
            ax.set_yticklabels(labels)
        else:
            ax.set_yticklabels([])
        ax.set_xlim(0, xmax * 1.32)
        ax.grid(axis="x", color="#E8E8E8", linewidth=0.65)
        ax.set_axisbelow(True)
        ax.tick_params(axis="y", length=0)
        ax.tick_params(axis="x", length=2, pad=1)
        ax.set_xlabel(unit, labelpad=2)

    fig.tight_layout(w_pad=0.55)
    fig.savefig(FIG)
    plt.close(fig)


def main() -> int:
    build()
    rows = run()
    plot(rows)
    print(f"Summary: {OUT}")
    print(f"Figure: {FIG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
