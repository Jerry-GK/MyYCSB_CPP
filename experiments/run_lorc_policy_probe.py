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
            "font.size": 8.5,
            "axes.labelsize": 8.5,
            "legend.fontsize": 8,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "figure.dpi": 180,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "axes.linewidth": 0.8,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )
    labels = ["Boundary-LRU", "Physical LRU", "Shortest-range"]
    policy_order = ["boundary_lru", "physical_lru", "shortest_range"]
    by_policy = {r["policy"]: r for r in rows}
    colors = ["#4E79A7", "#E15759", "#59A14F"]
    hatches = ["", "///", "..."]

    fig, axes = plt.subplots(
        1,
        3,
        figsize=(7.2, 2.15),
        gridspec_kw={"width_ratios": [1.12, 1.0, 1.0]},
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
    storage_work_per_k = [
        missed + chunk_records * (gaps / 100.0)
        for missed, gaps in zip(missed_per_scan, gaps_per_k)
    ]

    panels = [
        ("Scans touching\nstorage (%)", io_scan_pct, False),
        ("Storage gaps\nper 100 scans", gaps_per_k, False),
        ("Estimated storage work\nper scan", storage_work_per_k, False),
    ]
    for ax, (ylabel, values, higher_is_better) in zip(axes, panels):
        bars = ax.bar(
            range(len(policy_order)),
            values,
            color=colors,
            edgecolor="#222222",
            linewidth=0.45,
        )
        for bar, hatch in zip(bars, hatches):
            bar.set_hatch(hatch)
        for i, value in enumerate(values):
            ax.text(
                i,
                value + max(values + [1]) * 0.045,
                f"{value:.1f}",
                ha="center",
                va="bottom",
                fontsize=7.3,
            )
        ax.set_ylabel(ylabel)
        ax.set_xticks(range(len(policy_order)))
        ax.set_xticklabels(["Boundary", "Physical", "Shortest"], rotation=18, ha="right")
        ax.set_ylim(0, max(values + [1]) * 1.24)
        ax.grid(axis="y", color="#E7E7E7", linewidth=0.65)
        ax.set_axisbelow(True)

    axes[0].text(
        0.02,
        0.95,
        "main metric",
        transform=axes[0].transAxes,
        fontsize=7.3,
        fontweight="bold",
        color="#4E79A7",
        va="top",
    )
    axes[1].text(
        0.02,
        0.95,
        "gap cost",
        transform=axes[1].transAxes,
        fontsize=7.3,
        va="top",
        color="#555555",
    )
    axes[2].text(
        0.02,
        0.95,
        "combined",
        transform=axes[2].transAxes,
        fontsize=7.3,
        va="top",
        color="#555555",
    )

    fig.tight_layout(w_pad=0.75)
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
