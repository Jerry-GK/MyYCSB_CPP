#!/usr/bin/env python3
"""Run three controlled LORC/YCSB sweeps through the single-config runner.

This script is intentionally thin: every experiment point is executed by

    python3 experiments/run_lorc_single_config.py --config <generated.json> --no-build

The script only generates temporary JSON configs, changes one knob per sweep,
prints each finished system result, and redraws grouped bar charts for
throughput and p99 latency.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "experiments" / "configs" / "lorc_single_test_default.json"
DEFAULT_RUNNER = ROOT / "experiments" / "run_lorc_single_config.py"
DEFAULT_OUTPUT_ROOT = ROOT / "result" / "manual_three_sweeps"

SYSTEMS = ["rocksdb", "rocksdb_lorc", "blobdb", "blobdb_lorc", "lsbm"]
SYSTEM_LABELS = {
    "rocksdb": "RocksDB",
    "rocksdb_lorc": "RocksDB+LORC",
    "blobdb": "BlobDB",
    "blobdb_lorc": "BlobDB+LORC",
    "lsbm": "LSbM",
}
SYSTEM_COLORS = {
    "rocksdb": "#4C78A8",
    "rocksdb_lorc": "#F58518",
    "blobdb": "#54A24B",
    "blobdb_lorc": "#E45756",
    "lsbm": "#B279A2",
}


@dataclass(frozen=True)
class SweepPoint:
    suite: str
    knob: str
    value: Any
    label: str
    system: str
    rep: int

    @property
    def safe_name(self) -> str:
        def safe(x: Any) -> str:
            return str(x).replace("/", "_").replace(".", "p").replace("%", "pct")

        return (
            f"{safe(self.suite)}__{safe(self.value)}__"
            f"{safe(self.system)}__r{self.rep}"
        )


def parse_csv_list(raw: str, cast: type) -> list[Any]:
    values: list[Any] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        values.append(cast(item))
    if not values:
        raise ValueError(f"empty list: {raw!r}")
    return values


def value_size_label(value: int) -> str:
    if value >= 1024 and value % 1024 == 0:
        return f"{value // 1024}KB"
    return f"{value}B"


def load_base_config(path: Path) -> dict[str, Any]:
    cfg = json.loads(path.read_text())
    cfg["build_source"] = False
    return cfg


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    keys: list[str] = []
    preferred = [
        "suite",
        "knob",
        "x_value",
        "x_label",
        "system_key",
        "system",
        "rep",
        "returncode",
        "scan_throughput_ops_sec",
        "scan_avg_us",
        "scan_p99_us",
        "throughputops/sec",
        "max_rss_kb",
        "lorc_full_hit_rate",
        "lorc_hit_size_rate",
        "lorc_physical_range_count",
        "lorc_logical_range_count",
        "output_dir",
        "config_path",
        "error",
    ]
    for key in preferred:
        if any(key in row for row in rows):
            keys.append(key)
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def median_rows(rows: list[dict[str, Any]], suite: str) -> pd.DataFrame:
    df = pd.DataFrame([r for r in rows if r.get("suite") == suite])
    if df.empty:
        return df
    df = df[df["returncode"].fillna(1).astype(int) == 0].copy()
    if df.empty:
        return df
    numeric_cols = [
        "scan_throughput_ops_sec",
        "scan_p99_us",
        "scan_avg_us",
        "max_rss_kb",
        "lorc_full_hit_rate",
        "lorc_hit_size_rate",
        "lorc_physical_range_count",
        "lorc_logical_range_count",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    grouped = (
        df.groupby(["x_label", "x_order", "system_key"], as_index=False)[
            [c for c in numeric_cols if c in df.columns]
        ]
        .median(numeric_only=True)
        .sort_values(["x_order", "system_key"])
    )
    return grouped


def plot_sweep(
    rows: list[dict[str, Any]],
    *,
    suite: str,
    x_title: str,
    output_dir: Path,
    systems: list[str],
) -> None:
    df = median_rows(rows, suite)
    if df.empty:
        return
    order_df = df[["x_label", "x_order"]].drop_duplicates().sort_values("x_order")
    x_labels = order_df["x_label"].tolist()
    x = np.arange(len(x_labels))
    width = min(0.16, 0.78 / max(len(systems), 1))

    fig, axes = plt.subplots(1, 2, figsize=(11.6, 3.05))
    for ax, metric, y_label in [
        (axes[0], "scan_throughput_ops_sec", "Scan throughput (ops/s)"),
        (axes[1], "scan_p99_us", "p99 latency (us)"),
    ]:
        for i, system in enumerate(systems):
            vals: list[float] = []
            for label in x_labels:
                row = df[(df["x_label"] == label) & (df["system_key"] == system)]
                vals.append(float(row[metric].iloc[0]) if len(row) else math.nan)
            ax.bar(
                x + (i - (len(systems) - 1) / 2) * width,
                vals,
                width,
                label=SYSTEM_LABELS.get(system, system),
                color=SYSTEM_COLORS.get(system, "#999999"),
                edgecolor="black",
                linewidth=0.32,
            )
        ax.set_xticks(x)
        ax.set_xticklabels(x_labels)
        ax.set_xlabel(x_title)
        ax.set_ylabel(y_label)
        ax.grid(axis="y", color="#dddddd", linewidth=0.7)
        ax.set_axisbelow(True)

    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        ncol=min(len(systems), 5),
        frameon=False,
        loc="upper center",
        bbox_to_anchor=(0.5, 1.04),
        fontsize=8.5,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.94], pad=0.65)
    for ext in ("png", "pdf"):
        fig.savefig(output_dir / f"{suite}_throughput_p99.{ext}", dpi=220, bbox_inches="tight")
    plt.close(fig)


def generated_config(
    *,
    base: dict[str, Any],
    point: SweepPoint,
    point_dir: Path,
) -> dict[str, Any]:
    cfg = dict(base)
    cfg[point.knob] = point.value
    cfg["system"] = point.system
    cfg["output_dir"] = str(point_dir)
    cfg["build_source"] = False
    return cfg


def estimated_record_count(cfg: dict[str, Any]) -> int:
    total_bytes = int(float(cfg.get("total_data_gb", 4.0)) * 1024 * 1024 * 1024)
    key_size = int(cfg.get("key_size", 24))
    value_size = int(cfg.get("value_size", 1024))
    return max(1, total_bytes // (key_size + value_size))


def computed_warmup_ops(cfg: dict[str, Any]) -> int:
    recordcount = estimated_record_count(cfg)
    warmup_coverage = float(cfg.get("warmup_coverage", 8.0))
    scan_length = max(1, int(cfg.get("scan_length", 50)))
    return int(math.ceil(warmup_coverage * recordcount / scan_length))


def run_one_point(
    *,
    base: dict[str, Any],
    point: SweepPoint,
    runner: Path,
    run_root: Path,
    python: str,
) -> dict[str, Any]:
    point_dir = run_root / point.suite / point.safe_name
    config_dir = run_root / "configs" / point.suite
    point_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / f"{point.safe_name}.json"
    cfg = generated_config(base=base, point=point, point_dir=point_dir)
    config_path.write_text(json.dumps(cfg, indent=2) + "\n")

    cmd = [python, str(runner), "--config", str(config_path), "--no-build"]
    recordcount = estimated_record_count(cfg)
    warmup_ops = computed_warmup_ops(cfg)
    print(
        f"[run] suite={point.suite} {point.knob}={point.label} "
        f"system={SYSTEM_LABELS.get(point.system, point.system)} rep={point.rep}",
        flush=True,
    )
    print(
        f"      records={recordcount:,} warmup_ops={warmup_ops:,} "
        f"measured_ops={int(cfg.get('measured_operations', 0)):,}",
        flush=True,
    )
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    (point_dir / "driver.log").write_text(proc.stdout)

    row: dict[str, Any] = {
        "suite": point.suite,
        "knob": point.knob,
        "x_value": point.value,
        "x_label": point.label,
        "x_order": point.value if isinstance(point.value, (int, float)) else point.rep,
        "system_key": point.system,
        "system": SYSTEM_LABELS.get(point.system, point.system),
        "rep": point.rep,
        "returncode": proc.returncode,
        "output_dir": str(point_dir),
        "config_path": str(config_path),
    }
    summary_path = point_dir / "summary.json"
    if proc.returncode == 0 and summary_path.exists():
        summary = json.loads(summary_path.read_text())
        row.update(summary)
        row["system_key"] = point.system
        row["system"] = summary.get("system", SYSTEM_LABELS.get(point.system, point.system))
        print(
            "[done] "
            f"{SYSTEM_LABELS.get(point.system, point.system):14s} "
            f"{point.knob}={point.label:>6s} "
            f"thr={float(summary.get('scan_throughput_ops_sec', 0.0)):,.1f} ops/s "
            f"p99={float(summary.get('scan_p99_us', 0.0)):,.2f} us "
            f"rss={float(summary.get('max_rss_kb', 0.0)) / 1024 / 1024:.2f} GiB",
            flush=True,
        )
    else:
        row["error"] = proc.stdout[-3000:]
        print(
            "[fail] "
            f"{SYSTEM_LABELS.get(point.system, point.system):14s} "
            f"{point.knob}={point.label:>6s} rc={proc.returncode}; "
            f"log={point_dir / 'driver.log'}",
            flush=True,
        )
    return row


def run_sweep(
    *,
    base: dict[str, Any],
    suite: str,
    knob: str,
    values: Iterable[Any],
    labels: Iterable[str],
    x_title: str,
    runner: Path,
    run_root: Path,
    systems: list[str],
    reps: int,
    python: str,
    stop_on_failure: bool,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    suite_dir = run_root / suite
    suite_dir.mkdir(parents=True, exist_ok=True)
    for value, label in zip(values, labels):
        for system in systems:
            for rep in range(1, reps + 1):
                point = SweepPoint(
                    suite=suite,
                    knob=knob,
                    value=value,
                    label=str(label),
                    system=system,
                    rep=rep,
                )
                row = run_one_point(
                    base=base,
                    point=point,
                    runner=runner,
                    run_root=run_root,
                    python=python,
                )
                rows.append(row)
                write_csv(suite_dir / "summary.csv", rows)
                plot_sweep(
                    rows,
                    suite=suite,
                    x_title=x_title,
                    output_dir=suite_dir,
                    systems=systems,
                )
                if stop_on_failure and int(row.get("returncode", 0) or 0) != 0:
                    raise RuntimeError(f"point failed: {point}")
    print(f"[plot] {suite}: {suite_dir / (suite + '_throughput_p99.png')}", flush=True)
    return rows


def run_scan_length_sweep(args: argparse.Namespace, base: dict[str, Any], run_root: Path) -> list[dict[str, Any]]:
    values = parse_csv_list(args.scan_lengths, int)
    labels = [str(v) for v in values]
    return run_sweep(
        base=base,
        suite="scan_length",
        knob="scan_length",
        values=values,
        labels=labels,
        x_title="Scan length",
        runner=args.runner,
        run_root=run_root,
        systems=args.systems,
        reps=args.reps,
        python=args.python,
        stop_on_failure=args.stop_on_failure,
    )


def run_zipfian_sweep(args: argparse.Namespace, base: dict[str, Any], run_root: Path) -> list[dict[str, Any]]:
    values = parse_csv_list(args.zipfian_values, float)
    labels = [f"{v:g}" for v in values]
    return run_sweep(
        base=base,
        suite="zipfian",
        knob="zipfian_const",
        values=values,
        labels=labels,
        x_title="Zipfian constant",
        runner=args.runner,
        run_root=run_root,
        systems=args.systems,
        reps=args.reps,
        python=args.python,
        stop_on_failure=args.stop_on_failure,
    )


def run_value_size_sweep(args: argparse.Namespace, base: dict[str, Any], run_root: Path) -> list[dict[str, Any]]:
    values = parse_csv_list(args.value_sizes, int)
    labels = [value_size_label(v) for v in values]
    return run_sweep(
        base=base,
        suite="value_size",
        knob="value_size",
        values=values,
        labels=labels,
        x_title="Value size",
        runner=args.runner,
        run_root=run_root,
        systems=args.systems,
        reps=args.reps,
        python=args.python,
        stop_on_failure=args.stop_on_failure,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--runner", type=Path, default=DEFAULT_RUNNER)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-id", default=datetime.now().strftime("%Y%m%d_%H%M%S"))
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--systems", default=",".join(SYSTEMS))
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--scan-lengths", default="5,10,20,50,100")
    parser.add_argument("--zipfian-values", default="0,0.5,0.8,0.99,1.2,1.5")
    parser.add_argument("--value-sizes", default="256,512,1024,4096,8192")
    parser.add_argument("--stop-on-failure", action="store_true")
    args = parser.parse_args()
    args.systems = [s.strip() for s in args.systems.split(",") if s.strip()]
    unknown = [s for s in args.systems if s not in SYSTEMS]
    if unknown:
        raise SystemExit(f"unknown systems: {', '.join(unknown)}")
    if args.reps <= 0:
        raise SystemExit("--reps must be positive")
    return args


def main() -> int:
    args = parse_args()
    base = load_base_config(args.base_config)
    run_root = args.output_root / args.run_id
    run_root.mkdir(parents=True, exist_ok=True)
    print(f"[root] {run_root}", flush=True)
    print(f"[base] {args.base_config}", flush=True)
    print(f"[runner] {args.runner}", flush=True)
    print(f"[systems] {', '.join(args.systems)}", flush=True)
    print(f"[reps] {args.reps}", flush=True)

    all_rows: list[dict[str, Any]] = []
    for fn in (run_scan_length_sweep, run_zipfian_sweep, run_value_size_sweep):
        rows = fn(args, base, run_root)
        all_rows.extend(rows)
        write_csv(run_root / "summary.csv", all_rows)
    print(f"[summary] {run_root / 'summary.csv'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
