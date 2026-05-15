#!/usr/bin/env python3
"""Build and run the synthetic LORC replacement-policy probe."""

from __future__ import annotations

import csv
import subprocess
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap


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
    old_bitmap = [[1 if c == "1" else 0 for c in by_policy[p]["old_chunk_bitmap"]] for p in policy_order]
    new_bitmap = [[1 if c == "1" else 0 for c in by_policy[p]["new_chunk_bitmap"]] for p in policy_order]
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.15), gridspec_kw={"width_ratios": [1.35, 1.0]})
    cmap = ListedColormap(["#F2F2F2", "#4E79A7"])

    axes[0].imshow(old_bitmap, aspect="auto", cmap=cmap, vmin=0, vmax=1)
    axes[0].axvspan(4.5, 6.5, facecolor="#F28E2B", alpha=0.22, linewidth=0)
    axes[0].set_title("Old range coverage")
    axes[0].set_yticks(range(len(labels)))
    axes[0].set_yticklabels(labels)
    axes[0].set_xticks([0, 5, 11])
    axes[0].set_xticklabels(["left", "hot", "right"])
    axes[0].tick_params(axis="both", length=0)
    for x in range(13):
        axes[0].axvline(x - 0.5, color="white", linewidth=0.5)
    for y in range(4):
        axes[0].axhline(y - 0.5, color="white", linewidth=0.5)

    axes[1].imshow(new_bitmap, aspect="auto", cmap=cmap, vmin=0, vmax=1)
    axes[1].set_title("New range coverage")
    axes[1].set_yticks(range(len(labels)))
    axes[1].set_yticklabels([])
    axes[1].set_xticks([0, 4, 8])
    axes[1].set_xticklabels(["start", "mid", "end"])
    axes[1].tick_params(axis="both", length=0)
    for x in range(10):
        axes[1].axvline(x - 0.5, color="white", linewidth=0.5)
    for y in range(4):
        axes[1].axhline(y - 0.5, color="white", linewidth=0.5)
    fig.tight_layout(w_pad=1.1)
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
