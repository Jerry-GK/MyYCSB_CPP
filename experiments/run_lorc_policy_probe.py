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
            "font.size": 9,
            "axes.labelsize": 9,
            "legend.fontsize": 8,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "figure.dpi": 180,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )
    labels = ["Boundary-LRU", "Physical LRU", "Shortest-range"]
    policy_order = ["boundary_lru", "physical_lru", "shortest_range"]
    by_policy = {r["policy"]: r for r in rows}
    hot_hit = [
        100.0 * float(by_policy[p]["hot_hit_records"]) / 256.0 for p in policy_order
    ]
    new_hit = [
        100.0 * float(by_policy[p]["new_hit_records"]) / 640.0 for p in policy_order
    ]
    crossing_parts = [float(by_policy[p]["crossing_cached_parts"]) for p in policy_order]
    gaps = [float(by_policy[p]["crossing_gap_parts"]) for p in policy_order]

    fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.45))
    colors = ["#F28E2B", "#4E79A7", "#8C6D31"]
    width = 0.36
    xs = range(len(labels))
    axes[0].bar(
        [x - width / 2 for x in xs],
        hot_hit,
        width=width,
        label="Old hot island",
        color="#F28E2B",
        edgecolor="#303030",
        linewidth=0.5,
    )
    axes[0].bar(
        [x + width / 2 for x in xs],
        new_hit,
        width=width,
        label="New hot range",
        color="#4E79A7",
        edgecolor="#303030",
        linewidth=0.5,
    )
    axes[0].set_xticks(list(xs))
    axes[0].set_xticklabels(labels)
    axes[0].set_ylabel("Hit rate (%)")
    axes[0].set_ylim(0, 105)
    axes[0].legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, 1.26), ncol=2)
    axes[0].grid(axis="y", color="#dddddd", linewidth=0.6)

    axes[1].bar(
        [x - width / 2 for x in xs],
        crossing_parts,
        width=width,
        label="Cached pieces",
        color="#59A14F",
        edgecolor="#303030",
        linewidth=0.5,
    )
    axes[1].bar(
        [x + width / 2 for x in xs],
        gaps,
        width=width,
        label="Gaps",
        color="#E15759",
        edgecolor="#303030",
        linewidth=0.5,
    )
    axes[1].set_xticks(list(xs))
    axes[1].set_xticklabels(labels)
    axes[1].set_ylabel("Crossing-scan pieces")
    axes[1].legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, 1.26), ncol=2)
    axes[1].grid(axis="y", color="#dddddd", linewidth=0.6)
    for ax in axes:
        ax.set_axisbelow(True)
        ax.tick_params(axis="x", rotation=16)
    fig.tight_layout(w_pad=1.5)
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
