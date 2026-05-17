#!/usr/bin/env python3
"""Run the in-memory range-cache representation surrogate probe."""

from __future__ import annotations

import csv
import subprocess
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
PAPER = ROOT.parent / "68368b2dccae04737d71ce11"
SRC = ROOT / "experiments" / "range_cache_surrogate_probe.cc"
BIN = ROOT / "experiments" / "range_cache_surrogate_probe"
SUMMARY = PAPER / "figures" / "experiments" / "lorc_representation_probe_summary.csv"
FIGURE = PAPER / "figures" / "experiments" / "eval_representation_probe.pdf"


def build() -> None:
    subprocess.run(
        ["g++", "-std=c++17", "-O3", "-march=native", str(SRC), "-o", str(BIN)],
        cwd=ROOT,
        check=True,
    )


def run() -> list[dict[str, str]]:
    text = subprocess.check_output([str(BIN)], cwd=ROOT, text=True)
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY.write_text(text)
    return list(csv.DictReader(text.splitlines()))


def plot(rows: list[dict[str, str]]) -> None:
    plt.rcParams.update(
        {
            "font.size": 8.4,
            "axes.labelsize": 8.4,
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
    lengths = [int(r["scan_length"]) for r in rows]
    series = [
        ("Entry skip list", "entry_l1d_load_hit_rate", "#8C6D31", "o"),
        ("Continuous segment", "continuous_l1d_load_hit_rate", "#3D8B5B", "^"),
    ]
    fig, ax = plt.subplots(1, 1, figsize=(3.6, 2.35), constrained_layout=True)
    for label, key, color, marker in series:
        values = [float(r[key]) * 100.0 for r in rows]
        ax.plot(
            lengths,
            values,
            marker=marker,
            linewidth=1.8,
            markersize=4.5,
            color=color,
            label=label,
        )
    ax.set_xscale("log")
    ax.set_xticks(lengths)
    ax.get_xaxis().set_major_formatter(lambda x, _pos: f"{int(x)}")
    ax.set_xlabel("Scan length")
    ax.set_ylabel("L1D load hit rate (%)")
    ax.grid(axis="y", color="#E7E7E7", linewidth=0.65)
    ax.legend(frameon=False, loc="lower right")
    ymin = min(
        min(
            float(r["entry_l1d_load_hit_rate"]),
            float(r["continuous_l1d_load_hit_rate"]),
        )
        for r in rows
    ) * 100.0
    ax.set_ylim(max(0, ymin - 5), 100)
    FIGURE.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURE, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    build()
    rows = run()
    plot(rows)
    print(f"summary={SUMMARY}")
    print(f"figure={FIGURE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
