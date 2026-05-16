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
        ("Entry ordered", "entry_ordered_ns", "#8C6D31", "o"),
        ("Vector segment", "vec_segment_ns", "#4F7EA8", "s"),
        ("Continuous segment", "continuous_segment_ns", "#3D8B5B", "^"),
    ]
    fig, axes = plt.subplots(1, 2, figsize=(7.35, 2.35), constrained_layout=True)
    for label, key, color, marker in series:
        values = [float(r[key]) / 1000.0 for r in rows]
        axes[0].plot(lengths, values, marker=marker, linewidth=1.7,
                     markersize=4.2, color=color, label=label)
    axes[0].set_xscale("log")
    axes[0].set_xticks(lengths)
    axes[0].get_xaxis().set_major_formatter(lambda x, _pos: f"{int(x)}")
    axes[0].set_xlabel("Scan length")
    axes[0].set_ylabel("Cached-hit traversal (us/scan)")
    axes[0].grid(axis="y", color="#E7E7E7", linewidth=0.65)
    axes[0].legend(frameon=False, loc="upper left")

    speedup_entry = [float(r["continuous_vs_entry_speedup"]) for r in rows]
    speedup_vec = [float(r["continuous_vs_vec_speedup"]) for r in rows]
    width = 0.34
    xs = list(range(len(lengths)))
    axes[1].bar(
        [x - width / 2 for x in xs],
        speedup_entry,
        width,
        color="#8C6D31",
        edgecolor="#222222",
        linewidth=0.45,
        label="vs. entry ordered",
    )
    axes[1].bar(
        [x + width / 2 for x in xs],
        speedup_vec,
        width,
        color="#4F7EA8",
        edgecolor="#222222",
        linewidth=0.45,
        label="vs. vector segment",
    )
    axes[1].axhline(1.0, color="#444444", linewidth=0.8)
    axes[1].set_xticks(xs)
    axes[1].set_xticklabels([str(x) for x in lengths])
    axes[1].set_xlabel("Scan length")
    axes[1].set_ylabel("Continuous-segment speedup")
    axes[1].grid(axis="y", color="#E7E7E7", linewidth=0.65)
    axes[1].legend(frameon=False, loc="upper left")
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
